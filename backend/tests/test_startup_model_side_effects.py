"""Importing the serving model contract must not fit estimators or initialize CUDA."""
from __future__ import annotations

import ast
import runpy as _bootstrap_runpy
from pathlib import Path

_bootstrap_runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))

import model  # noqa: E402


def main() -> int:
    assert model._TRAINING_DEVICES_PROBED is False
    tree = ast.parse(Path(model.__file__).read_text(encoding="utf-8"))
    top_level_fit_lines = []
    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for node in ast.walk(statement):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "fit"
            ):
                top_level_fit_lines.append(node.lineno)
    assert not top_level_fit_lines, top_level_fit_lines
    print("startup-model-side-effects: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
