# Learnings — TAM Website

## Pitfalls
- `pytest ... | tail -1 && git commit` commits even when tests fail, because `tail` exits 0. Run `pytest -q && git commit`, or read the output first. (2026-11-23)
- The live database was patched by hand (gallery columns). Schema changes must be idempotent: check `PRAGMA table_info` before `ALTER TABLE` (see backend/migrations.py).
- Many modules read `DB_PATH` / `SETTINGS` at import time. Tests set env vars in conftest.py *before* importing backend modules and keep one DB path that is recreated per test.

## Testing
- This FastAPI version wraps included routers in `_IncludedRouter`; enumerate routes with `app.openapi()["paths"]` instead of `app.routes`.
- Fake Rekognition (tests/fakes.py): "people" are solid colours in image quadrants, so selfie matching can be tested end to end without AWS.
