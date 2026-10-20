# ISMS Implementation Audit

**Source reviewed:** `Shrey-Sawant/ISMS`, default branch `main`, snapshot commit `7d031beb2a8b6ff3044e54a58d1d7d6f6d9949e7` (2026-10-20). The GitHub connector authenticated as `PranavGadge01` and provided file contents. This workspace is a source snapshot, not a Git clone; its `.git` metadata is read-only. The connected GitHub tool set has no fork operation. No source or changes were written to the original repository.

## Stack and shape

- Flask 3 / Flask-SQLAlchemy / SQLite backend, with legacy Jinja routes and a JSON API in `api/__init__.py`.
- React 18 / Vite frontend in `frontend/`, using Axios and React Context.
- SQLAlchemy models in `models.py`; stock and transaction behavior in `services.py`; role permission map in `permissions.py`.
- Existing tests: three pytest modules; no migrations directory or CI workflow was present in the source tree.

## Confirmed defects and changes made locally

| Finding | Evidence | Local change |
|---|---|---|
| Fixed development session secret was the fallback (`isms-development-key-change-me`) and demo seed ran at startup in every environment. | `app.py`, `seed.py` | Generate a random local key, require `SECRET_KEY` in production, disable demo seeding in production, and set safer cookie flags/security headers. |
| Sales service trusted a caller-provided unit price. | `services.py` | Recalculate unit prices from active persisted products and validate payment method, quantity, and discount bounds. |
| Stock adjustment used read-modify-write with no database-side nonnegative guard. | `services.py` | Apply each delta with a conditional SQL update requiring the resulting balance to stay nonnegative. |
| Sales staff could return another staff member’s sale through the JSON API and legacy invoice route. | `api/__init__.py`, `routes/main.py` | Scope return requests to the owner for Sales Staff and return 404 for another employee’s record. |
| Sales staff legacy list and invoice routes showed other employees’ sales. | `routes/main.py` | Scope the legacy sales list and invoice route to the signed-in salesperson. |
| Draft PO line actions did not verify draft ownership; procurement could supply an arbitrary unit price. | `api/__init__.py`, `routes/main.py` | Restrict line edits/submission to the creator and require explicit `po.price.override` for price overrides (Administrator only by default). |
| Legacy warehouse receipt accepted over-delivery based only on a submitted checkbox. | `routes/main.py` | Restrict explicit over-delivery acceptance to Administrator or Procurement Officer; shared service still validates quantities/state. |
| Legacy warehouse adjustment routes could bypass the large-adjustment approval threshold. | `services.py`, `routes/main.py` | Route large Warehouse Staff adjustments to pending approval and enforce the threshold in the direct adjustment service. |
| There was no business audit-log model or endpoint; Auditor access included security login logs. | `models.py`, `permissions.py`, `api/__init__.py` | Add append-only-by-application `AuditLog` records for stock, sale/return, PO receipt/transitions, user and settings changes; add a business audit read endpoint; remove login-log access from Auditor. |
| Settings API accepted invalid/non-finite thresholds and rates. | `api/__init__.py` | Validate numeric setting ranges and text lengths before persistence. |

## Remaining confirmed limitations

- No Alembic migration history. `app.py` runs `create_all()` and handwritten `ALTER TABLE` statements at startup. The new audit table is created by `create_all`; existing-column changes remain untracked.
- Monetary values use SQLAlchemy `Float`; rounding is not consistently defined and should move to decimal-safe storage through a migration.
- `StockLevel` has global product reorder thresholds, not warehouse-specific thresholds.
- Sale, receipt, and return APIs/services now require an idempotency key, store the request fingerprint and resulting resource in the same transaction, replay the original result for an identical retry, and reject a reused key with a different payload. UI clients generate keys for form submissions. Concurrent same-key stress and key retention across browser restarts remain unverified.
- SQLite `with_for_update()` does not provide row locking; the conditional update prevents negative balances, but transient lock errors are not retried.
- No payment gateway, email delivery, or generated PDF engine is present. The README documents local reset links, in-app notification, and browser print behavior.
- Password reset in production returns a generic message but cannot deliver a link without a configured email/reset delivery provider. Demo reset links remain local-development-only.
- Some legacy Jinja routes still authorize by role list rather than the centralized action permission map. API and legacy behavior need a route-by-route authorization parity pass.
- Audit events were added for core stock/sale/PO and selected admin changes, but not every master-data edit, denied request, or export. Audit UI is not yet added.
- Authentication has lockout and idle timeout, but no configurable rate limiter, password reset delivery provider, CSRF token integration, or session revocation store.

## GitHub and verification status

On 2026-10-20, the authenticated GitHub API verified `PranavGadge01/ISMS` exists, is writable by the connected account, and has `main` at the same head as upstream, `7d031beb2a8b6ff3044e54a58d1d7d6f6d9949e7`. The implementation branch was created from that exact base; source hardening, regression tests, and documentation/configuration are published there in four grouped commits. No upstream write was attempted. The local workspace contains 71 files: all 63 upstream paths plus eight additions, with ten substantive changes among the shared paths; the remaining shared files match upstream content apart from a final newline. The local `.git` repository still has no commits or remotes and all workspace files are untracked on `master`; adding remotes failed because `.git/config` is read-only (`Permission denied`). A safety ZIP is available at `C:\Users\prana\.codex\visualizations\2026\10\20\01a157cd-5cf0-7f13-a52d-21980acd567d\ISMS-safety-snapshot.zip`.

The frontend build passed. The latest backend suite passed 24 tests, including regressions for price trust, PO ownership/price overrides, cross-user returns, concurrent sales, and sale/receipt/return idempotency. See `TEST_RESULTS.md` for the run record and warnings.