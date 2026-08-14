#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

import requests

API = "https://api.github.com"
REPO = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GITHUB_TOKEN"]
REF = os.environ.get("PIPELINE_REF", "main")

WORKFLOWS = [
    "Translate AI News to English",
    "Resolve AI News Into Events",
    "Classify Coverage and Event Lenses",
    "Finalize Stage 7C Residual Rule",
    "Publish Observatory Release",
]

TIMEOUT_MINUTES = {
    "Translate AI News to English": 180,
    "Resolve AI News Into Events": 240,
    "Classify Coverage and Event Lenses": 360,
    "Finalize Stage 7C Residual Rule": 60,
    "Publish Observatory Release": 60,
}

HEADERS = {
    "Accept": "application/vnd.github+json",
    "Authorization": f"Bearer {TOKEN}",
    "X-GitHub-Api-Version": "2022-11-28",
}


class OrchestrationError(RuntimeError):
    pass


def api(method: str, path: str, **kwargs) -> requests.Response:
    response = requests.request(
        method,
        f"{API}/repos/{REPO}{path}",
        headers=HEADERS,
        timeout=60,
        **kwargs,
    )

    if not response.ok:
        raise OrchestrationError(
            f"GitHub API {method} {path} failed "
            f"({response.status_code}): {response.text[:1000]}"
        )

    return response


def workflow_map() -> dict[str, dict[str, Any]]:
    response = api(
        "GET",
        "/actions/workflows?per_page=100",
    )
    data = response.json()

    return {
        str(item["name"]): item
        for item in data.get("workflows", [])
    }


def dispatch(workflow: dict[str, Any]) -> int:
    response = api(
        "POST",
        f"/actions/workflows/{workflow['id']}/dispatches",
        json={"ref": REF},
    )

    # Current GitHub may return a run identifier; older behavior returned 204.
    if response.content:
        try:
            data = response.json()
            if data.get("workflow_run_id"):
                return int(data["workflow_run_id"])
        except ValueError:
            pass

    created_after = datetime.now(timezone.utc).timestamp() - 5

    for _ in range(30):
        runs = api(
            "GET",
            (
                f"/actions/workflows/{workflow['id']}/runs"
                f"?event=workflow_dispatch&branch={REF}&per_page=10"
            ),
        ).json().get("workflow_runs", [])

        for run in runs:
            created = datetime.fromisoformat(
                run["created_at"].replace("Z", "+00:00")
            ).timestamp()

            if created >= created_after:
                return int(run["id"])

        time.sleep(2)

    raise OrchestrationError(
        f"Could not identify dispatched run for {workflow['name']}."
    )


def wait_for_run(
    workflow_name: str,
    run_id: int,
) -> None:
    deadline = time.time() + TIMEOUT_MINUTES[workflow_name] * 60

    while time.time() < deadline:
        run = api(
            "GET",
            f"/actions/runs/{run_id}",
        ).json()

        status = run.get("status")
        conclusion = run.get("conclusion")

        print(
            f"{workflow_name}: status={status} "
            f"conclusion={conclusion} "
            f"url={run.get('html_url')}",
            flush=True,
        )

        if status == "completed":
            if conclusion != "success":
                raise OrchestrationError(
                    f"{workflow_name} concluded {conclusion}. "
                    f"See {run.get('html_url')}"
                )
            return

        time.sleep(20)

    raise OrchestrationError(
        f"{workflow_name} exceeded its orchestration timeout."
    )


def main() -> int:
    workflows = workflow_map()

    missing = [
        name
        for name in WORKFLOWS
        if name not in workflows
    ]

    if missing:
        raise OrchestrationError(
            "Required workflow names were not found: "
            + ", ".join(missing)
        )

    for name in WORKFLOWS:
        print(f"\nDispatching: {name}", flush=True)
        run_id = dispatch(workflows[name])
        wait_for_run(name, run_id)

    print("\nWeekly Observatory pipeline completed successfully.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OrchestrationError as exc:
        print(f"Pipeline orchestration failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
