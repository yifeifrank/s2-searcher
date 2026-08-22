#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import research_tools  # noqa: E402


class FirecrawlTests(unittest.TestCase):
    def test_recommended_pair_is_the_code_default(self) -> None:
        self.assertEqual(research_tools.DEFAULT_SEARCH_BACKEND, "firecrawl")
        self.assertEqual(research_tools.DEFAULT_RETRIEVER_BACKEND, "exa")

    def test_v2_search_response_is_normalized(self) -> None:
        response = {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "A study",
                        "url": "https://example.org/study",
                        "description": "A useful result",
                    }
                ]
            },
        }
        with patch.dict(os.environ, {"FIRECRAWL_API_KEY": "test-key"}), patch.object(
            research_tools, "http_json", return_value=response
        ) as request:
            results = research_tools.search_firecrawl("research question", 3)

        self.assertEqual(results[0]["url"], "https://example.org/study")
        self.assertEqual(request.call_args.args[0], "https://api.firecrawl.dev/v2/search")
        self.assertEqual(request.call_args.kwargs["payload"]["sources"], ["web"])

    def test_legacy_search_response_remains_accepted(self) -> None:
        response = {
            "data": [
                {
                    "title": "Legacy result",
                    "url": "https://example.org/legacy",
                    "description": "Still readable",
                }
            ]
        }
        with patch.dict(os.environ, {"FIRECRAWL_API_KEY": "test-key"}), patch.object(
            research_tools, "http_json", return_value=response
        ):
            results = research_tools.search_firecrawl("research question", 1)
        self.assertEqual(results[0]["title"], "Legacy result")


class ExaTests(unittest.TestCase):
    def test_contents_response_becomes_citeable_text(self) -> None:
        response = {
            "results": [
                {
                    "id": "https://example.org/study",
                    "url": "https://example.org/study",
                    "title": "A study",
                    "author": "Researcher",
                    "publishedDate": "2026-01-01",
                    "text": "Full retrieved text.",
                }
            ],
            "statuses": [{"id": "https://example.org/study", "status": "success"}],
        }
        with patch.object(research_tools, "http_json", return_value=response) as request:
            canonical, markdown = research_tools._retrieve_exa_once(
                "https://example.org/study", 30, "test-key"
            )

        self.assertEqual(canonical, "https://example.org/study")
        self.assertIn("# A study", markdown)
        self.assertIn("Full retrieved text.", markdown)
        self.assertEqual(request.call_args.args[0], "https://api.exa.ai/contents")
        self.assertEqual(request.call_args.kwargs["payload"]["urls"], ["https://example.org/study"])
        self.assertTrue(request.call_args.kwargs["payload"]["text"])


if __name__ == "__main__":
    unittest.main()
