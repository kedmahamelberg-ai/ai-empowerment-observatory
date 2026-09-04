#!/usr/bin/env python3
"""Regression checks for language-neutral full-body collection and coding.

This intentionally uses only the standard library plus local policy helpers so
it can run in the lightweight repository-integrity job.
"""

from __future__ import annotations

import json
from pathlib import Path

from brief_content_common import MIN_FULL_BODY_EVIDENCE_UNITS, evidence_unit_count
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


def test_collection_and_routing_contract() -> None:
    collector = (ROOT / "scripts" / "brief_backfill_article_content.py").read_text(encoding="utf-8")
    resolver = (ROOT / "scripts" / "translate_headlines.py").read_text(encoding="utf-8")
    dual_lens = (ROOT / "scripts" / "classify_dual_lens.py").read_text(encoding="utf-8")
    symbiosis = (ROOT / "scripts" / "classify_symbiosis.py").read_text(encoding="utf-8")
    event_resolver = (ROOT / "scripts" / "resolve_events.py").read_text(encoding="utf-8")
    release_builder = (ROOT / "scripts" / "build_weekly_release.py").read_text(encoding="utf-8")

    require("Accept-Language" not in collector, "body collector still asks publishers for an English variant")
    require("decode_article_html" in collector, "body collector lacks multilingual charset decoding")
    require("qwen_primary_items" in resolver, "translation router does not process non-English languages")
    require(
        'lang not in {"en", "fr", "zh"}' not in resolver,
        "translation router still rejects every language outside English, French and Chinese",
    )
    require(
        "routed through multilingual normalization" in resolver,
        "low-confidence language detection can still silently assume English",
    )
    require(
        "lingua_english_conflicts_with_discovery_language" in resolver,
        "a French, Chinese or bilingual discovery-language conflict can still pass through as English",
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
    test_collection_and_routing_contract()
    test_market_configuration()
    print("Multilingual full-body pipeline regression checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
