#!/usr/bin/env python3
"""Regression checks for language-neutral full-body collection and coding.

This intentionally uses only the standard library plus local policy helpers so
it can run in the lightweight repository-integrity job.
"""

from __future__ import annotations

import json
from pathlib import Path

from brief_content_common import MIN_FULL_BODY_EVIDENCE_UNITS, evidence_unit_count
from language_routing import (
    ROUTE_CHINESE_AUDITED,
    ROUTE_ENGLISH_PASSTHROUGH,
    ROUTE_MULTILINGUAL,
    resolve_source_language,
    translation_route,
)
from translation_policy import (
    CURRENT_TRANSLATION_PROFILE,
    preferred_translation_rows,
)

ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"MULTILINGUAL PIPELINE ERROR: {message}")


def test_evidence_counter() -> None:
    examples = {
        "English": "People gain practical access and control through a documented AI service. " * 14,
        "French": "Les personnes gagnent un accès concret et un meilleur contrôle grâce à un service d’intelligence artificielle documenté. " * 12,
        "Chinese": "人工智能服务为研究人员提供了可验证的新工具和更好的访问机会。" * 8,
        "Japanese": "人工知能のサービスは研究者に検証可能な新しい道具とより良いアクセスを提供している。" * 7,
        "Korean": "인공지능 서비스는 연구자에게 검증 가능한 새로운 도구와 더 나은 접근성을 제공한다. " * 8,
        "Thai": "บริการปัญญาประดิษฐ์มอบเครื่องมือใหม่และการเข้าถึงที่ดีขึ้นแก่ผู้วิจัยอย่างตรวจสอบได้" * 7,
        "Arabic": "تمنح خدمة الذكاء الاصطناعي الباحثين أدوات جديدة ووصولا أفضل يمكن التحقق منه. " * 14,
        "Canadian bilingual": "Les personnes gagnent un accès concret. People gain practical access through the documented service. " * 12,
    }
    for language, text in examples.items():
        require(
            evidence_unit_count(text) >= MIN_FULL_BODY_EVIDENCE_UNITS,
            f"{language} full-body evidence was rejected by the minimum-body gate",
        )

    chinese_without_spaces = "人工智能正在帮助研究人员分析公开数据并获得新的研究工具" * 8
    require(" " not in chinese_without_spaces, "Chinese regression fixture must have no spaces")
    require(
        evidence_unit_count(chinese_without_spaces) >= MIN_FULL_BODY_EVIDENCE_UNITS,
        "space-free Chinese body was treated as empty or too short",
    )


def test_translation_profile_precedence() -> None:
    chosen = preferred_translation_rows(
        [
            {
                "article_id": "same-article",
                "translation_profile": "validated_language_routing_v3",
                "translated_headline": "legacy value",
                "created_at": "2099-01-01T00:00:00Z",
            },
            {
                "article_id": "same-article",
                "translation_profile": CURRENT_TRANSLATION_PROFILE,
                "translated_headline": "multilingual value",
                "created_at": "2026-09-04T00:00:00Z",
            },
        ]
    )
    require(
        chosen["same-article"]["translated_headline"] == "multilingual value",
        "legacy translation can override the current multilingual policy",
    )


def test_language_routing_contract() -> None:
    """Prove non-English sources cannot fall into an English-only exclusion path."""

    french = resolve_source_language(
        headline="La France accélère sur l’intelligence artificielle",
        detected_language="fr",
        confidence=0.98,
        observed_search_languages=["fr"],
    )
    require(french[0] == "fr", "French source was not retained as French")
    require(
        translation_route(french[0]) == ROUTE_MULTILINGUAL,
        "French source was not routed through multilingual normalization",
    )

    chinese = resolve_source_language(
        headline="人工智能服务为研究人员提供新的工具",
        detected_language="en",
        confidence=0.97,
        observed_search_languages=["zh-cn"],
    )
    require(chinese[0] == "zh", "Han-script source was not routed as Chinese")
    require(
        translation_route(chinese[0]) == ROUTE_CHINESE_AUDITED,
        "Chinese source was not assigned its primary-plus-audit route",
    )

    french_in_canadian_english_search = resolve_source_language(
        headline="Les services d’IA changent le travail au Canada",
        detected_language="fr",
        confidence=0.93,
        observed_search_languages=["en"],
    )
    require(
        french_in_canadian_english_search[0] == "fr",
        "French reporting found via an English Canadian search was overwritten as English",
    )
    require(
        translation_route(french_in_canadian_english_search[0]) == ROUTE_MULTILINGUAL,
        "French Canadian reporting was not sent to multilingual normalization",
    )

    bilingual_canadian = resolve_source_language(
        headline="Canada announces new AI rules — règles pour l’intelligence artificielle",
        detected_language="en",
        confidence=0.88,
        observed_search_languages=["en"],
        language_candidates=[("en", 0.88), ("fr", 0.18)],
    )
    require(
        bilingual_canadian[0] == "un" and bilingual_canadian[3],
        "Bilingual Canadian source was treated as English-only",
    )
    require(
        translation_route(bilingual_canadian[0]) == ROUTE_MULTILINGUAL,
        "Bilingual Canadian source was not sent to multilingual normalization",
    )

    uncertain_non_english = resolve_source_language(
        headline="خبر جديد عن الذكاء الاصطناعي",
        detected_language="en",
        confidence=0.52,
        observed_search_languages=["en"],
    )
    require(
        uncertain_non_english[0] == "un" and uncertain_non_english[3],
        "Uncertain non-English source was treated as reliable English",
    )
    require(
        translation_route(uncertain_non_english[0]) == ROUTE_MULTILINGUAL,
        "Uncertain source was excluded instead of sent to multilingual normalization",
    )

    reliable_english = resolve_source_language(
        headline="Researchers gain access to a new AI tool",
        detected_language="en",
        confidence=0.99,
        observed_search_languages=["en"],
        language_candidates=[("en", 0.99), ("de", 0.002)],
    )
    require(
        translation_route(reliable_english[0]) == ROUTE_ENGLISH_PASSTHROUGH,
        "Reliable English should be the only passthrough route",
    )


def test_collection_and_routing_contract() -> None:
    collector = (ROOT / "scripts" / "brief_backfill_article_content.py").read_text(encoding="utf-8")
    resolver = (ROOT / "scripts" / "translate_headlines.py").read_text(encoding="utf-8")
    dual_lens = (ROOT / "scripts" / "classify_dual_lens.py").read_text(encoding="utf-8")
    symbiosis = (ROOT / "scripts" / "classify_symbiosis.py").read_text(encoding="utf-8")
    event_resolver = (ROOT / "scripts" / "resolve_events.py").read_text(encoding="utf-8")
    release_builder = (ROOT / "scripts" / "build_weekly_release.py").read_text(encoding="utf-8")

    require("Accept-Language" not in collector, "body collector still asks publishers for an English variant")
    require("decode_article_html" in collector, "body collector lacks multilingual charset decoding")
    require("translation_route" in resolver, "translation router does not use the tested route policy")
    require("ROUTE_MULTILINGUAL" in resolver, "translation router lacks the multilingual route")
    require(
        "resolve_source_language" in resolver,
        "translation router lacks the source-language conflict safeguard",
    )
    for name, script in (("dual-lens classifier", dual_lens), ("relationship classifier", symbiosis)):
        require(
            "may be written in any language" in script,
            f"{name} lacks original-language full-body guidance",
        )
        require(
            'or "en"' not in script,
            f"{name} still labels an unknown source language as English",
        )
    require('or "und"' in event_resolver, "event resolution still assumes unknown sources are English")
    require('"und")' in release_builder, "weekly release still labels an unknown source language as English")


def test_market_configuration() -> None:
    config = json.loads((ROOT / "config" / "edu_countries.json").read_text(encoding="utf-8"))
    countries = config.get("countries") or []
    canada_languages = {
        str(row.get("hl") or "").lower()
        for row in countries
        if str(row.get("iso3") or "") == "CAN"
    }
    require(canada_languages == {"en", "fr"}, "Canada must have separate English and French discovery passes")
    require(
        any(str(row.get("iso3") or "") == "FRA" and str(row.get("hl") or "").startswith("fr") for row in countries),
        "France must retain a French discovery pass",
    )
    require(
        any(str(row.get("iso3") or "") == "CHN" and str(row.get("hl") or "").startswith("zh") for row in countries),
        "China must retain a Chinese discovery pass",
    )


def main() -> int:
    test_evidence_counter()
    test_translation_profile_precedence()
    test_language_routing_contract()
    test_collection_and_routing_contract()
    test_market_configuration()
    print("Multilingual full-body pipeline regression checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
