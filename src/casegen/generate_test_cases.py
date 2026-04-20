#!/usr/bin/env python3
"""
Generate discrete test cases by free-combining variable candidate values.

Usage:
    python src/casegen/generate_test_cases.py --config config/casegen/targets.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
from itertools import product
from pathlib import Path
from typing import Any, Dict, List

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from interface_parser.configuration import build_output_path, load_config
from interface_parser.parse_interface import parse_targets


def _normalize_scope(value: object, default: str = "selected") -> str:
    scope = str(value).strip().lower()
    if scope in {"all", "selected"}:
        return scope
    return default


def _normalize_output_mode(value: object, default: str = "full") -> str:
    mode = str(value).strip().lower()
    if mode in {"full", "simple"}:
        return mode
    return default


def _parse_case_count(value: object, default: str = "all") -> str | int:
    if value is None:
        return default
    text = str(value).strip().lower()
    if not text:
        return default
    if text == "all":
        return "all"
    try:
        num = int(text, 10)
    except ValueError as exc:
        raise ValueError(f"Invalid case_count: {value}. Use positive integer or 'all'.") from exc
    if num <= 0:
        raise ValueError(f"Invalid case_count: {value}. Must be > 0 or 'all'.")
    return num


def _safe_int(value: object, default: int) -> int:
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return default


def _get_case_generation_config(config: dict) -> dict:
    cfg = config.get("case_generation", {})
    if not isinstance(cfg, dict):
        cfg = {}
    return {
        "dir": cfg.get("dir", "output/testcases"),
        "filename_format": cfg.get("filename_format", "testcases_{interface}_{datetime}.json"),
        "variable_scope": _normalize_scope(cfg.get("variable_scope", "selected"), "selected"),
        "mode": _normalize_output_mode(cfg.get("mode", "full"), "full"),
        "case_count": _parse_case_count(cfg.get("case_count", "all"), "all"),
        "random_seed": _safe_int(cfg.get("random_seed", 42), 42),
    }


def _decode_combination_index(index: int, radixes: List[int]) -> List[int]:
    digits_reversed: List[int] = []
    cur = index
    for radix in reversed(radixes):
        digits_reversed.append(cur % radix)
        cur //= radix
    return list(reversed(digits_reversed))


def _sample_unique_indices(total_count: int, sample_count: int, rng: random.Random) -> List[int]:
    if sample_count >= total_count:
        return list(range(total_count))

    if total_count <= 500_000:
        return sorted(rng.sample(range(total_count), sample_count))

    seen = set()
    while len(seen) < sample_count:
        seen.add(rng.randrange(total_count))
    return sorted(seen)


def _build_interface_cases(
    interface_item: dict,
    scope: str,
    case_count_cfg: str | int,
    rng: random.Random,
) -> dict:
    interface_name = str(interface_item.get("interface", "unknown"))
    expanded = interface_item.get("expanded_variables", [])

    selected_vars = []
    for var in expanded:
        is_target = bool(var.get("variation_target", False))
        if scope == "selected" and not is_target:
            continue
        value_domain = var.get("value_domain", {})
        candidates = value_domain.get("candidates", []) if isinstance(value_domain, dict) else []
        if not isinstance(candidates, list):
            candidates = []
        candidates = [str(v) for v in candidates]
        selected_vars.append(
            {
                "name": str(var.get("name", "")),
                "basic_type": str(var.get("basic_type", "")),
                "candidates": candidates,
            }
        )

    warnings: List[str] = []
    vars_for_combine = [v for v in selected_vars if v["candidates"]]
    empty_candidate_vars = [v["name"] for v in selected_vars if not v["candidates"]]
    if empty_candidate_vars:
        warnings.append(
            "Skipped variables with empty candidate list: " + ", ".join(empty_candidate_vars)
        )

    radixes = [len(v["candidates"]) for v in vars_for_combine]
    total_combinations = math.prod(radixes) if radixes else 0

    requested = case_count_cfg
    if requested == "all":
        generated_count = total_combinations
    else:
        generated_count = min(int(requested), total_combinations)

    case_mode = (
        "all_combinations"
        if requested == "all" or generated_count == total_combinations
        else "sampled_without_replacement"
    )

    test_cases: List[dict] = []
    if total_combinations > 0 and generated_count > 0:
        if case_mode == "all_combinations":
            combos = product(*[v["candidates"] for v in vars_for_combine])
            for idx, combo in enumerate(combos, start=1):
                test_cases.append(
                    {
                        "id": f"TC_{idx:06d}",
                        "inputs": {
                            var_def["name"]: value
                            for var_def, value in zip(vars_for_combine, combo)
                        },
                    }
                )
        else:
            indices = _sample_unique_indices(total_combinations, generated_count, rng)
            for case_idx, combo_index in enumerate(indices, start=1):
                digit_indexes = _decode_combination_index(combo_index, radixes)
                combo = [
                    var_def["candidates"][digit]
                    for var_def, digit in zip(vars_for_combine, digit_indexes)
                ]
                test_cases.append(
                    {
                        "id": f"TC_{case_idx:06d}",
                        "combination_index": combo_index,
                        "inputs": {
                            var_def["name"]: value
                            for var_def, value in zip(vars_for_combine, combo)
                        },
                    }
                )
    elif selected_vars and total_combinations == 0:
        warnings.append("No combinations generated because all selected variables have empty candidates.")
    elif not selected_vars:
        warnings.append("No variables matched current scope for case generation.")

    return {
        "interface": interface_name,
        "variable_scope": scope,
        "variables": [
            {
                "name": v["name"],
                "basic_type": v["basic_type"],
                "candidate_count": len(v["candidates"]),
                "candidates": v["candidates"],
            }
            for v in vars_for_combine
        ],
        "stats": {
            "selected_variable_count": len(selected_vars),
            "combination_variable_count": len(vars_for_combine),
            "total_combinations": total_combinations,
            "requested_case_count": requested,
            "generated_case_count": len(test_cases),
            "generation_mode": case_mode,
        },
        "warnings": warnings,
        "test_cases": test_cases,
    }


def generate_cases(
    config_path: Path,
    scope_override: str | None = None,
    case_count_override: str | int | None = None,
    seed_override: int | None = None,
    output_mode_override: str | None = None,
) -> dict:
    config = load_config(config_path)
    gen_cfg = _get_case_generation_config(config)

    scope = _normalize_scope(scope_override, gen_cfg["variable_scope"]) if scope_override else gen_cfg["variable_scope"]
    output_mode = (
        _normalize_output_mode(output_mode_override, gen_cfg["mode"])
        if output_mode_override
        else gen_cfg["mode"]
    )
    case_count_cfg = (
        _parse_case_count(case_count_override, str(gen_cfg["case_count"]))
        if case_count_override is not None
        else gen_cfg["case_count"]
    )
    random_seed = seed_override if seed_override is not None else gen_cfg["random_seed"]
    rng = random.Random(random_seed)

    parsed = parse_targets(config_path, output_mode="full")

    interface_results = [
        _build_interface_cases(item, scope, case_count_cfg, rng)
        for item in parsed.get("interface_results", [])
    ]

    if output_mode == "simple":
        simple_items = []
        for item in interface_results:
            simple_cases = []
            for case in item.get("test_cases", []):
                simple_cases.append(
                    {
                        "id": case.get("id"),
                        "inputs": case.get("inputs", {}),
                    }
                )
            simple_items.append(
                {
                    "interface": item.get("interface", "unknown"),
                    "test_cases": simple_cases,
                }
            )
        return {
            "interface_results": simple_items,
        }

    summary = {
        "interface_count": len(interface_results),
        "output_mode": output_mode,
        "variable_scope": scope,
        "case_count_config": case_count_cfg,
        "random_seed": random_seed,
        "total_cases_generated": sum(
            int(item.get("stats", {}).get("generated_case_count", 0))
            for item in interface_results
        ),
    }

    return {
        "generation": {
            "dimension": "discrete_only",
            "notes": "No continuous-value bucketing in current version.",
        },
        "interface_results": interface_results,
        "summary": summary,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="Path to JSON config")
    ap.add_argument(
        "--output",
        default="",
        help="Optional output JSON file path. If omitted, write by config.case_generation.*",
    )
    ap.add_argument(
        "--scope",
        choices=["all", "selected"],
        default="",
        help="Override case_generation.variable_scope.",
    )
    ap.add_argument(
        "--mode",
        choices=["full", "simple"],
        default="",
        help="Override case_generation.mode.",
    )
    ap.add_argument(
        "--case-count",
        default="",
        help="Override case_generation.case_count. Use positive integer or 'all'.",
    )
    ap.add_argument(
        "--simple",
        action="store_true",
        help="Output simple report (only test id and variable inputs).",
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Override case_generation.random_seed.",
    )
    args = ap.parse_args()

    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    gen_cfg = _get_case_generation_config(config)

    scope_override = args.scope if args.scope else None
    mode_override = args.mode if args.mode else None
    if args.simple:
        mode_override = "simple"
    case_count_override: str | int | None = None
    if args.case_count:
        case_count_override = _parse_case_count(args.case_count, "all")

    result = generate_cases(
        config_path=config_path,
        scope_override=scope_override,
        case_count_override=case_count_override,
        seed_override=args.seed,
        output_mode_override=mode_override,
    )

    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        out_path = Path(args.output).resolve()
    else:
        out_path = build_output_path(config_path, config, output_cfg=gen_cfg)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(output, encoding="utf-8")
    print(f"Output written: {out_path}")


if __name__ == "__main__":
    main()
