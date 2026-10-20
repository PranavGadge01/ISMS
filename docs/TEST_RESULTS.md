# Test Results

**Date:** 2026-10-20 (Asia/Calcutta)  
**Source base:** fork and upstream `main` at `7d031beb2a8b6ff3044e54a58d1d7d6f6d9949e7`. The local worktree remains untracked because its `.git` metadata is read-only; the implementation is published in grouped commits to the fork branch `codex/isms-full-system-implementation`.

## Environment

- Windows, Python 3.14.0, Flask 3.1.3, Flask-SQLAlchemy 3.1.1, SQLAlchemy 2.1.5, pytest 9.1.1.
- Node.js 24.12.0, pnpm 11.25.0, Vite 6.4.4 (installed from the committed lockfile).
- Pytest used isolated SQLite files beneath the workspace via `--basetemp`; the local server used a separate ignored development database.

## Commands executed

| Command | Result |
|---|---|
| `python -m pytest -q` before dependencies | Collection failed: Flask and Werkzeug unavailable. |
| `npm run build` before dependency install | Failed: Vite unavailable. |
| `python -m pip install --target .venv\packages -r requirements.txt` | Passed; installed Flask 3.1.3, Flask-SQLAlchemy 3.1.1, pytest 9.1.1 and dependencies in ignored local workspace. |
| `pnpm install --frozen-lockfile` in `frontend/` | Passed; installed 56 packages from the committed lockfile. |
| `pnpm run build` in `frontend/` | Passed with Vite 6.4.4; generated `frontend/dist`. Re-run on 2026-10-20: passed in 5.31 s. |
| `python -m compileall -q app.py auth.py models.py permissions.py seed.py services.py api routes tests` | Re-run on 2026-10-20: passed. |
| `python -m pytest -x -q --basetemp .pytest_tmp2` with `PYTHONPATH=.venv\packages` | **16 passed** in 142.08 s. This was the pre-addition test set after the first implementation changes. |
| `python -m pytest -q --basetemp .pytest_tmp3 <four regression tests>` with `PYTHONPATH=.venv\packages` | **4 passed** in 29.31 s. |
| `python -m pytest -q --basetemp .pytest_tmp_full` with `PYTHONPATH=.venv\packages` | Previous run: **20 passed** in 144.72 s. |
| `python -m pytest -q --basetemp .pytest_tmp_user_rerun` with `PYTHONPATH=.venv\packages` | Re-run on 2026-10-20: **20 passed** in 165.65 s; 3,472 warnings. |
| `python -m pytest -q --basetemp .pytest_tmp_final` with `PYTHONPATH=.venv\packages` | Re-run on 2026-10-20 after durable idempotency changes: **24 passed**, 4,147 warnings. |
| `python -m compileall -q app.py auth.py models.py permissions.py seed.py services.py api routes tests` | Re-run after idempotency changes: passed. |
| `pnpm run build` in `frontend/` | Re-run after idempotency changes: passed with Vite 6.4.4 in 2.83 s. |
| `python app.py` with `PORT=5001`, `ISMS_ENV=development`, `PYTHONPATH=.venv\packages`; then `Invoke-WebRequest http://127.0.0.1:5001/` | Flask started successfully; HTTP probe failed with Windows socket error 10013 (socket access forbidden by sandbox). Server stopped after the probe. |

The first pytest rerun without an in-workspace basetemp hit Windows sandbox temp-directory permissions. A subsequent run against a reused temp path also hit Windows SQLite file-lock cleanup errors. The passing run used a fresh workspace-local basetemp.

## Current coverage status

- API routes discovered: **64 method/route pairs** in `api/__init__.py`.
- Full endpoint coverage: **not achieved**. The API catalog lists 64 method/path pairs; 28 have at least one named pytest reference and 36 have no named reference. A reference does not mean all failure cases are covered.
- Role-permission results: the existing tests cover selected reads/writes for all six roles and Auditor read-only behavior; added Auditor business-audit/security-log assertions were included in the passing 16-test run.
- Frontend build: passed.
- Frontend browser E2E, lint, type check, clean production migration, and concurrent stress tests: not run.
- Regression tests for cross-user returns, server price authority, draft ownership/price overrides, concurrent last-unit sales, and sale/receipt/return idempotency passed in the latest full-suite rerun. API sale tests also verify the required idempotency key, successful replay, and conflicting-payload rejection.
- The server process started but a live HTTP/UI smoke check could not reach loopback from this sandbox. No browser E2E verification is claimed.

## Known warnings and limits

The latest full suite emitted 4,147 warnings, mainly from `datetime.utcnow()` deprecations and SQLAlchemy `Query.get()` use. The implementation is not ready to claim full security, endpoint, or workflow completion. Known remaining defects and setup limitations are listed in `docs/IMPLEMENTATION_AUDIT.md`.