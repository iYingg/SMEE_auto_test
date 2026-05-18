#!/usr/bin/env python3
"""
Parse target C interfaces and flatten parameter variables to basic types.

Usage:
    python src/interface_parser/parse_interface.py
    python src/interface_parser/parse_interface.py --config config/parser/targets.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, List

if __package__:
    from .c_parser import CTypeParser, LeafVar, VarDecl, is_known_basic_type
    from .configuration import (
        build_output_path,
        get_type_profile,
        get_variable_profile,
        load_config,
        resolve_project_root,
        resolve_parse_file_groups,
    )
    from .type_specs import INTEGER_LIMITS, select_type_spec
else:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from interface_parser.c_parser import CTypeParser, LeafVar, VarDecl, is_known_basic_type
    from interface_parser.configuration import (
        build_output_path,
        get_type_profile,
        get_variable_profile,
        load_config,
        resolve_project_root,
        resolve_parse_file_groups,
    )
    from interface_parser.type_specs import INTEGER_LIMITS, select_type_spec


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


def _get_case_generation_extra_config(config: dict) -> tuple[List[dict], Dict[str, List[dict]]]:
    cfg = config.get("case_generation", {})
    if not isinstance(cfg, dict):
        return [], {}
    return (
        _normalize_extra_variables(cfg.get("extra_variables")),
        _normalize_interface_extra_variables(cfg.get("interface_extra_variables")),
    )


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


def _with_interface_prefix(interface_name: str, var_path: str) -> str:
    return f"{interface_name}.{var_path}"


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


def _string_candidate_len(text: str) -> int:
    s = str(text)
    if len(s) >= 2 and s[0] == s[-1] and s[0] in {"'", '"'}:
        inner = s[1:-1]
        try:
            return len(bytes(inner, "utf-8").decode("unicode_escape"))
        except UnicodeDecodeError:
            return len(inner)
    return len(s)


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


def _resolve_extra_variables_for_interface(
    config: dict,
    interface_name: str,
    extra_defs: List[dict],
    enum_members: Dict[str, List[str]],
    enum_member_values: Dict[str, List[tuple[str, int]]],
) -> List[dict]:
    resolved: List[dict] = []
    for item in extra_defs:
        name = str(item.get("name", "")).strip()
        if not name:
            continue

        basic_type = str(item.get("basic_type", "")).strip() or "custom"
        source_type = str(item.get("source_type", "")).strip() or basic_type
        type_name = str(item.get("type_name", "")).strip()
        if type_name:
            source_type = type_name

        type_profile = get_type_profile(config, interface_name, basic_type, source_type)
        variable_profile = get_variable_profile(config, interface_name, name)
        profile = _deep_merge_dict(type_profile, variable_profile)
        profile = _deep_merge_dict(profile, item if isinstance(item, dict) else {})
        type_spec = select_type_spec(
            basic_type,
            source_type,
            profile,
            enum_members,
            enum_member_values,
        )

        legal_values = _normalize_str_list(item.get("candidates"))
        if not legal_values:
            legal_values = _normalize_str_list(item.get("seed_pool"))
        if not legal_values:
            legal_values = _normalize_str_list(item.get("legal_values"))
        if not legal_values:
            legal_values = type_spec.get_legal_values()

        illegal_values = type_spec.get_illegal_values()
        is_target = bool(item.get("selected", True))
        value_domain = {
            "source": "extra_variable",
            "candidates": legal_values,
            "invalid_candidates": illegal_values,
        }

        extra_item = {
            "name": name,
            "basic_type": basic_type if basic_type != "custom" else "custom",
            "variation_target": is_target,
            "value_domain": value_domain,
        }
        if basic_type == "enum(int)":
            extra_item["enum_type_name"] = source_type
        resolved.append(extra_item)
    return resolved


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
            qualified_name = _with_interface_prefix(interface_name, v.path)
            is_target = _is_variation_target(qualified_name, variation_patterns) if variation_enabled else True
            if variation_mode == "only" and not is_target:
                continue
            simple_vars.append(
                {
                    "name": qualified_name,
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
        qualified_name = _with_interface_prefix(interface_name, v.path)
        is_target = _is_variation_target(qualified_name, variation_patterns) if variation_enabled else True
        if variation_mode == "only" and not is_target:
            continue

        type_profile = get_type_profile(config, interface_name, v.basic_type, v.source_type)
        variable_profile = get_variable_profile(config, interface_name, qualified_name)
        profile = _deep_merge_dict(type_profile, variable_profile)
        type_spec = select_type_spec(
            v.basic_type, v.source_type, profile, enum_members, enum_member_values
        )
        legal_values = type_spec.get_legal_values()
        illegal_values = type_spec.get_illegal_values()
        value_domain = {
            "source": type_spec.value_source(),
            "candidates": legal_values,
            "invalid_candidates": illegal_values,
        }
        item = {
            "name": qualified_name,
            "basic_type": v.basic_type,
            "variation_target": is_target,
            "value_domain": value_domain,
        }
        if v.basic_type == "enum(int)":
            item["enum_type_name"] = v.source_type
        expanded_variables.append(item)

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
    base_dir = resolve_project_root(config_path)
    config = load_config(config_path)

    type_files, interface_files = resolve_parse_file_groups(base_dir, config)
    parser = CTypeParser()
    parser.parse_headers(type_files)
    global_extra_defs, interface_extra_map = _get_case_generation_extra_config(config)

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
        extra_defs = list(global_extra_defs)
        extra_defs.extend(interface_extra_map.get(interface_name, []))
        if output_mode == "full" and extra_defs:
            extras = _resolve_extra_variables_for_interface(
                config,
                interface_name,
                extra_defs,
                parser.enum_members,
                parser.enum_member_values,
            )
            item["expanded_variables"].extend(extras)
            item["stats"]["expanded_variable_count"] = len(item["expanded_variables"])
            item["stats"]["variation_target_count"] = len(
                [x for x in item["expanded_variables"] if x.get("variation_target")]
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
    default_config = Path(__file__).resolve().parents[2] / "config" / "parser" / "targets.json"
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--config",
        default=str(default_config),
        help=f"Path to JSON config (default: {default_config}).",
    )
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
