"""Regression coverage for non-article Google News destinations."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from collect_news import is_article_url, normalize_item


class ArticleUrlGateTests(unittest.TestCase):
    COUNTRY = {
        "country": "United States",
        "iso2": "US",
        "iso3": "USA",
        "hl": "en",
        "query": "artificial intelligence",
    }

    def test_rejects_abc_alert_feed(self):
        url = "https://abcnews.com/alerts/ArtificialIntelligence"
        self.assertFalse(is_article_url(url))
        self.assertIsNone(
            normalize_item(
                {"title": "Artificial Intelligence", "link": url},
                self.COUNTRY,
                "2026-10-05T05:37:20Z",
                3,
            )
        )

    def test_keeps_specific_article_url(self):
        url = "https://www.example.com/news/ai-policy-improves-safety"
        self.assertTrue(is_article_url(url))
        self.assertIsNotNone(
            normalize_item(
                {"title": "Specific AI policy story", "link": url},
                self.COUNTRY,
                "2026-10-05T05:37:20Z",
                1,
            )
        )

    def test_rejects_other_common_collection_routes(self):
        for component in ("topics", "category", "search", "tag"):
            with self.subTest(component=component):
                self.assertFalse(is_article_url(f"https://example.com/{component}/ai"))


if __name__ == "__main__":
    unittest.main()
