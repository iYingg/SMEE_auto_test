from __future__ import annotations

import glob
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple


C_PARSE_FILE_EXTENSIONS = {".h", ".hpp", ".hh", ".hxx", ".c", ".cc", ".cpp", ".cxx"}


def resolve_project_root(config_path: Path) -> Path:
    """
    Resolve project root for both legacy and split config layouts.

    Priority:
    1) first ancestor containing both 'data' and 'src'
    2) fallback to legacy behavior: config_path.parent.parent
    """
    cfg = config_path.resolve()
    for p in [cfg.parent, *cfg.parents]:
        if (p / "data").exists() and (p / "src").exists():
            return p.resolve()
    return cfg.parent.parent.resolve()


def _deep_merge_dict(base: dict, override: dict) -> dict:
    merged = dict(base)
    for k, v in override.items():
        if isinstance(merged.get(k), dict) and isinstance(v, dict):
            merged[k] = _deep_merge_dict(merged[k], v)
        else:
            merged[k] = v
    return merged


def _load_json_dict(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"Config file must be a JSON object: {path}")
    return data


def _resolve_include_path(config_path: Path, include_path: str) -> Path:
    p = Path(include_path)
    if p.is_absolute():
        return p
    from_config_dir = (config_path.parent / p).resolve()
    if from_config_dir.exists():
        return from_config_dir
    return (config_path.parent.parent / p).resolve()


def _load_includes(config_path: Path, includes: object) -> dict:
    merged: dict = {}
    if isinstance(includes, str) and includes.strip():
        include_path = _resolve_include_path(config_path, includes)
        if not include_path.exists():
            raise FileNotFoundError(f"Included config not found: {include_path}")
        merged = _deep_merge_dict(merged, _load_json_dict(include_path))
    elif isinstance(includes, list):
        for item in includes:
            if not isinstance(item, str) or not item.strip():
                continue
            include_path = _resolve_include_path(config_path, item)
            if not include_path.exists():
                raise FileNotFoundError(f"Included config not found: {include_path}")
            merged = _deep_merge_dict(merged, _load_json_dict(include_path))
    elif isinstance(includes, dict):
        # Backward-compatible include style.
        for section in ["output", "case_generation", "basic_type_profiles", "variable_profiles", "interface_profiles"]:
            include_file = includes.get(section)
            if not isinstance(include_file, str) or not include_file.strip():
                continue
            include_path = _resolve_include_path(config_path, include_file)
            if not include_path.exists():
                raise FileNotFoundError(f"Included config not found for '{section}': {include_path}")
            include_obj = _load_json_dict(include_path)
            if section in include_obj and isinstance(include_obj[section], dict):
                merged[section] = _deep_merge_dict(merged.get(section, {}), include_obj[section])
            else:
                merged[section] = _deep_merge_dict(merged.get(section, {}), include_obj)
    return merged


def load_config(config_path: Path) -> dict:
    data = _load_json_dict(config_path)

    includes = _load_includes(config_path, data.get("includes"))
    data = _deep_merge_dict(includes, data)

    # Backward-compatible alias.
    profile_files = data.get("profile_files", {})
    if isinstance(profile_files, dict):
        for section in ["basic_type_profiles", "variable_profiles", "interface_profiles"]:
            include_file = profile_files.get(section)
            if not isinstance(include_file, str) or not include_file.strip():
                continue
            include_obj = _load_json_dict(_resolve_include_path(config_path, include_file))
            data[section] = _deep_merge_dict(include_obj, data.get(section, {}))

    if "target_interfaces" not in data:
        raise ValueError("Missing config key: target_interfaces")
    if not any(
        k in data for k in ["header_files", "source_files", "type_files", "interface_files"]
    ):
        raise ValueError(
            "Missing source file config: one of header_files/source_files/type_files/interface_files is required"
        )
    return data


def _is_parse_candidate(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in C_PARSE_FILE_EXTENSIONS


def _expand_path_entry(base_dir: Path, entry: str) -> List[Path]:
    entry = str(entry).strip()
    if not entry:
        return []

    has_glob = any(c in entry for c in "*?[]")
    if has_glob:
        pattern = entry if Path(entry).is_absolute() else str(base_dir / entry)
        matched = [Path(p).resolve() for p in glob.glob(pattern, recursive=True)]
        files = [p for p in matched if _is_parse_candidate(p)]
        if not files:
            raise FileNotFoundError(f"No parseable files matched pattern: {entry}")
        return sorted(files, key=lambda p: str(p).lower())

    full = (Path(entry) if Path(entry).is_absolute() else (base_dir / entry)).resolve()
    if not full.exists():
        raise FileNotFoundError(f"Configured path not found: {full}")

    if full.is_file():
        if not _is_parse_candidate(full):
            raise ValueError(f"Unsupported parse file type: {full}")
        return [full]

    files = [p.resolve() for p in full.rglob("*") if _is_parse_candidate(p)]
    if not files:
        raise FileNotFoundError(f"No parseable files found under directory: {full}")
    return sorted(files, key=lambda p: str(p).lower())


def resolve_paths(base_dir: Path, entries: List[str]) -> List[Path]:
    if not isinstance(entries, list) or not entries:
        return []
    out: List[Path] = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, str):
            continue
        for p in _expand_path_entry(base_dir, entry):
            key = str(p).lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
    return out


def resolve_parse_file_groups(base_dir: Path, config: dict) -> Tuple[List[Path], List[Path]]:
    shared_inputs = config.get("source_files", [])
    legacy_inputs = config.get("header_files", [])

    type_inputs = config.get("type_files", [])
    interface_inputs = config.get("interface_files", [])

    if not type_inputs:
        type_inputs = shared_inputs if shared_inputs else legacy_inputs
    if not interface_inputs:
        interface_inputs = shared_inputs if shared_inputs else legacy_inputs
    if not type_inputs:
        type_inputs = interface_inputs
    if not interface_inputs:
        interface_inputs = type_inputs

    type_files = resolve_paths(base_dir, type_inputs)
    interface_files = resolve_paths(base_dir, interface_inputs)

    if not type_files:
        raise ValueError("No type files resolved from config")
    if not interface_files:
        raise ValueError("No interface files resolved from config")

    return type_files, interface_files


def _render_output_filename(filename_format: str, target_interfaces: List[str]) -> str:
    now = datetime.now()
    placeholder_values = {
        "date": now.strftime("%Y%m%d"),
        "time": now.strftime("%H%M%S"),
        "datetime": now.strftime("%Y%m%d_%H%M%S"),
        "count": str(len(target_interfaces)),
        "interface": target_interfaces[0] if len(target_interfaces) == 1 else "multi",
    }

    filename = filename_format
    for key, value in placeholder_values.items():
        filename = filename.replace(f"{{{key}}}", value)
    if not filename.lower().endswith(".json"):
        filename += ".json"
    return re.sub(r'[<>:"/\\|?*]', "_", filename)


def build_output_path(config_path: Path, config: dict, output_cfg: dict | None = None) -> Path:
    base_dir = resolve_project_root(config_path)
    output_cfg = output_cfg if isinstance(output_cfg, dict) else config.get("output", {})
    out_dir_rel = output_cfg.get("dir", "output")
    filename_format = output_cfg.get("filename_format", "parsed_{datetime}.json")

    target_interfaces = config.get("target_interfaces", [])
    filename = _render_output_filename(str(filename_format), target_interfaces)
    out_dir = (base_dir / out_dir_rel).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / filename


def _get_profiles(raw: object) -> Dict[str, dict]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, dict] = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            out[str(k)] = v
    return out


def _merge_profile_levels(profiles: Dict[str, dict], basic_type: str, source_type: str) -> dict:
    merged: dict = {}
    for key in ["*", basic_type, source_type]:
        item = profiles.get(key, {})
        if isinstance(item, dict):
            merged = _deep_merge_dict(merged, item)
    return merged


def _pattern_match(pattern: str, text: str) -> bool:
    regex = re.escape(pattern)
    regex = regex.replace(r"\[\*\]", r"\[[^\]]+\]")
    regex = regex.replace(r"\*", r".*")
    return re.fullmatch(regex, text) is not None


def _merge_variable_profile_levels(profiles: Dict[str, dict], var_path: str) -> dict:
    merged: dict = {}
    wildcard_keys: List[str] = []
    exact_hit: dict = {}

    for key, item in profiles.items():
        if not isinstance(item, dict):
            continue
        if key == var_path:
            exact_hit = item
            continue
        if key == "*":
            merged = _deep_merge_dict(merged, item)
            continue
        if _pattern_match(key, var_path):
            wildcard_keys.append(key)

    # Apply wildcard matches from generic to specific, then exact path last.
    wildcard_keys.sort(key=lambda x: (-x.count("*"), len(x)))
    for key in wildcard_keys:
        merged = _deep_merge_dict(merged, profiles[key])
    merged = _deep_merge_dict(merged, exact_hit)
    return merged


def get_type_profile(config: dict, interface_name: str, basic_type: str, source_type: str) -> dict:
    """
    Priority:
    interface-level override > source_type override > basic_type override > default(*).

    Effective merge order:
    global[*] -> global[basic_type] -> global[source_type]
    -> interface[*] -> interface[basic_type] -> interface[source_type]
    """
    global_profiles = _get_profiles(config.get("basic_type_profiles", {}))
    interface_map = config.get("interface_profiles", {})
    interface_cfg = interface_map.get(interface_name, {}) if isinstance(interface_map, dict) else {}
    interface_profiles = _get_profiles(interface_cfg.get("basic_type_profiles", {}))

    merged = _merge_profile_levels(global_profiles, basic_type, source_type)
    merged = _deep_merge_dict(merged, _merge_profile_levels(interface_profiles, basic_type, source_type))
    return merged


def get_variable_profile(config: dict, interface_name: str, var_path: str) -> dict:
    """
    Variable-level override priority:
    global variable_profiles < interface variable_profiles

    Per level merge order:
    [*] -> wildcard patterns -> exact variable path
    """
    global_profiles = _get_profiles(config.get("variable_profiles", {}))
    interface_map = config.get("interface_profiles", {})
    interface_cfg = interface_map.get(interface_name, {}) if isinstance(interface_map, dict) else {}
    interface_profiles = _get_profiles(interface_cfg.get("variable_profiles", {}))

    merged = _merge_variable_profile_levels(global_profiles, var_path)
    merged = _deep_merge_dict(merged, _merge_variable_profile_levels(interface_profiles, var_path))
    return merged
