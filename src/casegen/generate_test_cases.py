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

from interface_parser.configuration import (
    build_output_path,
    get_type_profile,
    get_variable_profile,
    load_config,
    resolve_parse_file_groups,
    resolve_project_root,
)
from interface_parser.c_parser import CTypeParser
from interface_parser.parse_interface import parse_targets
from interface_parser.type_specs import select_type_spec


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


def _normalize_constraint_groups(value: object) -> List[dict]:
    if not isinstance(value, list):
        return []
    out: List[dict] = []
    for item in value:
        if isinstance(item, dict):
            out.append(item)
    return out


def _normalize_extra_variables(value: object) -> List[dict]:
    if not isinstance(value, list):
        return []
    out: List[dict] = []
    for item in value:
        if isinstance(item, dict):
            out.append(item)
    return out


def _normalize_interface_extra_variables(value: object) -> Dict[str, List[dict]]:
    if not isinstance(value, dict):
        return {}
    out: Dict[str, List[dict]] = {}
    for k, v in value.items():
        key = str(k).strip()
        if not key:
            continue
        out[key] = _normalize_extra_variables(v)
    return out


def _to_str_list(value: object) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value]


def _deep_merge_dict(base: dict, override: dict) -> dict:
    merged = dict(base)
    for k, v in override.items():
        if isinstance(merged.get(k), dict) and isinstance(v, dict):
            merged[k] = _deep_merge_dict(merged[k], v)
        else:
            merged[k] = v
    return merged


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
        "constraint_groups": _normalize_constraint_groups(cfg.get("constraint_groups")),
        "extra_variables": _normalize_extra_variables(cfg.get("extra_variables")),
        "interface_extra_variables": _normalize_interface_extra_variables(
            cfg.get("interface_extra_variables")
        ),
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


def _resolve_extra_variables_for_interface(
    config: dict,
    interface_name: str,
    extra_defs: List[dict],
    type_parser: CTypeParser,
) -> tuple[List[dict], List[str]]:
    resolved: List[dict] = []
    warnings: List[str] = []

    for idx, item in enumerate(extra_defs, start=1):
        name = str(item.get("name", "")).strip()
        if not name:
            warnings.append(f"extra_variables[{idx}] skipped: missing 'name'.")
            continue

        selected = bool(item.get("selected", True))
        type_name = str(item.get("type_name", "")).strip()
        source_type_cfg = str(item.get("source_type", "")).strip()
        basic_type_cfg = str(item.get("basic_type", "")).strip()

        source_type = "custom"
        basic_type = "custom"
        if type_name:
            source_type = type_parser.resolve_alias(type_name)
            basic_type = type_parser.classify_basic_type(type_name)
        elif source_type_cfg:
            source_type = type_parser.resolve_alias(source_type_cfg)
            basic_type = type_parser.classify_basic_type(source_type_cfg)
        elif basic_type_cfg:
            basic_type = basic_type_cfg
            source_type = basic_type_cfg
        else:
            source_type = "custom"
            basic_type = "custom"

        candidates = _to_str_list(item.get("candidates"))
        if not candidates:
            candidates = _to_str_list(item.get("seed_pool"))
        if not candidates:
            candidates = _to_str_list(item.get("legal_values"))

        source = "custom_candidates"
        if not candidates:
            if any(k in item for k in ["basic_type", "source_type", "type_name", "from_profile"]):
                profile = get_type_profile(config, interface_name, basic_type, source_type)
                profile = _deep_merge_dict(
                    profile, get_variable_profile(config, interface_name, name)
                )
                entry_profile = {}
                for key in [
                    "seed_pool",
                    "legal_values",
                    "illegal_values",
                    "boundary_values",
                    "value_range",
                ]:
                    if key in item:
                        entry_profile[key] = item[key]
                profile = _deep_merge_dict(profile, entry_profile)

                type_spec = select_type_spec(
                    basic_type=basic_type,
                    source_type=source_type,
                    profile=profile,
                    enum_members=type_parser.enum_members,
                    enum_member_values=type_parser.enum_member_values,
                )
                candidates = type_spec.get_legal_values()
                source = "profile_derived"

        if not candidates:
            warnings.append(
                f"extra variable '{name}' skipped: no candidates (set candidates/seed_pool or profile keys)."
            )
            continue

        resolved.append(
            {
                "name": name,
                "basic_type": basic_type,
                "source_type": source_type,
                "candidates": candidates,
                "selected": selected,
                "source": source,
            }
        )

    return resolved, warnings


def _build_dimensions(
    selected_vars: List[dict], constraint_groups: List[dict], warnings: List[str]
) -> tuple[List[dict], List[str]]:
    var_map = {v["name"]: v for v in selected_vars}
    constrained_vars = set()
    dimensions: List[dict] = []
    applied_group_names: List[str] = []

    for idx, group in enumerate(constraint_groups, start=1):
        name = str(group.get("name", f"group_{idx}"))
        vars_raw = group.get("variables", [])
        combos_raw = group.get("combinations", [])

        if not isinstance(vars_raw, list) or not vars_raw:
            warnings.append(f"Constraint group '{name}' skipped: variables is empty.")
            continue
        group_vars = [str(v) for v in vars_raw]

        if not isinstance(combos_raw, list) or not combos_raw:
            warnings.append(f"Constraint group '{name}' skipped: combinations is empty.")
            continue

        missing_vars = [v for v in group_vars if v not in var_map]
        if missing_vars:
            warnings.append(
                f"Constraint group '{name}' skipped: variables not found in current scope: "
                + ", ".join(missing_vars)
            )
            continue

        overlap = [v for v in group_vars if v in constrained_vars]
        if overlap:
            warnings.append(
                f"Constraint group '{name}' skipped: overlap with previous groups: "
                + ", ".join(overlap)
            )
            continue

        options: List[dict] = []
        for combo_idx, combo in enumerate(combos_raw, start=1):
            if not isinstance(combo, dict):
                warnings.append(
                    f"Constraint group '{name}' combo#{combo_idx} skipped: not an object."
                )
                continue

            missing_keys = [v for v in group_vars if v not in combo]
            if missing_keys:
                warnings.append(
                    f"Constraint group '{name}' combo#{combo_idx} skipped: missing keys: "
                    + ", ".join(missing_keys)
                )
                continue

            option: Dict[str, str] = {}
            valid = True
            for var_name in group_vars:
                val = str(combo[var_name])
                candidates = var_map[var_name]["candidates"]
                if val not in candidates:
                    warnings.append(
                        f"Constraint group '{name}' combo#{combo_idx} skipped: value '{val}' "
                        f"not in candidates of '{var_name}'."
                    )
                    valid = False
                    break
                option[var_name] = val
            if valid:
                options.append(option)

        if not options:
            warnings.append(f"Constraint group '{name}' skipped: no valid combinations.")
            continue

        dimensions.append(
            {
                "kind": "group",
                "name": name,
                "variables": group_vars,
                "options": options,
            }
        )
        applied_group_names.append(name)
        for var_name in group_vars:
            constrained_vars.add(var_name)

    for v in selected_vars:
        if v["name"] in constrained_vars:
            continue
        options = [{v["name"]: cand} for cand in v["candidates"]]
        if not options:
            continue
        dimensions.append(
            {
                "kind": "single",
                "name": v["name"],
                "variables": [v["name"]],
                "options": options,
            }
        )

    return dimensions, applied_group_names


def _build_interface_cases(
    interface_item: dict,
    scope: str,
    case_count_cfg: str | int,
    rng: random.Random,
    constraint_groups: List[dict],
    extra_vars: List[dict],
    extra_warnings: List[str],
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
                "source_type": str(var.get("basic_type", "")),
                "candidates": candidates,
                "selected": bool(var.get("variation_target", False)),
                "source": "interface_parsed",
            }
        )

    warnings: List[str] = list(extra_warnings)
    if extra_vars:
        selected_vars.extend(extra_vars)

    # Deduplicate by name; later one wins (extra vars can override parsed vars).
    merged_by_name: Dict[str, dict] = {}
    for v in selected_vars:
        merged_by_name[v["name"]] = v
    selected_vars = list(merged_by_name.values())

    if scope == "selected":
        selected_vars = [v for v in selected_vars if bool(v.get("selected", True))]

    vars_for_combine = [v for v in selected_vars if v["candidates"]]
    empty_candidate_vars = [v["name"] for v in selected_vars if not v["candidates"]]
    if empty_candidate_vars:
        warnings.append(
            "Skipped variables with empty candidate list: " + ", ".join(empty_candidate_vars)
        )

    dimensions, applied_group_names = _build_dimensions(vars_for_combine, constraint_groups, warnings)
    radixes = [len(d["options"]) for d in dimensions]
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
            combos = product(*[d["options"] for d in dimensions])
            for idx, combo_options in enumerate(combos, start=1):
                merged_inputs: Dict[str, str] = {}
                for option in combo_options:
                    merged_inputs.update(option)
                test_cases.append(
                    {
                        "id": f"TC_{idx:06d}",
                        "inputs": merged_inputs,
                    }
                )
        else:
            indices = _sample_unique_indices(total_combinations, generated_count, rng)
            for case_idx, combo_index in enumerate(indices, start=1):
                digit_indexes = _decode_combination_index(combo_index, radixes)
                merged_inputs: Dict[str, str] = {}
                for dim, digit in zip(dimensions, digit_indexes):
                    merged_inputs.update(dim["options"][digit])
                test_cases.append(
                    {
                        "id": f"TC_{case_idx:06d}",
                        "combination_index": combo_index,
                        "inputs": merged_inputs,
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
                "source_type": v.get("source_type", v["basic_type"]),
                "candidate_count": len(v["candidates"]),
                "candidates": v["candidates"],
                "source": v.get("source", "unknown"),
            }
            for v in vars_for_combine
        ],
        "constraint_groups_applied": applied_group_names,
        "stats": {
            "selected_variable_count": len(selected_vars),
            "combination_variable_count": len(vars_for_combine),
            "combination_dimension_count": len(dimensions),
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
    base_dir = resolve_project_root(config_path)
    type_files, _ = resolve_parse_file_groups(base_dir, config)
    type_parser = CTypeParser()
    type_parser.parse_headers(type_files)

    interface_results = []
    for item in parsed.get("interface_results", []):
        interface_name = str(item.get("interface", "unknown"))
        raw_extra = list(gen_cfg["extra_variables"])
        raw_extra.extend(gen_cfg["interface_extra_variables"].get(interface_name, []))
        extra_vars, extra_warnings = _resolve_extra_variables_for_interface(
            config, interface_name, raw_extra, type_parser
        )
        interface_results.append(
            _build_interface_cases(
                interface_item=item,
                scope=scope,
                case_count_cfg=case_count_cfg,
                rng=rng,
                constraint_groups=gen_cfg["constraint_groups"],
                extra_vars=extra_vars,
                extra_warnings=extra_warnings,
            )
        )

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
