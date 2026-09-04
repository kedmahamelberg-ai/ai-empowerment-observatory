"""Pure language-routing policy used by the Observatory translation stage.

This module deliberately has no model, database, or network dependencies so
the repository can test the inclusion policy without running a translation job.
Discovery language is useful context, but it is never treated as proof that a
publisher wrote an English source.  Only a reliable English detection may take
the English passthrough; every other detected or uncertain language is routed
through multilingual normalization.
"""

from __future__ import annotations

from collections.abc import Iterable


ROUTE_ENGLISH_PASSTHROUGH = "english_passthrough"
ROUTE_CHINESE_AUDITED = "chinese_primary_with_independent_audit"
ROUTE_MULTILINGUAL = "multilingual_normalization"
ENGLISH_PASSTHROUGH_CONFIDENCE = 0.85
COMPETING_LANGUAGE_CONFIDENCE = 0.15


def contains_han(value: object) -> bool:
    """Return whether text contains a CJK Unified Ideograph.

    Han script is decisive for routing because short Chinese headlines can be
    difficult for a generic language detector and often contain no spaces.
    """

    return any(
        "\u3400" <= character <= "\u4dbf" or "\u4e00" <= character <= "\u9fff"
        for character in str(value or "")
    )


def normalize_search_language(value: object) -> str:
    """Normalize a discovery-interface language without narrowing inclusion."""

    language = str(value or "").strip().casefold()
    if not language:
        return ""
    if language.startswith("zh"):
        return "zh"
    if language.startswith("fr"):
        return "fr"
    if language.startswith("en"):
        return "en"
    return language.split("-", 1)[0]


def resolve_source_language(
    *,
    headline: object,
    detected_language: object,
    confidence: float,
    observed_search_languages: Iterable[object],
    language_candidates: Iterable[tuple[object, float]] = (),
) -> tuple[str, float, str, bool, str]:
    """Choose a source-language route without ever defaulting uncertainty to English.

    Returns ``source_language, confidence, method, requires_review, reason``.
    ``un`` means "unknown language routed through multilingual normalization",
    not an unsupported or excluded source.
    """

    detected = str(detected_language or "und").strip().casefold()
    probability = max(0.0, min(1.0, float(confidence or 0.0)))
    hints = {
        normalized
        for value in observed_search_languages
        if (normalized := normalize_search_language(value))
    }
    hint = next(iter(hints)) if len(hints) == 1 else None
    competing_languages = {
        normalize_search_language(language)
        for language, candidate_confidence in language_candidates
        if normalize_search_language(language) not in {"", "en"}
        and float(candidate_confidence or 0.0) >= COMPETING_LANGUAGE_CONFIDENCE
    }

    if contains_han(headline):
        return "zh", probability, "han_script+lingua", False, ""

    if detected == "en":
        if probability < ENGLISH_PASSTHROUGH_CONFIDENCE:
            return (
                "un",
                probability,
                "lingua_english_below_passthrough_threshold",
                True,
                "English detection did not meet the reliable passthrough threshold; routed through multilingual normalization.",
            )
        if (hint and hint != "en") or any(language != "en" for language in hints):
            return (
                "un",
                probability,
                "lingua_english_conflicts_with_discovery_language",
                True,
                "English detection conflicts with the discovery language; routed through multilingual normalization rather than assumed English.",
            )
        if competing_languages:
            return (
                "un",
                probability,
                "lingua_english_with_competing_language",
                True,
                "The headline has a material non-English language signal; routed through multilingual normalization rather than assumed English-only.",
            )
        return "en", probability, "lingua", False, ""

    if probability >= 0.65:
        method = "lingua+search_language" if hint and detected == hint else "lingua"
        return detected, probability, method, False, ""

    language = detected if len(detected) == 2 and detected != "en" else "un"
    return (
        language,
        probability,
        "lingua_low_confidence",
        True,
        "Uncertain source language; routed through multilingual normalization rather than assumed English.",
    )


def translation_route(source_language: object) -> str:
    """Return the only permitted translation path for a normalized language."""

    language = str(source_language or "un").strip().casefold()
    if language == "en":
        return ROUTE_ENGLISH_PASSTHROUGH
    if language == "zh":
        return ROUTE_CHINESE_AUDITED
    return ROUTE_MULTILINGUAL
