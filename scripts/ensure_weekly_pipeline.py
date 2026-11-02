#!/usr/bin/env python3
"""Dispatch a missed Monday collection without duplicating an active weekly run."""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

from release_common import iso_week_id, previous_complete_week

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = {'queued', 'in_progress', 'waiting', 'requested', 'pending', 'action_required'}


def decision(current_id: str, target_id: str, runs: list[dict]) -> str:
    if current_id == target_id:
        return 'already_published'
    if any(run.get('status') in ACTIVE for run in runs):
        return 'weekly_run_active'
    return 'dispatch_missing_week'


def github_json(url: str, token: str, *, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(url, data=body, method='POST' if body is not None else 'GET', headers={
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {token}',
        'X-GitHub-Api-Version': '2026-03-10',
        **({'Content-Type': 'application/json'} if body is not None else {}),
    })
    with urlopen(request, timeout=20) as response:
        raw = response.read()
    return json.loads(raw) if raw else {}


def main() -> int:
    target_id = iso_week_id(previous_complete_week())
    current_path = ROOT / 'data/releases/current.json'
    current_id = json.loads(current_path.read_text()).get('release_id', '') if current_path.exists() else ''
    token = os.environ['GITHUB_TOKEN']
    repository = os.environ['GITHUB_REPOSITORY']
    base = f'https://api.github.com/repos/{repository}/actions/workflows/weekly-observatory.yml'
    runs = github_json(base + '/runs?per_page=30', token).get('workflow_runs', [])
    result = decision(current_id, target_id, runs)
    if result == 'dispatch_missing_week':
        github_json(base + '/dispatches', token, payload={'ref': 'main'})
    print(json.dumps({'target_release': target_id, 'published_release': current_id,
                      'decision': result}, sort_keys=True))
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a') as output:
            output.write(f'## Weekly collection watchdog\n\n{target_id}: {result.replace("_", " ")}.\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
