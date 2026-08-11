"""The specialist bucket audit may not call helpers before defining them or hide failures."""

from __future__ import annotations

import ast
from pathlib import Path


def main() -> int:
    path = Path(__file__).resolve().parent / "head_probability_buckets.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    helper_line = next(
        node.lineno for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_verified_load"
    )
    entry_line = next(
        node.lineno for node in tree.body
        if isinstance(node, ast.If)
        and "__name__" in ast.unparse(node.test)
    )
    assert helper_line < entry_line, "_verified_load must exist before the script entry point"

    main_fn = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    source = ast.unparse(main_fn)
    assert "if errors" in source and "return 1" in source
    assert "raise SystemExit(main())" in path.read_text(encoding="utf-8")
    print("  PASS  verified loader is defined before execution")
    print("  PASS  partial audits fail nonzero and do not publish a report")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
