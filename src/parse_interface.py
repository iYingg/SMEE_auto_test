#!/usr/bin/env python3
"""
Parse target C interfaces and flatten parameter variables to basic types.

Usage:
    python src/parse_interface.py --config config/targets.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, List

from c_parser import CTypeParser, LeafVar, VarDecl, is_known_basic_type
from configuration import (
    build_output_path,
    get_type_profile,
    get_variable_profile,
    load_config,
    resolve_parse_file_groups,
)
from type_specs import select_type_spec


def dedupe_leaf_vars(items: List[LeafVar]) -> List[LeafVar]:
    seen = set()
    out: List[LeafVar] = []
    for v in items:
        key = (v.path, v.basic_type)
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


def _normalize_str_list(value: object) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(x).strip() for x in value if str(x).strip()]


def _deep_merge_dict(base: dict, override: dict) -> dict:
    merged = dict(base)
    for k, v in override.items():
        if isinstance(merged.get(k), dict) and isinstance(v, dict):
            merged[k] = _deep_merge_dict(merged[k], v)
        else:
            merged[k] = v
    return merged


def _normalize_mode(value: object, default: str = "full") -> str:
    mode = str(value).strip().lower()
    if mode in {"full", "simple"}:
        return mode
    return default


def _normalize_scope(value: object, default: str = "all") -> str:
    scope = str(value).strip().lower()
    if scope in {"all", "selected"}:
        return scope
    return default


def _get_variation_config(config: dict, interface_name: str) -> tuple[str, List[str], bool]:
    """
    Returns:
        mode: one of all/mark/only
        patterns: match patterns for variable path
        enabled: whether variation selection is enabled by config
    """
    variation_cfg = config.get("variation", {})
    if not isinstance(variation_cfg, dict):
        return "all", [], False

    mode = str(variation_cfg.get("mode", "mark")).strip().lower()
    if mode not in {"mark", "only"}:
        mode = "mark"

    global_patterns = _normalize_str_list(variation_cfg.get("variables"))
    iface_patterns_map = variation_cfg.get("interfaces", {})
    iface_patterns = []
    if isinstance(iface_patterns_map, dict):
        iface_patterns = _normalize_str_list(iface_patterns_map.get(interface_name))

    patterns = global_patterns + iface_patterns
    if not patterns:
        return "all", [], False
    return mode, patterns, True


def _is_variation_target(path: str, patterns: List[str]) -> bool:
    for pat in patterns:
        # Support array wildcard like field[*].x -> field[0].x / field[1].x ...
        regex = re.escape(pat)
        regex = regex.replace(r"\[\*\]", r"\[[^\]]+\]")
        # Generic wildcard.
        regex = regex.replace(r"\*", r".*")
        if re.fullmatch(regex, path):
            return True
    return False


def _build_selected_report(parsed: dict) -> dict:
    out = {
        "interface_results": [],
        "summary": {
            "interface_count": 0,
            "expanded_variable_count": 0,
            "variation_target_count": 0,
            "report_scope": "variation_selected_only",
        },
    }

    for item in parsed.get("interface_results", []):
        expanded = item.get("expanded_variables", [])
        selected_vars = [v for v in expanded if v.get("variation_target")]

        new_item = dict(item)
        new_item["expanded_variables"] = selected_vars

        stats = dict(new_item.get("stats", {}))
        stats["expanded_variable_count"] = len(selected_vars)
        stats["variation_target_count"] = len(selected_vars)
        new_item["stats"] = stats

        out["interface_results"].append(new_item)
        out["summary"]["interface_count"] += 1
        out["summary"]["expanded_variable_count"] += len(selected_vars)
        out["summary"]["variation_target_count"] += len(selected_vars)
    return out


def _try_parse_int(text: str) -> int | None:
    try:
        return int(str(text).strip(), 0)
    except (ValueError, TypeError):
        return None


def _try_parse_float(text: str) -> float | None:
    try:
        return float(str(text).strip())
    except (ValueError, TypeError):
        return None


def _get_custom_value_range(profile: dict) -> dict | None:
    custom = profile.get("value_range")
    if not isinstance(custom, dict):
        return None
    if "min" not in custom or "max" not in custom:
        return None

    min_i = _try_parse_int(custom.get("min"))
    max_i = _try_parse_int(custom.get("max"))
    if min_i is not None and max_i is not None:
        count_i = _try_parse_int(custom.get("count"))
        if count_i is None:
            count_i = max(0, max_i - min_i + 1)
        return {
            "min": min_i,
            "max": max_i,
            "count": max(0, count_i),
        }

    min_f = _try_parse_float(custom.get("min"))
    max_f = _try_parse_float(custom.get("max"))
    if min_f is not None and max_f is not None:
        out = {
            "min": min_f,
            "max": max_f,
        }
        count_i = _try_parse_int(custom.get("count"))
        if count_i is not None:
            out["count"] = max(0, count_i)
        return out

    return None


def _compute_effective_value_range(
    basic_type: str, legal_values: List[str], type_spec
) -> dict | None:
    enum_range = type_spec.get_value_range()
    if enum_range is not None:
        return enum_range

    int_vals = [_try_parse_int(v) for v in legal_values]
    if all(v is not None for v in int_vals) and int_vals:
        vals = [v for v in int_vals if v is not None]
        return {
            "min": min(vals),
            "max": max(vals),
            "count": len(sorted(set(vals))),
        }

    if basic_type in {"float", "double", "long double"}:
        float_vals = [_try_parse_float(v) for v in legal_values]
        if all(v is not None for v in float_vals) and float_vals:
            vals_f = [v for v in float_vals if v is not None]
            return {
                "min": min(vals_f),
                "max": max(vals_f),
                "count": len(vals_f),
            }
    return None


def _compute_effective_boundary_values(
    basic_type: str,
    legal_values: List[str],
    illegal_values: List[str],
    value_range: dict | None,
    fallback_boundary: dict,
) -> dict:
    if basic_type == "enum(int)":
        out = dict(fallback_boundary)
        out["first"] = legal_values[0] if legal_values else None
        out["last"] = legal_values[-1] if legal_values else None
        if value_range:
            out["min"] = str(value_range["min"])
            out["max"] = str(value_range["max"])
            out["count"] = value_range["count"]
        out["invalid"] = illegal_values
        return out

    range_min_i = _try_parse_int(value_range.get("min")) if isinstance(value_range, dict) else None
    range_max_i = _try_parse_int(value_range.get("max")) if isinstance(value_range, dict) else None
    if range_min_i is not None and range_max_i is not None:
        mn = range_min_i
        mx = range_max_i
        typical = legal_values if legal_values else [str(mn), str(mx)]
        return {
            "min": str(mn),
            "min_plus_1": str(mn + 1) if mn + 1 <= mx else str(mn),
            "typical": typical,
            "max_minus_1": str(mx - 1) if mx - 1 >= mn else str(mx),
            "max": str(mx),
            "invalid": illegal_values,
        }

    int_vals = [_try_parse_int(v) for v in legal_values]
    if all(v is not None for v in int_vals) and int_vals:
        vals = [v for v in int_vals if v is not None]
        mn = min(vals)
        mx = max(vals)
        return {
            "min": str(mn),
            "min_plus_1": str(mn + 1) if mn + 1 <= mx else str(mn),
            "typical": legal_values,
            "max_minus_1": str(mx - 1) if mx - 1 >= mn else str(mx),
            "max": str(mx),
            "invalid": illegal_values,
        }

    range_min_f = _try_parse_float(value_range.get("min")) if isinstance(value_range, dict) else None
    range_max_f = _try_parse_float(value_range.get("max")) if isinstance(value_range, dict) else None
    if range_min_f is not None and range_max_f is not None:
        return {
            "negative_large": None,
            "negative_small": str(range_min_f),
            "zero": "0.0" if range_min_f <= 0 <= range_max_f else None,
            "positive_small": str(range_max_f),
            "positive_large": None,
            "invalid": illegal_values,
        }

    if basic_type in {"float", "double", "long double"}:
        float_vals = [_try_parse_float(v) for v in legal_values]
        if all(v is not None for v in float_vals) and float_vals:
            vals_f = [v for v in float_vals if v is not None]
            return {
                "negative_large": None,
                "negative_small": str(min(vals_f)),
                "zero": "0.0" if any(abs(v) < 1e-15 for v in vals_f) else None,
                "positive_small": str(max(vals_f)),
                "positive_large": None,
                "invalid": illegal_values,
            }

    out = dict(fallback_boundary)
    out["invalid"] = illegal_values
    return out


def build_interface_output(
    interface_name: str,
    flat_vars: List[LeafVar],
    enum_members: Dict[str, List[str]],
    enum_member_values: Dict[str, List[tuple[str, int]]],
    config: dict,
    output_mode: str,
) -> dict:
    flat_vars = dedupe_leaf_vars(flat_vars)
    unresolved = sorted({v.source_type for v in flat_vars if not is_known_basic_type(v.basic_type)})
    variation_mode, variation_patterns, variation_enabled = _get_variation_config(
        config, interface_name
    )

    if output_mode == "simple":
        simple_vars = []
        for v in flat_vars:
            is_target = (
                _is_variation_target(v.path, variation_patterns) if variation_enabled else True
            )
            if variation_mode == "only" and not is_target:
                continue
            simple_vars.append(
                {
                    "name": v.path,
                    "basic_type": v.basic_type,
                    "variation_target": is_target,
                }
            )
        return {
            "interface": interface_name,
            "expanded_variables": simple_vars,
            "stats": {
                "expanded_variable_count": len(simple_vars),
                "variation_target_count": len([x for x in simple_vars if x["variation_target"]]),
            },
            "warnings": {
                "unresolved_basic_types": unresolved,
            },
            "variation": {
                "mode": variation_mode,
                "enabled": variation_enabled,
                "patterns": variation_patterns,
            },
        }

    expanded_variables = []
    for v in flat_vars:
        is_target = _is_variation_target(v.path, variation_patterns) if variation_enabled else True
        if variation_mode == "only" and not is_target:
            continue

        type_profile = get_type_profile(config, interface_name, v.basic_type, v.source_type)
        variable_profile = get_variable_profile(config, interface_name, v.path)
        profile = _deep_merge_dict(type_profile, variable_profile)
        type_spec = select_type_spec(
            v.basic_type, v.source_type, profile, enum_members, enum_member_values
        )
        legal_values = type_spec.get_legal_values()
        illegal_values = type_spec.get_illegal_values()
        value_range = _get_custom_value_range(profile)
        if value_range is None:
            value_range = _compute_effective_value_range(v.basic_type, legal_values, type_spec)
        effective_boundary = _compute_effective_boundary_values(
            v.basic_type, legal_values, illegal_values, value_range, type_spec.get_boundary_values()
        )
        value_domain = {
            "source": type_spec.value_source(),
            "candidates": legal_values,
            "invalid_candidates": illegal_values,
        }
        if value_range is not None:
            value_domain["value_range"] = value_range
        expanded_variables.append(
            {
                "name": v.path,
                "basic_type": v.basic_type,
                "variation_target": is_target,
                "value_domain": value_domain,
                "boundary_values": effective_boundary,
            }
        )

    return {
        "interface": interface_name,
        "expanded_variables": expanded_variables,
        "stats": {
            "expanded_variable_count": len(expanded_variables),
            "variation_target_count": len(
                [x for x in expanded_variables if x.get("variation_target")]
            ),
        },
        "warnings": {
            "unresolved_basic_types": unresolved,
        },
        "variation": {
            "mode": variation_mode,
            "enabled": variation_enabled,
            "patterns": variation_patterns,
        },
    }


def parse_targets(config_path: Path, output_mode: str = "full") -> dict:
    base_dir = config_path.parent.parent.resolve()
    config = load_config(config_path)

    type_files, interface_files = resolve_parse_file_groups(base_dir, config)
    parser = CTypeParser()
    parser.parse_headers(type_files)

    text_by_file = {fp: fp.read_text(encoding="utf-8") for fp in interface_files}
    result = {
        "interface_results": [],
        "summary": {
            "interface_count": 0,
            "expanded_variable_count": 0,
            "variation_target_count": 0,
        },
    }

    for interface_name in config["target_interfaces"]:
        found_params: List[VarDecl] = []
        for fp, txt in text_by_file.items():
            try:
                found_params = parser.parse_function_params(txt, interface_name)
                if found_params:
                    break
            except ValueError:
                continue
        if not found_params:
            raise ValueError(f"Interface not found in configured interface files: {interface_name}")

        flat_vars: List[LeafVar] = []
        for p in found_params:
            flat_vars.extend(parser.flatten_decl(p))

        item = build_interface_output(
            interface_name,
            flat_vars,
            parser.enum_members,
            parser.enum_member_values,
            config,
            output_mode,
        )
        result["interface_results"].append(item)
        result["summary"]["interface_count"] += 1
        result["summary"]["expanded_variable_count"] += item["stats"][
            "expanded_variable_count"
        ]
        result["summary"]["variation_target_count"] += item["stats"].get(
            "variation_target_count", 0
        )

    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="Path to JSON config")
    ap.add_argument(
        "--output",
        default="",
        help="Optional output JSON file path. If omitted, write by config.output.*",
    )
    ap.add_argument(
        "--mode",
        choices=["full", "simple"],
        default="",
        help="Override output mode from config.output.mode.",
    )
    ap.add_argument(
        "--scope",
        choices=["all", "selected"],
        default="",
        help="Override output scope from config.output.scope.",
    )
    ap.add_argument(
        "--simple",
        action="store_true",
        help="Output simple result (only variable name and basic type).",
    )
    args = ap.parse_args()

    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    output_cfg = config.get("output", {}) if isinstance(config.get("output", {}), dict) else {}
    mode = _normalize_mode(output_cfg.get("mode", "full"), "full")
    if args.mode:
        mode = _normalize_mode(args.mode, mode)
    if args.simple:
        mode = "simple"

    # Backward-compat: legacy output.selected_report.enabled -> selected scope in single-output mode.
    scope_default = "all"
    legacy_selected_cfg = output_cfg.get("selected_report", {})
    if (
        "scope" not in output_cfg
        and isinstance(legacy_selected_cfg, dict)
        and bool(legacy_selected_cfg.get("enabled", False))
    ):
        scope_default = "selected"
        if not args.simple and "mode" in legacy_selected_cfg:
            mode = _normalize_mode(legacy_selected_cfg.get("mode"), mode)

    scope = _normalize_scope(output_cfg.get("scope", scope_default), scope_default)
    if args.scope:
        scope = _normalize_scope(args.scope, scope)

    parsed = parse_targets(config_path, output_mode=mode)
    if scope == "selected":
        parsed = _build_selected_report(parsed)
    else:
        parsed["summary"]["report_scope"] = "all_variables"
    parsed["summary"]["output_mode"] = mode
    parsed["summary"]["output_scope"] = scope
    output = json.dumps(parsed, ensure_ascii=False, indent=2)

    if args.output:
        out_path = Path(args.output).resolve()
    else:
        out_path = build_output_path(config_path, config)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(output, encoding="utf-8")
    print(f"Output written: {out_path}")


if __name__ == "__main__":
    main()
