"""Shared translation-profile policy for multilingual Observatory sources."""

from __future__ import annotations

CURRENT_TRANSLATION_PROFILE = "validated_language_routing_v4_multilingual"
LEGACY_TRANSLATION_PROFILES = ("validated_language_routing_v3",)
SUPPORTED_TRANSLATION_PROFILES = (
    CURRENT_TRANSLATION_PROFILE,
    *LEGACY_TRANSLATION_PROFILES,
)


def profile_priority(value: object) -> int:
    """Prefer the current multilingual translation over a legacy fallback."""
    profile = str(value or "")
    try:
        return len(SUPPORTED_TRANSLATION_PROFILES) - SUPPORTED_TRANSLATION_PROFILES.index(profile)
    except ValueError:
        return 0


def preferred_translation_rows(rows: list[dict]) -> dict[str, dict]:
    """Choose one translation per article, preferring v4 even if v3 is newer."""
    ordered = sorted(
        rows,
        key=lambda row: (
            profile_priority(row.get("translation_profile")),
            str(row.get("created_at") or ""),
        ),
        reverse=True,
    )
    chosen: dict[str, dict] = {}
    for row in ordered:
        article_id = str(row.get("article_id") or "")
        if article_id and article_id not in chosen:
            chosen[article_id] = row
    return chosen
