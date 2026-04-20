from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Set, Tuple

QUALIFIER_TOKENS = {"const", "volatile", "restrict", "static"}
DIR_TOKENS = {"IN", "OUT", "INOUT"}
BUILTIN_TYPES = {
    "char",
    "signed char",
    "unsigned char",
    "short",
    "short int",
    "unsigned short",
    "unsigned short int",
    "int",
    "unsigned int",
    "long",
    "long int",
    "unsigned long",
    "unsigned long int",
    "long long",
    "long long int",
    "unsigned long long",
    "unsigned long long int",
    "float",
    "double",
    "long double",
    "size_t",
    "int8_t",
    "uint8_t",
    "int16_t",
    "uint16_t",
    "int32_t",
    "uint32_t",
    "int64_t",
    "uint64_t",
    "bool",
    "_Bool",
    "void",
}
STRING_CHAR_TYPES = {"char", "signed char", "unsigned char"}
STRING_BASIC_TYPE = "string"


@dataclass
class VarDecl:
    name: str
    type_name: str
    pointer_level: int
    array_dims: List[str]


@dataclass
class LeafVar:
    path: str
    basic_type: str
    source_type: str


class CTypeParser:
    def __init__(self) -> None:
        self.typedef_alias: Dict[str, str] = {}
        self.struct_defs: Dict[str, List[VarDecl]] = {}
        self.enum_types: Set[str] = set()
        self.enum_members: Dict[str, List[str]] = {}
        self.enum_member_values: Dict[str, List[Tuple[str, int]]] = {}
        self.int_constants: Dict[str, int] = {}

    @staticmethod
    def _strip_comments(text: str) -> str:
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        text = re.sub(r"//.*?$", "", text, flags=re.M)
        return text

    @staticmethod
    def _norm_ws(text: str) -> str:
        return re.sub(r"\s+", " ", text.strip())

    @staticmethod
    def _remove_qualifiers(text: str) -> str:
        tokens = [t for t in text.split() if t not in QUALIFIER_TOKENS]
        return " ".join(tokens)

    @staticmethod
    def _remove_dir_tokens(text: str) -> str:
        tokens = [t for t in text.split() if t not in DIR_TOKENS]
        return " ".join(tokens)

    def parse_headers(self, file_paths: List[Path]) -> None:
        combined = []
        for fp in file_paths:
            combined.append(fp.read_text(encoding="utf-8", errors="ignore"))
        text = self._strip_comments("\n".join(combined))
        self._parse_macros(text)
        self._parse_enums(text)
        self._parse_structs(text)
        self._parse_typedef_alias(text)

    def _parse_macros(self, text: str) -> None:
        # Only parse object-like numeric macros: #define N 3 / #define N (A+1)
        # Parse line-by-line to avoid cross-line matching with empty macros like '#define IN'.
        for line in text.splitlines():
            m = re.match(r"^\s*#define\s+([A-Za-z_]\w+)(?:\s+([^\r\n]+))?\s*$", line)
            if not m:
                continue
            name = m.group(1)
            expr = m.group(2)
            if not expr:
                continue
            value = self._eval_const_expr(expr.strip(), self.int_constants)
            if value is not None:
                self.int_constants[name] = value

    def _parse_enums(self, text: str) -> None:
        pattern = re.compile(
            r"typedef\s+enum(?:\s+\w+)?\s*\{(?P<body>.*?)\}\s*(?P<name>\w+)\s*;",
            flags=re.S,
        )
        for m in pattern.finditer(text):
            name = m.group("name")
            body = m.group("body")
            self.enum_types.add(name)
            member_values = self._parse_enum_member_values(body)
            self.enum_member_values[name] = member_values
            self.enum_members[name] = [x[0] for x in member_values]
            for member_name, member_val in member_values:
                self.int_constants[member_name] = member_val

    @staticmethod
    def _try_parse_int_literal(text: str) -> int | None:
        token = text.strip()
        if not token:
            return None
        # Strip common integer suffixes (U/L/UL/LL)
        token = re.sub(r"(?i)(ULL|LLU|UL|LU|LL|U|L)$", "", token)
        try:
            return int(token, 0)
        except ValueError:
            return None

    @classmethod
    def _eval_const_expr(cls, expr: str, known_values: Dict[str, int]) -> int | None:
        expr = expr.strip()
        if not expr:
            return None
        # Trim wrapping parentheses: (X) -> X
        while expr.startswith("(") and expr.endswith(")"):
            inner = expr[1:-1].strip()
            if not inner:
                break
            expr = inner

        if expr in known_values:
            return known_values[expr]

        direct = cls._try_parse_int_literal(expr)
        if direct is not None:
            return direct

        m = re.match(r"^([A-Za-z_]\w*)\s*([+-])\s*([A-Za-z_]\w*|0x[0-9A-Fa-f]+|\d+)$", expr)
        if m:
            left_name = m.group(1)
            op = m.group(2)
            right_text = m.group(3)
            if left_name in known_values:
                right_val = known_values.get(right_text)
                if right_val is None:
                    right_val = cls._try_parse_int_literal(right_text)
                if right_val is not None:
                    return known_values[left_name] + right_val if op == "+" else known_values[left_name] - right_val

        m2 = re.match(r"^(0x[0-9A-Fa-f]+|\d+)\s*([+-])\s*([A-Za-z_]\w*)$", expr)
        if m2:
            left_text = m2.group(1)
            op = m2.group(2)
            right_name = m2.group(3)
            if right_name in known_values:
                left_val = cls._try_parse_int_literal(left_text)
                if left_val is not None:
                    return left_val + known_values[right_name] if op == "+" else left_val - known_values[right_name]

        return None

    @classmethod
    def _parse_enum_member_values(cls, body: str) -> List[Tuple[str, int]]:
        members: List[Tuple[str, int]] = []
        known_values: Dict[str, int] = {}
        current = -1
        for part in body.split(","):
            token = part.strip()
            if not token:
                continue
            m = re.match(r"^([A-Za-z_]\w*)(?:\s*=\s*(.+))?$", token)
            if not m:
                continue
            name = m.group(1)
            expr = m.group(2)
            if expr is not None:
                value = cls._eval_const_expr(expr, known_values)
                if value is None:
                    value = current + 1
            else:
                value = current + 1
            current = value
            known_values[name] = value
            members.append((name, value))
        return members

    def _parse_structs(self, text: str) -> None:
        pattern = re.compile(
            r"typedef\s+struct(?:\s+\w+)?\s*\{(?P<body>.*?)\}\s*(?P<name>\w+)\s*;",
            flags=re.S,
        )
        for m in pattern.finditer(text):
            body = m.group("body")
            name = m.group("name")
            fields = self._parse_struct_fields(body)
            self.struct_defs[name] = fields

    def _parse_typedef_alias(self, text: str) -> None:
        pattern = re.compile(
            r"typedef\s+(?!struct\b)(?!enum\b)(?P<src>[^;{}]+?)\s+(?P<alias>\w+)\s*;"
        )
        for m in pattern.finditer(text):
            src = self._norm_ws(self._remove_qualifiers(m.group("src")))
            alias = m.group("alias")
            self.typedef_alias[alias] = src

    def _parse_struct_fields(self, body: str) -> List[VarDecl]:
        fields: List[VarDecl] = []
        for item in body.split(";"):
            decl = item.strip()
            if not decl:
                continue
            parsed = self._parse_single_var_decl(decl)
            if parsed:
                fields.append(parsed)
        return fields

    def _parse_single_var_decl(self, decl: str) -> VarDecl | None:
        decl = self._norm_ws(self._remove_dir_tokens(decl))
        if not decl:
            return None
        array_dims = re.findall(r"\[[^\]]*\]", decl)
        decl_no_arr = re.sub(r"\[[^\]]*\]", "", decl).strip()
        m = re.search(r"([A-Za-z_]\w*)\s*$", decl_no_arr)
        if not m:
            return None
        name = m.group(1)
        prefix = decl_no_arr[: m.start()].strip()
        pointer_level = prefix.count("*")
        type_part = self._norm_ws(self._remove_qualifiers(prefix.replace("*", " ")))
        return VarDecl(
            name=name,
            type_name=type_part,
            pointer_level=pointer_level,
            array_dims=array_dims,
        )

    def parse_function_params(self, text: str, func_name: str) -> List[VarDecl]:
        text = self._strip_comments(text)
        pattern = re.compile(
            rf"[\w\s\*]+?\b{re.escape(func_name)}\s*\((?P<params>.*?)\)\s*(?:;|\{{)",
            flags=re.S,
        )
        m = pattern.search(text)
        if not m:
            raise ValueError(f"Target function not found: {func_name}")

        params_raw = self._split_params(m.group("params"))
        params: List[VarDecl] = []
        for raw in params_raw:
            normalized = self._norm_ws(self._remove_dir_tokens(raw))
            if not normalized or normalized == "void":
                continue
            parsed = self._parse_single_var_decl(normalized)
            if parsed:
                params.append(parsed)
        return params

    @staticmethod
    def _split_params(param_text: str) -> List[str]:
        out: List[str] = []
        level = 0
        start = 0
        for i, ch in enumerate(param_text):
            if ch == "(":
                level += 1
            elif ch == ")":
                level -= 1
            elif ch == "," and level == 0:
                out.append(param_text[start:i].strip())
                start = i + 1
        last = param_text[start:].strip()
        if last:
            out.append(last)
        return out

    def _resolve_array_dim_count(self, inner: str) -> int | None:
        inner = inner.strip()
        if not inner:
            return None
        if inner.isdigit():
            count = int(inner)
            return count if count >= 0 else None
        if inner in self.int_constants:
            count = int(self.int_constants[inner])
            return count if count >= 0 else None
        expr_val = self._eval_const_expr(inner, self.int_constants)
        if expr_val is not None and expr_val >= 0:
            return int(expr_val)
        return None

    def _expand_paths_with_arrays(self, base_path: str, array_dims: List[str]) -> List[str]:
        paths = [base_path]
        for dim in array_dims:
            inner = dim.strip()[1:-1].strip()
            new_paths: List[str] = []
            count = self._resolve_array_dim_count(inner)
            if count is not None:
                for p in paths:
                    for i in range(count):
                        new_paths.append(f"{p}[{i}]")
            else:
                for p in paths:
                    new_paths.append(f"{p}[*]")
            paths = new_paths
        return paths

    def resolve_alias(self, tname: str) -> str:
        cur = self._norm_ws(tname)
        seen = set()
        while cur in self.typedef_alias and cur not in seen:
            seen.add(cur)
            cur = self._norm_ws(self.typedef_alias[cur])
        return cur

    def classify_basic_type(self, tname: str) -> str:
        resolved = self.resolve_alias(tname)
        if resolved in self.struct_defs:
            return "struct"
        if resolved in self.enum_types:
            return "enum(int)"
        if resolved in BUILTIN_TYPES:
            return resolved
        return resolved

    def flatten_decl(self, decl: VarDecl) -> List[LeafVar]:
        return self._flatten(
            path=decl.name,
            type_name=decl.type_name,
            pointer_level=decl.pointer_level,
            array_dims=decl.array_dims,
            visiting=set(),
        )

    def _flatten(
        self,
        path: str,
        type_name: str,
        pointer_level: int,
        array_dims: List[str],
        visiting: Set[Tuple[str, str]],
    ) -> List[LeafVar]:
        resolved = self.resolve_alias(type_name)
        basic = self.classify_basic_type(type_name)

        if resolved in self.struct_defs:
            key = (path, resolved)
            if key in visiting:
                return []
            visiting = set(visiting)
            visiting.add(key)
            flattened: List[LeafVar] = []
            base_paths = self._expand_paths_with_arrays(path, array_dims)
            for base_path in base_paths:
                for field in self.struct_defs[resolved]:
                    child_name = f"{base_path}.{field.name}"
                    flattened.extend(
                        self._flatten(
                            path=child_name,
                            type_name=field.type_name,
                            pointer_level=field.pointer_level,
                            array_dims=field.array_dims,
                            visiting=visiting,
                        )
                    )
            return flattened

        # C string style: char[] should be handled as one variable, not element-wise array.
        if pointer_level == 0 and array_dims and basic in STRING_CHAR_TYPES:
            return [
                LeafVar(
                    path=path,
                    basic_type=STRING_BASIC_TYPE,
                    source_type=self.resolve_alias(type_name),
                )
            ]

        return [
            LeafVar(path=base_path, basic_type=basic, source_type=self.resolve_alias(type_name))
            for base_path in self._expand_paths_with_arrays(path, array_dims)
        ]


def is_known_basic_type(type_name: str) -> bool:
    return type_name in BUILTIN_TYPES or type_name in {"enum(int)", STRING_BASIC_TYPE}
