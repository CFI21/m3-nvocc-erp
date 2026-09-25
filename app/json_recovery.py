from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def extract_array_objects(text: str, key: str) -> list[dict[str, Any]]:
    """Recover complete object items from a named array in truncated JSON.

    Only individually valid JSON objects that are fully present before the
    truncation point are returned. Missing tail content is never invented.
    """
    m = re.search(r'"' + '"' + r'{}' + '"' + r'\s*:\s*\['.format(re.escape(key)), text)
    if not m:
        return []
    i = m.end()
    out: list[dict[str, Any]] = []
    in_string = False
    escape = False
    depth = 0
    start = None

    while i < len(text):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif ch == '}':
                if depth:
                    depth -= 1
                    if depth == 0 and start is not None:
                        fragment = text[start:i + 1]
                        try:
                            value = json.loads(fragment)
                        except json.JSONDecodeError:
                            pass
                        else:
                            if isinstance(value, dict):
                                out.append(value)
                        start = None
            elif ch == ']' and depth == 0:
                break
        i += 1
    return out


def load_json_or_recover_arrays(path: Path, keys: list[str], scalars: dict[str, Any] | None = None) -> tuple[dict[str, Any], bool]:
    text = path.read_text()
    try:
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError(f'{path.name} must contain a JSON object')
        return value, False
    except json.JSONDecodeError:
        recovered: dict[str, Any] = dict(scalars or {})
        for key in keys:
            recovered[key] = extract_array_objects(text, key)
        return recovered, True


def recover_string_scalar(text: str, key: str, default: str = '') -> str:
    m = re.search(r'"' + '"' + r'{}' + '"' + r'\s*:\s*"([^"]*)"'.format(re.escape(key)), text)
    return m.group(1) if m else default


def recover_int_scalar(text: str, key: str, default: int = 0) -> int:
    m = re.search(r'"' + '"' + r'{}' + '"' + r'\s*:\s*(\d+)'.format(re.escape(key)), text)
    return int(m.group(1)) if m else default
