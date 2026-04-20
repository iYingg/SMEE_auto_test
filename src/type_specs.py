from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Tuple

INTEGER_LIMITS = {
    "int8_t": (-128, 127),
    "uint8_t": (0, 255),
    "int16_t": (-32768, 32767),
    "uint16_t": (0, 65535),
    "int32_t": (-2147483648, 2147483647),
    "uint32_t": (0, 4294967295),
    "int64_t": (-9223372036854775808, 9223372036854775807),
    "uint64_t": (0, 18446744073709551615),
    "char": (-128, 127),
    "signed char": (-128, 127),
    "unsigned char": (0, 255),
    "short": (-32768, 32767),
    "short int": (-32768, 32767),
    "unsigned short": (0, 65535),
    "unsigned short int": (0, 65535),
    "int": (-2147483648, 2147483647),
    "unsigned int": (0, 4294967295),
    "long": (-2147483648, 2147483647),
    "long int": (-2147483648, 2147483647),
    "unsigned long": (0, 4294967295),
    "unsigned long int": (0, 4294967295),
    "long long": (-9223372036854775808, 9223372036854775807),
    "long long int": (-9223372036854775808, 9223372036854775807),
    "unsigned long long": (0, 18446744073709551615),
    "unsigned long long int": (0, 18446744073709551615),
}


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


class BaseTypeSpec(ABC):
    def __init__(self, basic_type: str, source_type: str, profile: dict) -> None:
        self.basic_type = basic_type
        self.source_type = source_type
        self.profile = profile

    @abstractmethod
    def default_legal_values(self) -> List[str]:
        pass

    @abstractmethod
    def default_illegal_values(self) -> List[str]:
        pass

    @abstractmethod
    def default_boundary_values(self) -> dict:
        pass

    def get_default_values(self) -> dict:
        return {
            "legal_values": self.default_legal_values(),
            "illegal_values": self.default_illegal_values(),
            "boundary_values": self.default_boundary_values(),
        }

    def get_legal_values(self) -> List[str]:
        custom = _to_str_list(self.profile.get("seed_pool"))
        if custom:
            return custom
        custom = _to_str_list(self.profile.get("legal_values"))
        if custom:
            return custom
        return self.default_legal_values()

    def get_illegal_values(self) -> List[str]:
        custom = _to_str_list(self.profile.get("illegal_values"))
        if custom:
            return custom
        return self.default_illegal_values()

    def get_boundary_values(self) -> dict:
        defaults = self.default_boundary_values()
        custom = self.profile.get("boundary_values")
        if isinstance(custom, dict):
            return _deep_merge_dict(defaults, custom)
        return defaults

    def get_value_range(self) -> dict | None:
        return None

    def value_source(self) -> str:
        if any(
            k in self.profile
            for k in ["seed_pool", "legal_values", "illegal_values", "boundary_values", "value_range"]
        ):
            return "config_profile"
        return "builtin_default"


class IntegerTypeSpec(BaseTypeSpec):
    def _limits(self) -> Tuple[int, int] | None:
        return INTEGER_LIMITS.get(self.basic_type)

    def default_legal_values(self) -> List[str]:
        limits = self._limits()
        if not limits:
            return ["0", "1", "-1"]
        mn, mx = limits
        values = [mn, 0, 1, mx]
        in_range = [str(v) for v in values if mn <= v <= mx]
        seen = set()
        out: List[str] = []
        for v in in_range:
            if v not in seen:
                seen.add(v)
                out.append(v)
        return out

    def default_illegal_values(self) -> List[str]:
        limits = self._limits()
        if not limits:
            return []
        mn, mx = limits
        return [str(mn - 1), str(mx + 1)]

    def default_boundary_values(self) -> dict:
        limits = self._limits()
        if not limits:
            return {
                "min": None,
                "min_plus_1": None,
                "typical": [],
                "max_minus_1": None,
                "max": None,
                "invalid": [],
            }
        mn, mx = limits
        typical = []
        for v in [0, 1, -1]:
            if mn <= v <= mx:
                typical.append(str(v))
        return {
            "min": str(mn),
            "min_plus_1": str(mn + 1),
            "typical": typical,
            "max_minus_1": str(mx - 1),
            "max": str(mx),
            "invalid": [str(mn - 1), str(mx + 1)],
        }


class FloatTypeSpec(BaseTypeSpec):
    def default_legal_values(self) -> List[str]:
        return ["-1.0", "0.0", "1.0"]

    def default_illegal_values(self) -> List[str]:
        return []

    def default_boundary_values(self) -> dict:
        return {
            "negative_large": None,
            "negative_small": "-1e-6",
            "zero": "0.0",
            "positive_small": "1e-6",
            "positive_large": None,
            "invalid": [],
        }


class BoolTypeSpec(BaseTypeSpec):
    def default_legal_values(self) -> List[str]:
        return ["0", "1"]

    def default_illegal_values(self) -> List[str]:
        return ["-1", "2"]

    def default_boundary_values(self) -> dict:
        return {
            "false": "0",
            "true": "1",
            "invalid": ["-1", "2"],
        }


class StringTypeSpec(BaseTypeSpec):
    def default_legal_values(self) -> List[str]:
        return ['""', '"A"', '"Hello"']

    def default_illegal_values(self) -> List[str]:
        return ["NULL"]

    def default_boundary_values(self) -> dict:
        return {
            "empty": '""',
            "min_len": '"A"',
            "max_len": None,
            "invalid": ["NULL"],
        }


class EnumTypeSpec(BaseTypeSpec):
    def __init__(
        self,
        basic_type: str,
        source_type: str,
        profile: dict,
        enum_members: Dict[str, List[str]],
        enum_member_values: Dict[str, List[Tuple[str, int]]],
    ) -> None:
        super().__init__(basic_type, source_type, profile)
        self.enum_members = enum_members
        self.enum_member_values = enum_member_values

    @staticmethod
    def _is_enum_max_symbol(name: str) -> bool:
        upper = name.upper()
        return upper.endswith("_MAX") or upper.endswith("_ENUM_MAX") or upper.endswith("_NUM")

    def _get_member_values(self) -> List[Tuple[str, int]]:
        items = self.enum_member_values.get(self.source_type, [])
        if items:
            return items
        # Fallback by index when concrete values were not captured.
        return [(name, idx) for idx, name in enumerate(self.enum_members.get(self.source_type, []))]

    def _get_effective_legal_members(self) -> List[Tuple[str, int]]:
        members = self._get_member_values()
        if not members:
            return []

        # Common convention: last XXX_MAX/XXX_NUM is sentinel, not legal input.
        last_name, last_val = members[-1]
        if self._is_enum_max_symbol(last_name) and last_val >= 1:
            return [x for x in members if x[1] < last_val]
        return members

    def _get_effective_range(self) -> Tuple[int, int] | None:
        legal_members = self._get_effective_legal_members()
        if not legal_members:
            return None
        values = [x[1] for x in legal_members]
        return min(values), max(values)

    def default_legal_values(self) -> List[str]:
        return [x[0] for x in self._get_effective_legal_members()]

    def default_illegal_values(self) -> List[str]:
        value_range = self._get_effective_range()
        if value_range is None:
            return []
        mn, mx = value_range
        invalid = [str(mn - 1), str(mx + 1)]

        members = self._get_member_values()
        if members:
            last_name, _ = members[-1]
            if self._is_enum_max_symbol(last_name) and last_name not in invalid:
                invalid.append(last_name)
        return invalid

    def default_boundary_values(self) -> dict:
        legal_members = self._get_effective_legal_members()
        value_range = self._get_effective_range()
        mn = str(value_range[0]) if value_range else None
        mx = str(value_range[1]) if value_range else None
        return {
            "first": legal_members[0][0] if legal_members else None,
            "last": legal_members[-1][0] if legal_members else None,
            "min": mn,
            "max": mx,
            "count": len(legal_members),
            "invalid": self.default_illegal_values(),
        }

    def value_source(self) -> str:
        if any(
            k in self.profile
            for k in ["seed_pool", "legal_values", "illegal_values", "boundary_values", "value_range"]
        ):
            return "config_profile"
        if self.enum_members.get(self.source_type):
            return "enum_definition"
        return "builtin_default"

    def get_value_range(self) -> dict | None:
        value_range = self._get_effective_range()
        if value_range is None:
            return None
        mn, mx = value_range
        return {
            "min": mn,
            "max": mx,
            "count": mx - mn + 1,
        }


class GenericTypeSpec(BaseTypeSpec):
    def default_legal_values(self) -> List[str]:
        return []

    def default_illegal_values(self) -> List[str]:
        return []

    def default_boundary_values(self) -> dict:
        return {
            "min": None,
            "min_plus_1": None,
            "typical": [],
            "max_minus_1": None,
            "max": None,
            "invalid": [],
        }


def select_type_spec(
    basic_type: str,
    source_type: str,
    profile: dict,
    enum_members: Dict[str, List[str]],
    enum_member_values: Dict[str, List[Tuple[str, int]]] | None = None,
) -> BaseTypeSpec:
    enum_member_values = enum_member_values or {}
    if basic_type == "enum(int)":
        return EnumTypeSpec(basic_type, source_type, profile, enum_members, enum_member_values)
    if basic_type == "string":
        return StringTypeSpec(basic_type, source_type, profile)
    if basic_type in INTEGER_LIMITS:
        return IntegerTypeSpec(basic_type, source_type, profile)
    if basic_type in {"float", "double", "long double"}:
        return FloatTypeSpec(basic_type, source_type, profile)
    if basic_type in {"bool", "_Bool"}:
        return BoolTypeSpec(basic_type, source_type, profile)
    return GenericTypeSpec(basic_type, source_type, profile)
