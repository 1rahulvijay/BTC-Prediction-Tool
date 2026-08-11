# Backend Tests

This directory contains top-level backend regression and invariant tests. It is
not imported by the running application.

Run the complete Python suite from the repository root:

```powershell
python -m pytest -q
```

Run one direct-script invariant with:

```powershell
python backend/tests/test_launcher_integrity.py
```

`_bootstrap.py` preserves the historical direct-script import behavior while
keeping test code outside the core runtime module directory.
