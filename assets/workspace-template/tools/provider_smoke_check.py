#!/usr/bin/env python3
"""Check the recommended Firecrawl-search and Exa-retrieval setup."""
from __future__ import annotations

import argparse
import hashlib
import json
import os

import research_tools


DEFAULT_QUERY = "social science reproducibility site:arxiv.org"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Make one search and one retrieval request")
    parser.add_argument("--query", default=DEFAULT_QUERY, help="Small Firecrawl test query")
    parser.add_argument("--url", default="", help="Retrieve this URL instead of the first search result")
    parser.add_argument("--num-results", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=30)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.num_results < 1:
        raise SystemExit("--num-results must be at least 1")
    if args.timeout < 1:
        raise SystemExit("--timeout must be at least 1 second")

    required = ("FIRECRAWL_API_KEY", "EXA_API_KEY")
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        raise SystemExit(
            "Missing " + ", ".join(missing) + ". Copy .env.example to .env and fill those values."
        )

    if not args.live:
        print(json.dumps({"status": "ready", "live_request": False, "providers": ["firecrawl", "exa"]}))
        return 0

    results = research_tools.search_firecrawl(args.query, args.num_results)
    if not results:
        raise SystemExit("Firecrawl authenticated successfully but returned no test results")
    selected_url = args.url.strip() or results[0]["url"]
    if not selected_url:
        raise SystemExit("Firecrawl returned a result without a URL")

    canonical_url, text = research_tools.retrieve_exa(selected_url, args.timeout)
    if not text.strip():
        raise SystemExit("Exa authenticated successfully but returned no page text")

    print(
        json.dumps(
            {
                "status": "success",
                "firecrawl_search": {
                    "query": args.query,
                    "result_count": len(results),
                    "selected_url": selected_url,
                },
                "exa_retrieval": {
                    "canonical_url": canonical_url,
                    "character_count": len(text),
                    "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
