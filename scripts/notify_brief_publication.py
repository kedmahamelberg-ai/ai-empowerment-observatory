#!/usr/bin/env python3
"""Notify the Brief only after the exact Observatory export is publicly live."""
import argparse
import json
import os
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

EXPORT_URL = "https://observatory.hamelberg-ai.com/data/brief/current.json"
DISPATCH_URL = ("https://api.github.com/repos/kedmahamelberg-ai/aieo-brief/"
                "actions/workflows/follow-observatory.yml/dispatches")


def identity(document):
    release_id = document.get("release_id", "")
    digest = document.get("public_content_sha256", "")
    if not re.fullmatch(r"\d{4}-W\d{2}", release_id) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Missing or invalid release identity in the public Brief export")
    return release_id, digest


def record_artifact(path):
    release_id, digest = identity(json.loads(Path(path).read_text()))
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"release_id={release_id}\nexport_sha256={digest}\n")


def wait_for_live(expected, attempts=20, delay=15, opener=urlopen, sleep=time.sleep):
    # A cache-busting query avoids acknowledging a previous Pages deployment.
    for attempt in range(attempts):
        request = Request(f"{EXPORT_URL}?handoff={expected[1]}-{attempt}",
                          headers={"Cache-Control": "no-cache", "User-Agent": "Observatory-Brief-Handoff"})
        try:
            with opener(request, timeout=15) as response:
                if identity(json.load(response)) == expected:
                    print(f"Verified live Observatory export: {expected[0]}")
                    return
        except (HTTPError, URLError, TimeoutError, ValueError):
            pass
        if attempt + 1 < attempts:
            sleep(delay)
    raise RuntimeError("The deployed export did not match the Pages artifact. Brief was not triggered; scheduled checks remain active.")


def dispatch(token, opener=urlopen, sleep=time.sleep):
    if not token:
        raise ValueError("The dedicated Brief handoff token is missing")
    request = Request(DISPATCH_URL, data=json.dumps({"ref": "main"}).encode(), method="POST",
                      headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                               "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28"})
    # A lost response can cause a duplicate dispatch. The receiver serializes
    # updates and compares the two live editions before any editorial work.
    for attempt in range(3):
        try:
            with opener(request, timeout=30) as response:
                if response.status not in (200, 201, 204):
                    raise RuntimeError(f"Unexpected GitHub dispatch status: {response.status}")
            print("GitHub accepted Follow Observatory Editions on aieo-brief/main.")
            summary = os.environ.get("GITHUB_STEP_SUMMARY")
            if summary:
                with open(summary, "a") as output:
                    output.write("## Observatory → Brief\n\nThe exact export is live. GitHub accepted the Brief update request. "
                                 "[Follow its progress](https://github.com/kedmahamelberg-ai/aieo-brief/actions/workflows/follow-observatory.yml). "
                                 "Acceptance does not mean the Brief has finished publishing.\n")
            return
        except HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError(f"GitHub rejected the Brief trigger (HTTP {error.code}); check the App installation and Actions permission.") from None
        except (URLError, TimeoutError):
            if attempt == 2:
                raise RuntimeError("Could not reach GitHub to trigger the Brief") from None
        sleep(10 * (attempt + 1))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--record-artifact")
    mode.add_argument("--verify-live", action="store_true")
    mode.add_argument("--dispatch", action="store_true")
    args = parser.parse_args()
    if args.record_artifact:
        record_artifact(args.record_artifact)
    elif args.verify_live:
        expected = identity({"release_id": os.environ.get("EXPECTED_RELEASE_ID", ""),
                             "public_content_sha256": os.environ.get("EXPECTED_EXPORT_SHA256", "")})
        wait_for_live(expected)
    else:
        dispatch(os.environ.get("BRIEF_HANDOFF_TOKEN", ""))


if __name__ == "__main__":
    main()
