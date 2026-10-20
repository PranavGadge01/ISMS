# Test Plan

## Verified by the current automated suite

The 20-test pytest suite currently exercises selected paths, not every registered route:

- Six-role read/write permission samples, including Auditor read-only behavior and denial of login-log access.
- Purchase order creation, approval behavior, and Warehouse Staff adjustment permissions.
- Stock adjustment, transfer/count flows, ledger consistency, receiving, sales, low-stock alerts, and a return in one integrated workflow.
- Duplicate SKU handling, sale rejection for insufficient stock, receipt stock increases, return quantity limits, and five-failure login lockout.
- Regression cases for cross-user returns, server-controlled sale prices, draft ownership and price override enforcement, concurrent attempts to sell the last unit, and idempotent sale/receipt/return retries.

## Coverage still required

1. The API catalog lists 64 API method/path pairs. Add route-specific tests for every pair, including unauthenticated access, role denial, missing/invalid fields, invalid IDs, boundaries, persistence, and unchanged state after failures. Twenty-eight pairs currently have at least one named pytest reference in the catalog; this is not full endpoint coverage.
2. Test the 51 legacy Jinja route rules and four authentication route rules for authorization parity with the JSON API.
3. Add explicit cases for PO self-approval with multiple/sole approvers, approval threshold boundaries, rejection/re-submission, illegal cancellation, and over-delivery authorization.
4. Add failure-injection tests proving sales and receipts roll back all stock, ledger, receipt, and transaction changes on a mid-operation error. Exercise concurrent requests using the same idempotency key and verify behavior after client restarts.
5. Assert audit-event persistence and actor/target details for each important action. Coverage for selected audit access exists; complete event coverage and an audit UI do not.
6. Test alert deduplication, clearing and re-triggering; password-reset token expiry/one-use, idle-session expiry, logout, and production reset delivery behavior.
7. Add frontend browser E2E against the real Flask backend, including each role's navigation and the PO, receipt, sales, return, alert, and report journeys. The current sandbox blocks loopback socket access (Windows error 10013), so live HTTP/browser E2E was not possible here.
8. Add clean-database and upgrade-path tests. There is no migration framework or migration history; startup uses `create_all()` and handwritten compatibility `ALTER TABLE` statements.
9. Add stress tests for concurrent receipts, concurrent returns, and lock-retry behavior. SQLite does not enforce `with_for_update()` row locks and lock errors are not retried.

## Reproducible commands

From the repository root in PowerShell, with Python 3.10+ and Node.js 18+:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

The app does not load dotenv files. For local development, set `ISMS_ENV=development` if desired; a random per-process `SECRET_KEY` is generated when none is provided. `python app.py` creates `instance/isms.db` tables and development demo data on first run. For a production-style configuration, set `ISMS_ENV=production` and provide `SECRET_KEY` through the deployment environment; production startup does not seed demo users. There is no separate seed CLI.

Start Flask from the repository root:

```powershell
python app.py
```

In another terminal, install and run the React development server:

```powershell
cd frontend
pnpm install --frozen-lockfile
pnpm run dev
```

For the same-origin production-style local app, run `pnpm run build` in `frontend/`, return to the repository root, and run `python app.py`.

Run the backend suite from the repository root:

```powershell
python -m pytest -q --basetemp .pytest_tmp
```

The sandbox verification used `PYTHONPATH=.venv\packages` because packages were installed into that ignored workspace directory. The frontend build command is `pnpm run build` from `frontend/`.