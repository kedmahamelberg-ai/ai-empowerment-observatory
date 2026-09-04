#!/usr/bin/env python3
"""Retired one-off presentation patch.

The former script rewrote a few frontend files in place and could overwrite
newer public presentation work. Relationship presentation is now part of the
complete repository and is protected by `validate_repository_integrity.py` and
`validate_public_site_artifact.py`. Keep this file only so an old manual link
fails safely instead of applying an obsolete overlay.
"""

from __future__ import annotations


def main() -> int:
    raise SystemExit(
        "This one-off patch is retired. Use the complete repository handoff and "
        "run python scripts/validate_repository_integrity.py instead."
    )


if __name__ == "__main__":
    raise SystemExit(main())
