"""Schema validation helpers for structured agent results."""

from collections.abc import Mapping, Sequence
import re
from typing import Any


def validate_result_payload(payload: Any, schema: Mapping[str, Any]) -> str | None:
    """Return ``None`` when ``payload`` satisfies ``schema``, else a reason.

    This intentionally implements the constrained JSON-Schema subset used by
    doeff-agentd: enough for result contracts without adding a heavyweight
    dependency to doeff-agents.
    """
    try:
        _validate(payload, schema, "result")
    except ValueError as exc:
        return str(exc)
    return None


def _validate(instance: Any, schema: Mapping[str, Any], loc: str) -> None:  # noqa: PLR0912, PLR0915 - baseline cleanup keeps existing control flow unchanged
    if not isinstance(schema, Mapping):
        raise ValueError(f"schema at '{loc}' is not an object")

    if "oneOf" in schema:
        branches = schema["oneOf"]
        if not isinstance(branches, Sequence) or isinstance(branches, (str, bytes)):
            raise ValueError(f"'oneOf' at '{loc}' must be an array")
        matches = 0
        errors: list[str] = []
        for index, branch in enumerate(branches):
            try:
                _validate(instance, branch, loc)
                matches += 1
            except ValueError as exc:
                errors.append(f"variant {index}: {exc}")
        if matches == 0:
            raise ValueError(
                f"value at '{loc}' matched none of the {len(branches)} allowed variants: "
                + "; ".join(errors)
            )
        if matches > 1:
            raise ValueError(f"value at '{loc}' matched {matches} variants")

    if "const" in schema and instance != schema["const"]:
        raise ValueError(f"'{loc}' must equal {schema['const']!r}")

    expected_type = schema.get("type")
    if expected_type is not None and not _matches_type(instance, expected_type):
        raise ValueError(f"'{loc}' must be of type {expected_type}")

    enum_values = schema.get("enum")
    if enum_values is not None:
        if not isinstance(enum_values, Sequence) or isinstance(enum_values, (str, bytes)):
            raise ValueError(f"'enum' at '{loc}' must be an array")
        if instance not in enum_values:
            raise ValueError(f"'{loc}' must be one of {list(enum_values)!r}")

    if "minLength" in schema and isinstance(instance, str):
        min_length = int(schema["minLength"])
        if len(instance) < min_length:
            raise ValueError(f"'{loc}' must be at least length {min_length}")

    pattern = schema.get("pattern")
    if pattern is not None and isinstance(instance, str):
        if not isinstance(pattern, str):
            raise ValueError(f"'pattern' at '{loc}' must be a string")
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"'pattern' at '{loc}' is invalid: {exc}") from exc
        if not compiled.search(instance):
            raise ValueError(f"'{loc}' must match pattern {pattern!r}")

    required = schema.get("required")
    if required is not None:
        if not isinstance(required, Sequence) or isinstance(required, (str, bytes)):
            raise ValueError(f"'required' at '{loc}' must be an array")
        if not isinstance(instance, Mapping):
            raise ValueError(f"'{loc}' must be an object to check required fields")
        for key in required:
            if not isinstance(key, str):
                raise ValueError(f"'required' at '{loc}' contains a non-string key")
            if key not in instance:
                raise ValueError(f"'{loc}' is missing required field '{key}'")

    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, Mapping):
            raise ValueError(f"'properties' at '{loc}' must be an object")
        if isinstance(instance, Mapping):
            for key, child_schema in properties.items():
                if key in instance:
                    _validate(instance[key], child_schema, f"{loc}.{key}")

    if schema.get("additionalProperties") is False and isinstance(instance, Mapping):
        allowed = set((properties or {}).keys()) if isinstance(properties, Mapping) else set()
        extra = sorted(set(instance.keys()) - allowed)
        if extra:
            raise ValueError(f"'{loc}' has unexpected fields {extra!r}")

    items = schema.get("items")
    if (
        items is not None
        and isinstance(instance, Sequence)
        and not isinstance(
            instance,
            (str, bytes),
        )
    ):
        for index, item in enumerate(instance):
            _validate(item, items, f"{loc}[{index}]")


def _matches_type(instance: Any, expected_type: Any) -> bool:  # noqa: PLR0911 - baseline cleanup keeps existing control flow unchanged
    if isinstance(expected_type, Sequence) and not isinstance(expected_type, (str, bytes)):
        return any(_matches_type(instance, item) for item in expected_type)
    if expected_type == "object":
        return isinstance(instance, Mapping)
    if expected_type == "array":
        return isinstance(instance, Sequence) and not isinstance(instance, (str, bytes))
    if expected_type == "string":
        return isinstance(instance, str)
    if expected_type == "number":
        return isinstance(instance, int | float) and not isinstance(instance, bool)
    if expected_type == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected_type == "boolean":
        return isinstance(instance, bool)
    if expected_type == "null":
        return instance is None
    raise ValueError(f"unsupported schema type {expected_type!r}")
