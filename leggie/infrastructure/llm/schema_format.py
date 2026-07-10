"""JSON Schema utilities — convert Pydantic models to OpenRouter json_schema format.

Provides a helper to generate the strict JSON Schema dict that OpenRouter
expects for ``response_format={"type": "json_schema", ...}`` calls.

Usage::

    schema_dict = pydantic_to_json_schema(LensFindings)
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "LensFindings", "strict": True,
                        "schema": schema_dict},
    }
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def pydantic_to_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Build a strict JSON Schema from a Pydantic v2 model.

    The output is compatible with OpenRouter's ``json_schema`` strict mode
    (``additionalProperties: false``, every field listed in ``required``,
    ``$defs`` inlined).

    Args:
        model: A Pydantic ``BaseModel`` subclass.

    Returns:
        A JSON Schema ``dict`` ready to embed in
        ``response_format.json_schema.schema``.
    """
    raw = model.model_json_schema()
    return _make_strict(raw, raw)


def _make_strict(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    """Recursively turn a raw Pydantic JSON Schema into a strict variant.

    * Inlines ``$ref`` references from the root ``$defs``.
    * Adds ``additionalProperties: false`` to every ``object`` type.
    * Ensures all property names are listed in ``required``.
    * Strips Pydantic metadata keys (``title``, ``description``) that some
      providers may reject or that add no constraint.
    """
    # Resolve $ref
    if "$ref" in schema:
        ref_path = schema["$ref"]  # e.g. "#/$defs/IRACCandidate"
        def_key = ref_path.rsplit("/", 1)[-1]  # "IRACCandidate"
        defs = root.get("$defs", {})
        if def_key in defs:
            resolved = dict(defs[def_key])
            resolved.pop("title", None)
            return _make_strict(resolved, root)

    result = dict(schema)
    result.pop("title", None)
    result.pop("description", None)

    schema_type = result.get("type")

    if schema_type == "object":
        result["additionalProperties"] = False
        props: dict[str, Any] = result.get("properties", {})
        # Recursively process nested properties
        for key, val in props.items():
            if isinstance(val, dict):
                props[key] = _make_strict(val, root)
        # All property names become required for strict mode
        result["required"] = sorted(props.keys())
        result["properties"] = props

    elif schema_type == "array":
        items = result.get("items")
        if isinstance(items, dict):
            result["items"] = _make_strict(items, root)

    # Remove $defs from the result (only needed at root for refs)
    result.pop("$defs", None)

    return result
