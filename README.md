# Inventory and Stock Management System

ISMS is a Flask + SQLite backend with a React 18/Vite single-page frontend. The server-rendered Jinja pages and their existing routes remain available for fallback and legacy tests. Flask serves the React production build from `frontend/dist` and the JSON API under `/api`.

## Prerequisites

- Python 3.10 or later
- Node.js 18 or later and pnpm 9 (lockfile version 9)

## Development

Create the environment and install Python packages:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Terminal 1, run the Flask API:

```powershell
python app.py
```

Terminal 2, install and run React with its `/api` proxy to Flask:

```powershell
cd frontend
pnpm install --frozen-lockfile
pnpm run dev
```

Open the Vite URL printed in Terminal 2. Flask runs on `http://127.0.0.1:5000`.

## Production-style local run

```powershell
cd frontend
pnpm install --frozen-lockfile
pnpm run build
cd ..
python app.py
```

Open `http://127.0.0.1:5000`. Flask serves the React app and API from the same origin. For a public deployment, use a production WSGI server, set `ISMS_ENV=production`, and provide a strong `SECRET_KEY` through the deployment secret manager; `python app.py` is for local use. Local development generates an ephemeral random key when `SECRET_KEY` is unset, so sessions are cleared when the process restarts.

## Database and demo logins

The default SQLite database is `instance/isms.db`. Tables are created and local demo seed data is added on first run. No separate database process is required. Demo accounts and the shared `Pass@123` password are for local development only; demo seed creation is disabled in production. The project currently has no tracked migration framework and uses startup schema creation plus compatibility `ALTER TABLE` statements.

| Username | Role | Approver |
|---|---|---|
| `admin` | Administrator | Yes |
| `invmgr` | Inventory Manager | Yes |
| `procure` | Procurement Officer | No |
| `sales` | Sales Staff | No |
| `warehouse` | Warehouse Staff | No |
| `auditor` | Auditor | No |

Legacy demo usernames are also retained: `administrator`, `inventory_manager`, `procurement_officer`, `sales_staff`, `warehouse_staff`, and `auditor`.

## Who can do what

| Role | Main permissions |
|---|---|
| Administrator | All system actions; users, settings, logs, PO approvals, and fallback operations. Cannot approve their own PO/request when another approver is available. |
| Inventory Manager | Product/catalogue management, stock adjustments/transfers/counts, PO approvals, reports, and read access to purchasing/sales/suppliers. |
| Procurement Officer | Supplier management, PO creation/submission/sending/cancellation, receipts, alerts, and purchasing reports. Cannot approve POs. |
| Sales Staff | Create sales, invoices, returns, view products/stock, and see only their own sales. |
| Warehouse Staff | View products/stock/POs, receive sent orders, count/transfer stock, and submit small adjustments. Large adjustments require approval. |
| Auditor | Read-only product, stock, supplier, PO, sales, alert, report and business audit-log access. Security login logs are Administrator-only. Writes are denied by the API. |

API permissions are defined in `permissions.py`; `GET /api/auth/me` returns the permission keys used by the React UI. Every API write is checked server-side.

## Approval rules

- A PO at or below `po_approval_limit` auto-approves. A larger PO waits for an active approver.
- The PO creator cannot approve their own PO unless they are the only active approver. The seeded approvers are `admin` and `invmgr`.
- A Warehouse Staff adjustment over `adjustment_approval_threshold` becomes a pending request and does not change stock until an approver accepts it. Large Inventory Manager/Administrator adjustments apply immediately and are flagged.
- Procurement can cancel Draft or Pending POs with a reason. An approver may cancel an Approved PO only before receipt. Sent orders cannot be cancelled.
- Warehouse Staff cannot accept over-delivery. Procurement or Administrator must explicitly confirm it.
- Sales discounts are percentages and are capped by `max_discount_percent`; only Administrator changes that setting. Returns cannot exceed the unreturned sold quantity.

## Role journeys

- **Procurement:** review low-stock alerts, create/edit and submit POs, wait for approval, then print/send and track receipt.
- **Inventory Manager:** review approvals, maintain catalogue and stock, and monitor reports and low-stock alerts.
- **Warehouse:** receive sent POs, count/transfer stock, and submit large adjustment requests for approval.
- **Sales:** record a sale, print its invoice, and process eligible returns; the sales list is scoped to the signed-in staff member.
- **Auditor:** review dashboards, records, approval history, reports and login logs without write controls.
- **Administrator:** manage users and settings, inspect login activity, and act as an approver/fallback.

## Simplifications

- Password reset links are displayed only in local development; no email delivery is configured. Notifications are in-app only.
- Print views use browser Print / Save as PDF; the app does not generate PDF files.
- This is a single organisation, English-language, INR-only demo. Accounting, payments, SSO, email/SMS and external integrations are not included.
- PO rejection returns the PO to Draft with its rejection reason retained. The creator may approve their own PO only when they are the sole active approver.
- The React frontend is intentionally plain JavaScript and uses Context, not Redux or TypeScript.

## Tests

Run backend and API tests with:

```powershell
python -m pytest -q --basetemp .pytest_tmp
```

Build the React frontend with `pnpm run build` from `frontend/`.

The sanitized variable list is in `.env.example`. The application does not load dotenv files automatically; set variables in the shell or deployment environment.
