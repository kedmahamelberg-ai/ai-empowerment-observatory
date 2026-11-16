#!/usr/bin/env python3
"""Recover a missed weekly publication without repeating collection."""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

from release_common import iso_week_id, previous_complete_week

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = {'queued', 'in_progress', 'waiting', 'requested', 'pending', 'action_required'}


def decision(current_id: str, target_id: str, published_id: str,
             weekly_runs: list[dict], recovery_runs: list[dict],
             publication_runs: list[dict]) -> str:
    if published_id == target_id:
        return 'already_published'
    if any(run.get('status') in ACTIVE for run in weekly_runs + recovery_runs + publication_runs):
        return 'release_work_active'
    if current_id == target_id:
        return 'dispatch_saved_work_recovery'
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


def deployed_release_id() -> str:
    base = os.environ.get('OBSERVATORY_BASE_URL', 'https://observatory.hamelberg-ai.com').rstrip('/')
    request = Request(base + '/data/brief/current.json', headers={'Cache-Control': 'no-cache'})
    with urlopen(request, timeout=20) as response:
        export = json.load(response)
    if export.get('schema_version') != 'aieo_brief_export_v1':
        raise ValueError('Unexpected deployed Brief export schema')
    return export['release_id']


def main() -> int:
    target_id = iso_week_id(previous_complete_week())
    current_path = ROOT / 'data/releases/current.json'
    current_id = json.loads(current_path.read_text()).get('release_id', '') if current_path.exists() else ''
    published_id = deployed_release_id()
    token = os.environ['GITHUB_TOKEN']
    repository = os.environ['GITHUB_REPOSITORY']
    base = f'https://api.github.com/repos/{repository}/actions/workflows/'
    def runs(workflow):
        return github_json(base + workflow + '/runs?per_page=30', token).get('workflow_runs', [])
    weekly_runs = runs('weekly-observatory.yml')
    recovery_runs = runs('resume-full-body-relationship-results.yml')
    publication_runs = runs('publish-observatory-release.yml')
    result = decision(current_id, target_id, published_id, weekly_runs, recovery_runs, publication_runs)
    if result == 'dispatch_missing_week':
        github_json(base + 'weekly-observatory.yml/dispatches', token, payload={'ref': 'main'})
    elif result == 'dispatch_saved_work_recovery':
        github_json(base + 'resume-full-body-relationship-results.yml/dispatches', token,
                    payload={'ref': 'main'})
    print(json.dumps({'target_release': target_id, 'built_release': current_id,
                      'deployed_release': published_id, 'decision': result}, sort_keys=True))
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a') as output:
            output.write(f'## Weekly publication watchdog\n\n{target_id}: {result.replace("_", " ")}. '
                         f'Built: {current_id}; deployed: {published_id}.\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
