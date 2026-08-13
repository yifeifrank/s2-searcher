#!/usr/bin/env python3
"""Parse and compose the combined Markdown instruction/codebook contract."""

from __future__ import annotations

import json
import re
from typing import Any


CODEBOOK_HEADING = re.compile(
    r"(?im)^(?P<marks>#{1,6})[ \t]+Codebook[ \t]*#*[ \t]*$"
)
JSON_FENCE = re.compile(
    r"(?ims)^```[ \t]*(?:json(?:[ \t]+codebook)?|codebook)[ \t]*\r?\n"
    r"(?P<body>.*?)^```[ \t]*$"
)


def codebook_section_span(markdown: str) -> tuple[int, int]:
    headings = list(CODEBOOK_HEADING.finditer(markdown))
    if len(headings) != 1:
        raise ValueError(
            "instruction.md must contain exactly one Markdown heading named 'Codebook'"
        )
    heading = headings[0]
    level = len(heading.group("marks"))
    next_heading = re.compile(rf"(?m)^#{{1,{level}}}[ \t]+.+$").search(
        markdown, heading.end()
    )
    return heading.start(), next_heading.start() if next_heading else len(markdown)


def extract_codebook(markdown: str) -> dict[str, Any]:
    start, end = codebook_section_span(markdown)
    section = markdown[start:end]
    fences = list(JSON_FENCE.finditer(section))
    if not fences:
        raise ValueError(
            "the Codebook section must contain at least one fenced JSON object"
        )
    combined: dict[str, Any] = {}
    for position, fence in enumerate(fences, 1):
        try:
            value = json.loads(fence.group("body"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"invalid JSON in Codebook chunk {position}: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise ValueError(f"Codebook chunk {position} must be a JSON object")
        duplicates = sorted(set(combined).intersection(value))
        if duplicates:
            raise ValueError(
                "duplicate top-level codebook keys across JSON chunks: "
                + ", ".join(duplicates)
            )
        combined.update(value)
    return combined


def instruction_prose(markdown: str) -> str:
    """Return the Markdown contract with its complete Codebook section removed."""
    start, end = codebook_section_span(markdown)
    return (markdown[:start].rstrip() + "\n\n" + markdown[end:].lstrip()).strip()


def compose_instruction(prose: str, codebook: dict[str, Any]) -> str:
    if CODEBOOK_HEADING.search(prose):
        raise ValueError(
            "separate instruction prose already contains a Codebook heading; "
            "use --instruction for a combined contract"
        )
    prefix = prose.rstrip()
    rendered = json.dumps(codebook, indent=2, ensure_ascii=False)
    return (
        f"{prefix}\n\n## Codebook\n\n"
        "The following fenced JSON object is the machine-extracted output contract. "
        "Additional fenced JSON objects may split large codebooks; top-level keys "
        "must remain unique across chunks.\n\n"
        f"```json\n{rendered}\n```\n"
    )
