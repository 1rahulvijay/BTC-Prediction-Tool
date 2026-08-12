"""One persisted decision cycle may contain one serving release, never two."""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path

_bootstrap_runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))


def main() -> int:
    source = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    assert "_cycle_release_generation = SERVING_RELEASE_GENERATION" in source
    assert source.count("SERVING_RELEASE_GENERATION != _cycle_release_generation") >= 2
    assert "Discarding inference cycle: release changed" in source
    for reason in (
        "restore-full-refit-shadow",
        "install-full-refit-shadow",
        "activate-bootstrap-candidate",
        "promote-full-refit-shadow",
    ):
        assert f'_mark_serving_release_change("{reason}")' in source, reason
    print("release-cycle-atomicity: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
