#!/usr/bin/env python3
"""Deterministic router for the three supported research execution modes."""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any


ROUTER_VERSION = "research-router-v5"
ROUTES = {"direct_api", "local_agent", "online_agent"}


def load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Cannot read {label} at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return value


def _risk_score(difficulty: dict[str, Any]) -> tuple[int, list[str]]:
    score = 0
    factors: list[str] = []
    weighted = {
        "output_complexity": {"compound": 1},
        "corpus_size": {"large": 1},
        "noise": {"high": 1},
        "identity_ambiguity": {"high": 2, "medium": 1},
        "conflict_risk": {"high": 2, "medium": 1},
    }
    for key, values in weighted.items():
        value = difficulty.get(key, "unknown")
        points = values.get(value, 0)
        if points:
            score += points
            factors.append(f"{key}={value} (+{points})")
    if bool(difficulty.get("multilingual")):
        score += 1
        factors.append("multilingual=true (+1)")
    return score, factors


def _difficulty_profile(difficulty: dict[str, Any], document_count: int) -> tuple[str, int, list[str]]:
    score, factors = _risk_score(difficulty)
    if document_count > 20:
        score += 1
        factors.append("document_count>20 (+1)")
    if score >= 6:
        return "extreme", score, factors
    if score >= 3:
        return "intensive", score, factors
    if score == 0 and difficulty.get("output_complexity") == "simple":
        return "light", score, factors
    return "standard", score, factors


def _route(contract: dict[str, Any], default_route: str) -> tuple[str, bool, list[str]]:
    routing = contract.get("routing", {})
    requested = routing.get("requested_route", "auto")
    if requested in ROUTES:
        return requested, True, [f"Task contract explicitly requested {requested}."]
    if default_route in ROUTES:
        return default_route, True, [f"Project default explicitly selected {default_route}."]

    task_input = contract.get("input", {})
    difficulty = contract.get("difficulty", {})
    documents = [str(item) for item in task_input.get("documents", []) if str(item).strip()]
    scope = task_input.get("document_scope", "auto")
    external = task_input.get("requires_external_search")

    if external is True or scope == "open_web":
        why = "external search is required" if external is True else "document scope is open_web"
        return "online_agent", False, [f"Selected online_agent because {why}."]
    if not documents and external is not False:
        return "online_agent", False, [
            "Selected online_agent because no local documents were supplied and external search was not prohibited."
        ]

    local_signals: list[str] = []
    if scope == "fixed_collection":
        local_signals.append("the input is a fixed collection")
    if len(documents) > 1:
        local_signals.append("multiple documents require iterative inspection")
    if difficulty.get("corpus_size") == "large":
        local_signals.append("the corpus is large")
    if difficulty.get("noise") == "high":
        local_signals.append("the corpus is noisy")
    if difficulty.get("output_complexity") == "compound" and scope not in {"inline", "single_document"}:
        local_signals.append("compound output is dispersed across local evidence")
    if local_signals:
        return "local_agent", False, ["Selected local_agent because " + "; ".join(local_signals) + "."]

    return "direct_api", False, [
        "Selected direct_api because the supplied evidence is compact, local, and self-contained."
    ]


def route_contract(contract: dict[str, Any], framework: dict[str, Any] | None = None) -> dict[str, Any]:
    framework = framework or {}
    default_route = str(framework.get("default_route", "auto"))
    selected_route, explicit_override, reasons = _route(contract, default_route)
    task_input = contract.get("input", {})
    routing = contract.get("routing", {})
    difficulty = contract.get("difficulty", {})
    documents = [str(item) for item in task_input.get("documents", []) if str(item).strip()]
    profile, risk_score, risk_factors = _difficulty_profile(difficulty, len(documents))

    workflow_prompt: str | None
    if selected_route == "local_agent":
        workflow_prompt = "prompts/workflows/local_agent.md"
    elif selected_route == "online_agent":
        workflow_prompt = "prompts/workflows/online_agent.md"
        reasons.append("The standalone session owns the complete online research workflow.")
    else:
        workflow_prompt = None

    requested_budgets = deepcopy(contract.get("budgets", {}))
    defaults = framework.get("defaults", {})
    recommended_budgets = {
        "max_search_calls": int(requested_budgets.get("max_search_calls", defaults.get("max_search_calls", 40))),
        "max_runtime_seconds": int(
            requested_budgets.get("max_runtime_seconds", defaults.get("session_timeout_seconds", 3600))
        ),
        "max_retries": int(requested_budgets.get("max_retries", defaults.get("api_max_retries", 2))),
    }
    if selected_route != "online_agent":
        recommended_budgets["max_search_calls"] = 0

    configured_permission = str(
        framework.get("permissions", {}).get("codex_sandbox_mode", "danger-full-access")
    )
    permission_mode = "none" if selected_route == "direct_api" else configured_permission
    requested_coder_mode = routing.get("coder_mode")
    if requested_coder_mode is None and routing.get("separate_coder") is True:
        requested_coder_mode = "api"
        reasons.append("Mapped legacy separate_coder=true to coder_mode=api.")
    coder_mode = str(requested_coder_mode or framework.get("coder_mode_default", "none"))
    if coder_mode not in {"none", "api"}:
        raise ValueError(f"Unsupported coder_mode: {coder_mode}")
    if selected_route == "direct_api" and coder_mode != "none":
        reasons.append("Ignored post-research coder because direct_api already performs coding.")
        coder_mode = "none"
    elif selected_route == "direct_api":
        reasons.append("No post-research coder is needed because direct_api performs the coding call itself.")
    elif coder_mode == "api":
        reasons.append("One optional post-freeze API coding call was requested.")
    else:
        reasons.append("No post-research coder is enabled; research artifacts are the terminal deliverable.")

    return {
        "router_version": ROUTER_VERSION,
        "task_id": str(contract.get("task_id", "")),
        "selected_route": selected_route,
        "workflow_prompt": workflow_prompt,
        "difficulty_profile": profile,
        "coder_mode": coder_mode,
        "permission_mode": permission_mode,
        "factors": {
            "document_scope": task_input.get("document_scope", "auto"),
            "document_count": len(documents),
            "requires_external_search": task_input.get("requires_external_search"),
            "risk_score": risk_score,
            "risk_factors": risk_factors,
            **difficulty,
        },
        "reasons": reasons,
        "explicit_override": explicit_override,
        "recommended_budgets": recommended_budgets,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--framework", default="framework.json")
    parser.add_argument("--output", help="Defaults to route_decision.json beside the contract")
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args()

    contract_path = Path(args.contract).expanduser().resolve()
    framework_path = Path(args.framework).expanduser().resolve()
    contract = load_object(contract_path, "task contract")
    framework = load_object(framework_path, "framework configuration") if framework_path.exists() else {}
    decision = route_contract(contract, framework)
    rendered = json.dumps(decision, indent=2, ensure_ascii=False) + "\n"
    if args.stdout:
        print(rendered, end="")
    else:
        output = Path(args.output).expanduser().resolve() if args.output else contract_path.with_name("route_decision.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        print(output)


if __name__ == "__main__":
    main()
