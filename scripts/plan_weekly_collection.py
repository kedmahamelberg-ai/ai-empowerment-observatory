#!/usr/bin/env python3
"""Pin the completed week once, before any weekly pipeline job starts."""
import argparse
import json
import os
from datetime import date
from pathlib import Path
from release_common import previous_complete_week, iso_week_id

ROOT = Path(__file__).resolve().parents[1]


def plan(as_of=None):
    config = json.loads((ROOT / 'config/edu_countries.json').read_text())
    countries = config['countries']
    if {c['iso3'] for c in countries} != {'USA', 'CHN', 'GBR', 'FRA', 'CAN'}:
        raise ValueError('All five discovery markets must be configured.')
    if not {'en', 'fr'}.issubset({c['hl'] for c in countries if c['iso3'] == 'CAN'}):
        raise ValueError('Canada must retain English and French searches.')
    if not config['meta'].get('require_all_markets'):
        raise ValueError('A failed discovery market must block the weekly pipeline.')
    period = previous_complete_week(as_of)
    return {'release_id': iso_week_id(period), 'period_start': str(period.start),
            'period_end': str(period.end), 'markets': 5, 'searches': len(countries)}


def needs_collection(target_release_id, current_release_id, force=False):
    return force or target_release_id != current_release_id


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--as-of', type=date.fromisoformat)
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    result = plan(args.as_of)
    current_path = ROOT / 'data/releases/current.json'
    current_id = json.loads(current_path.read_text()).get('release_id', '') if current_path.exists() else ''
    result['needs_collection'] = needs_collection(result['release_id'], current_id, args.force)
    print(json.dumps(result, indent=2))
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            for key in ('release_id', 'period_start', 'period_end'):
                output.write(f'{key}={result[key]}\n')
            output.write(f"needs_collection={str(result['needs_collection']).lower()}\n")
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as output:
            output.write(f"## Weekly collection\n\n**{result['release_id']}**, {result['period_start']} to {result['period_end']}. "
                         'Five markets, six language searches. The public release uses publication dates within this completed week.\n')
            if not result['needs_collection']:
                output.write('The completed week is already current; duplicate collection was skipped.\n')


if __name__ == '__main__':
    main()
