"""Tiny JSON-schema validator for tool arguments.

Why not the `jsonschema` package: it is a new third-party dependency for
a PyInstaller bundle, and tool specs only ever use a small, predictable
subset of the spec - `type`, `required`, `properties`, `enum`, `items`,
`additionalProperties: false`. That subset is enough to reject the
LLM's most common mistakes (missing path, string where an int belongs)
with a message the LLM can act on. Everything else passes through.
"""
from __future__ import annotations

from typing import Any


def validate(schema: dict[str, Any], value: Any) -> list[str]:
    """Return a list of human-readable problems; empty means valid."""
    errors: list[str] = []
    _check(schema, value, "", errors)
    return errors


def _check(schema: dict[str, Any], value: Any, path: str, errors: list[str]) -> None:
    where = f"'{path}'" if path else "arguments"

    declared = schema.get("type")
    if declared is not None:
        allowed = [declared] if isinstance(declared, str) else list(declared)
        if not any(_is_type(value, t) for t in allowed):
            errors.append(f"{where} must be {' or '.join(allowed)}, got {_type_name(value)}")
            return

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where} must be one of {list(schema['enum'])!r}, got {value!r}")

    if isinstance(value, dict):
        props: dict[str, Any] = schema.get("properties") or {}
        for key in schema.get("required") or []:
            if key not in value:
                errors.append(f"missing required argument '{_join(path, key)}'")
        for key, sub in props.items():
            if key in value and isinstance(sub, dict):
                _check(sub, value[key], _join(path, key), errors)
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    errors.append(f"unexpected argument '{_join(path, key)}'")

    if isinstance(value, (list, tuple)) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            _check(schema["items"], item, f"{path}[{i}]", errors)


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _is_type(value: Any, name: str) -> bool:
    if name == "string":
        return isinstance(value, str)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "integer":
        if isinstance(value, bool):
            return False
        return isinstance(value, int) or (isinstance(value, float) and value.is_integer())
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "array":
        return isinstance(value, (list, tuple))
    if name == "object":
        return isinstance(value, dict)
    if name == "null":
        return value is None
    return True                                  # unknown type keyword: don't block


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__
