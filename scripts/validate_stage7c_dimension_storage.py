#!/usr/bin/env python3
"""Regression guard for Stage 7C dimension persistence.

This deliberately avoids importing ``classify_dual_lens`` and therefore has
no dependency on the model, llama.cpp, or Supabase.  It executes only the
database-row adapter and verifies the exact singleton-resume state that caused
W35 to fail is converted to the live table's accepted representation.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CLASSIFIER = ROOT / "scripts" / "classify_dual_lens.py"


def load_dimension_writer():
    source = CLASSIFIER.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(CLASSIFIER))
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "dimension_row_for_storage"
        ),
        None,
    )
    if function is None:
        raise SystemExit(
            "STAGE 7C DIMENSION CONTRACT ERROR: "
            "dimension_row_for_storage is missing."
        )

    insert_function = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "insert_classification"
        ),
        None,
    )
    uses_writer = insert_function is not None and any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "dimension_row_for_storage"
        for node in ast.walk(insert_function)
    )
    if not uses_writer:
        raise SystemExit(
            "STAGE 7C DIMENSION CONTRACT ERROR: "
            "insert_classification bypasses the dimension storage adapter."
        )

    namespace: dict[str, Any] = {"Any": Any}
    module = ast.Module(body=[function], type_ignores=[])
    exec(
        compile(
            ast.fix_missing_locations(module),
            "<stage7c-dimension-contract>",
            "exec",
        ),
        namespace,
    )
    return namespace["dimension_row_for_storage"]


def main() -> int:
    make_row = load_dimension_writer()

    # Exact state after saved coverage is read and reused for a singleton
    # event: direction has become display-oriented ``not_present``.
    resumed_absent = make_row(
        classification_id="test-classification",
        dimension="agentic",
        item={
            "present": False,
            "direction": "not_present",
            "degree": 0,
            "confidence": 0.0,
            "reasoning": "No mention of autonomy, control, ownership, or decision authority.",
        },
    )
    expected_absent = {
        "lens_classification_id": "test-classification",
        "dimension": "agentic",
        "present": False,
        "direction": None,
        "degree": 0,
        "confidence": 0.0,
        "reasoning": "No mention of autonomy, control, ownership, or decision authority.",
    }
    if resumed_absent != expected_absent:
        raise SystemExit(
            "STAGE 7C DIMENSION CONTRACT ERROR: an absent singleton "
            f"would be written as {resumed_absent!r}, expected {expected_absent!r}."
        )

    present = make_row(
        classification_id="test-present",
        dimension="capability",
        item={
            "present": True,
            "direction": "expanding",
            "degree": 2,
            "confidence": 0.85,
            "reasoning": "A concrete new capability is available.",
        },
    )
    if present["direction"] != "expanding" or present["degree"] != 2:
        raise SystemExit(
            "STAGE 7C DIMENSION CONTRACT ERROR: a present dimension was altered."
        )

    print("Stage 7C dimension storage contract passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
