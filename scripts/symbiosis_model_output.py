"""The model transport contract, separate from the research codebook version.

Invalid or truncated generations are operational failures, never observations
of insufficient source evidence. No model reasoning or article text is logged.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

TRANSPORT_VERSION = "symbiosis_json_v2"
CONFIDENCE_VALUES = [round(index / 20, 2) for index in range(21)]
PATTERNS = ("mutualism", "ai_benefiting_parasitism", "human_benefiting_parasitism", "competition")


class ModelOutputError(RuntimeError):
    def __init__(self, message: str, diagnostics: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or []


def enum(*values: str) -> dict[str, Any]:
    return {"type": "string", "enum": list(values)}


PROPERTIES = {
    "ai_relevant": {"type": "boolean"},
    "evidence_status": enum("sufficient", "partial", "insufficient"),
    "relational_signal": enum("complete", "human_only", "ai_only", "none", "unclear"),
    "human_experience_type": enum("extension", "expansion", "restriction", "reduction", "neutral", "unclear"),
    "ai_expressive_role": enum("ai_extension", "ai_expansion", "ai_restriction", "ai_reduction", "neutral", "unclear"),
    **{key: {"type": "string"} for key in (
        "human_reasoning", "ai_reasoning", "summary", "topic", "public_takeaway", "people_evidence"
    )},
    # llama.cpp's number grammar does not enforce minimum/maximum. A numeric
    # enum makes the diagnostic 0..1 scale a real decoding constraint.
    "confidence": {"type": "number", "enum": CONFIDENCE_VALUES, "minimum": 0, "maximum": 1},
    "geographic_scope": enum("country", "multi_country", "global", "unclear"),
    "country_iso3s": {"type": "array", "items": {"type": "string", "pattern": "^[A-Z]{3}$"}},
    "relationship_patterns": {
        "type": "object", "properties": {key: {"type": "boolean"} for key in PATTERNS},
        "required": list(PATTERNS), "additionalProperties": False,
    },
    "distribution_signal": enum("broadly_shared", "unequal", "not_shown", "unclear"),
}
RESPONSE_SCHEMA = {
    "type": "object", "properties": PROPERTIES,
    "required": list(PROPERTIES), "additionalProperties": False,
}


def extract_json(text: str) -> dict[str, Any]:
    # Old servers can include a thinking wrapper even when thinking is off.
    # Never search a reasoning trace for something that looks like a result.
    raw = text.strip()
    if "</think>" in raw:
        raw = raw.rsplit("</think>", 1)[1].strip()
    if "<think>" in raw:
        raise ModelOutputError("Model returned reasoning without a final answer.")
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", raw)
    if fenced:
        raw = fenced.group(1)
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ModelOutputError("Model final answer is not a complete JSON object.") from exc
    if not isinstance(payload, dict):
        raise ModelOutputError("Model final answer must be a JSON object.")
    return payload


def require_result_fields(payload: dict[str, Any]) -> None:
    missing = set(PROPERTIES) - payload.keys()
    if missing:
        raise ModelOutputError("Model result is missing required fields: " + ", ".join(sorted(missing)))
    for key, spec in PROPERTIES.items():
        value = payload[key]
        kind = spec["type"]
        valid = (
            (kind == "boolean" and type(value) is bool)
            or (kind == "string" and isinstance(value, str) and bool(value.strip()))
            or (kind == "number" and type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1)
            or (kind == "array" and isinstance(value, list) and all(isinstance(v, str) and re.fullmatch(r"[A-Z]{3}", v) for v in value))
            or (kind == "object" and isinstance(value, dict) and set(value) == set(PATTERNS) and all(type(v) is bool for v in value.values()))
        )
        if not valid or ("enum" in spec and value not in spec["enum"]):
            raise ModelOutputError(f"Model result has an invalid {key} field.")


def require_schema(value: Any, schema: dict[str, Any], path: str = "result") -> None:
    """Validate the nested Stage 7C schema before its normalizer can fill gaps."""
    if "oneOf" in schema:
        for option in schema["oneOf"]:
            try:
                require_schema(value, option, path)
                return
            except ModelOutputError:
                pass
        raise ModelOutputError(f"Inconsistent model object: {path}.")
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int}
    valid = type(value) in (int, float) and math.isfinite(value) if kind == "number" else type(value) is types[kind]
    if not valid or ("enum" in schema and value not in schema["enum"]):
        raise ModelOutputError(f"Invalid model field: {path}.")
    if kind in {"number", "integer"} and not schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf):
        raise ModelOutputError(f"Out-of-range model field: {path} ({value}).")
    if kind == "object":
        if set(schema.get("required", [])) - value.keys():
            raise ModelOutputError(f"Incomplete model object: {path}.")
        for key, spec in schema.get("properties", {}).items():
            if key in value: require_schema(value[key], spec, f"{path}.{key}")
    if kind == "array":
        for item in value: require_schema(item, schema["items"], path)


def dimension_schema() -> dict[str, Any]:
    """Constrain presence, direction and degree together during decoding."""
    options = []
    for present in (False, True):
        properties = {
            "present": {"type": "boolean", "enum": [present]},
            "direction": enum("expanding", "contracting", "mixed", "unclear") if present else enum("not_present"),
            "degree": {"type": "integer", "enum": [1, 2, 3] if present else [0]},
            "confidence": {"type": "number", "enum": CONFIDENCE_VALUES},
            "reasoning": {"type": "string"},
        }
        options.append({"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False})
    return {"oneOf": options}


def response_result(response: dict[str, Any], schema: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    choices = response.get("choices") or []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = choice.get("message") or {}
    content = message.get("content") if isinstance(message, dict) else None
    diagnostics = {
        "finish_reason": choice.get("finish_reason"),
        "content_characters": len(content) if isinstance(content, str) else 0,
        "completion_tokens": (response.get("usage") or {}).get("completion_tokens"),
    }
    try:
        if choice.get("finish_reason") == "length":
            raise ModelOutputError("Model generation reached its token limit.")
        if not isinstance(content, str) or not content.strip():
            raise ModelOutputError("Model returned no final answer.")
        payload = extract_json(content)
        if schema is None:
            require_result_fields(payload)
        else:
            require_schema(payload, schema)
    except ModelOutputError as exc:
        raise ModelOutputError(str(exc), [diagnostics]) from exc
    return payload, diagnostics
