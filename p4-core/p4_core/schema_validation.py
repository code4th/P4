from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SchemaValidationResult:
    ok: bool
    errors: tuple[str, ...]


def validate_json_schema(value: Any, schema: dict[str, Any], *, path: str = "$") -> SchemaValidationResult:
    errors: list[str] = []
    _validate(value, schema, path=path, errors=errors)
    return SchemaValidationResult(ok=not errors, errors=tuple(errors))


def _validate(value: Any, schema: dict[str, Any], *, path: str, errors: list[str]) -> None:
    schema_type = schema.get("type")
    if schema_type is not None and not _type_matches(value, str(schema_type)):
        errors.append(f"{path}: expected {schema_type}, got {type(value).__name__}")
        return

    enum = schema.get("enum")
    if enum is not None and value not in enum:
        errors.append(f"{path}: value {value!r} is not in enum {list(enum)!r}")

    if isinstance(value, str):
        max_length = schema.get("maxLength")
        if isinstance(max_length, int) and len(value) > max_length:
            errors.append(f"{path}: string length {len(value)} exceeds maxLength {max_length}")

    if isinstance(value, list):
        max_items = schema.get("maxItems")
        if isinstance(max_items, int) and len(value) > max_items:
            errors.append(f"{path}: array length {len(value)} exceeds maxItems {max_items}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate(item, item_schema, path=f"{path}[{index}]", errors=errors)

    if isinstance(value, dict):
        required = schema.get("required") or []
        for key in required:
            if key not in value:
                errors.append(f"{path}: missing required key {key!r}")
        properties = schema.get("properties") or {}
        if not isinstance(properties, dict):
            properties = {}
        additional = schema.get("additionalProperties", True)
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in properties and isinstance(properties[key], dict):
                _validate(child, properties[key], path=child_path, errors=errors)
            elif additional is False:
                errors.append(f"{child_path}: additional property is not allowed")
            elif isinstance(additional, dict):
                _validate(child, additional, path=child_path, errors=errors)


def _type_matches(value: Any, schema_type: str) -> bool:
    if schema_type == "object":
        return isinstance(value, dict)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return (isinstance(value, int) or isinstance(value, float)) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "null":
        return value is None
    raise AssertionError(f"unsupported schema type: {schema_type}")
