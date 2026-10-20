# API Catalog

Source inventory: `api/__init__.py` at upstream snapshot `7d031beb2a8b6ff3044e54a58d1d7d6f6d9949e7`, plus the added `GET /api/audit-log`. Static route inventory found **64 API method/route pairs**. The table has a named pytest reference for **28** pairs; **36** have no named reference, and even referenced routes do not have complete failure-path coverage. Protected routes return 401 without an active session and 403 when the action permission is missing unless noted. Most list results use `{items, page, pages, total}`; JSON errors generally use `{error, fields?}`. Resource IDs are integer path parameters.

| Method | Route | Access / primary rule | Test references |
|---|---|---|---|
| POST | `/api/auth/login` | Public; lockout after five failures | `test_isms.py` |
| POST | `/api/auth/logout` | Session required by client; clears session | — |
| GET | `/api/auth/me` | Public route; returns 401 without active user | `test_api_permissions.py` |
| POST | `/api/auth/forgot` | Public; generic production response; local reset link only | — |
| POST | `/api/auth/reset/<token>` | Public token route; expiry, one-use, 8+ chars | — |
| GET | `/api/dashboard` | `dashboard.view` | — |
| GET | `/api/products` | `product.view`; search/category/status/pagination | `test_api_permissions.py` |
| GET | `/api/products/<pid>` | `product.view`; details, levels, suppliers, movement history | — |
| POST | `/api/products` | `product.manage`; unique SKU; optional opening movement | `test_api_permissions.py`, `test_isms.py` |
| PUT | `/api/products/<pid>` | `product.manage` | — |
| POST | `/api/products/<pid>/suppliers` | `supplier.manage`; active supplier | — |
| GET | `/api/products.csv` | `product.view` | — |
| GET | `/api/categories` | `product.view` | — |
| POST | `/api/categories` | `product.manage`; unique name | — |
| PUT | `/api/categories/<cid>` | `product.manage` | — |
| DELETE | `/api/categories/<cid>` | `product.manage`; rejects referenced categories | — |
| GET | `/api/warehouses` | `stock.view` | — |
| POST | `/api/warehouses` | `product.manage` | — |
| PUT | `/api/warehouses/<wid>` | `product.manage` | — |
| DELETE | `/api/warehouses/<wid>` | `product.manage`; rejects stock/history | — |
| GET | `/api/stock` | `stock.view` | `test_api_permissions.py`, `test_workflow.py` |
| POST | `/api/stock/adjust` | `stock.adjust`; reason/direction/quantity; large warehouse request | `test_api_permissions.py` |
| POST | `/api/stock/transfer` | `stock.transfer`; positive quantity, different warehouses | `test_api_permissions.py` |
| POST | `/api/stock/count` | `stock.count`; large warehouse variance is approval request | `test_workflow.py` |
| GET | `/api/stock/requests` | `stock.view`; approver sees all, requester sees own | — |
| GET | `/api/suppliers` | `supplier.view`; search/status/pagination | `test_api_permissions.py` |
| POST | `/api/suppliers` | `supplier.manage` | — |
| GET | `/api/suppliers/<sid>` | `supplier.view` | — |
| PUT | `/api/suppliers/<sid>` | `supplier.manage` | — |
| POST | `/api/suppliers/<sid>/links` | `supplier.manage`; active supplier link | — |
| DELETE | `/api/suppliers/<sid>/links/<lid>` | `supplier.manage` | — |
| GET | `/api/purchase-orders` | `po.view`; filters/status/pagination | `test_api_permissions.py` |
| POST | `/api/purchase-orders` | `po.create`; active supplier | `test_workflow.py` |
| GET | `/api/purchase-orders/<poid>` | `po.view`; history/receipts | `test_workflow.py` |
| POST | `/api/purchase-orders/<poid>/lines` | `po.create`; creator-owned draft; explicit price override | `test_workflow.py`, added regression test |
| PUT | `/api/purchase-orders/<poid>/lines/<lid>` | `po.create`; creator-owned draft; explicit price override | — |
| DELETE | `/api/purchase-orders/<poid>/lines/<lid>` | `po.create`; creator-owned draft | — |
| POST | `/api/purchase-orders/<poid>/submit` | `po.create`; creator-owned nonempty draft; threshold | `test_workflow.py` |
| POST | `/api/purchase-orders/<poid>/approve` | `po.approve`; approver, pending state, separation of duties | `test_api_permissions.py`, `test_workflow.py` |
| POST | `/api/purchase-orders/<poid>/reject` | `po.approve`; reason required | — |
| POST | `/api/purchase-orders/<poid>/send` | `po.send`; Approved only | `test_workflow.py` |
| POST | `/api/purchase-orders/<poid>/receive` | `po.receive`; eligible PO; actual quantities; over-delivery permission; required `Idempotency-Key` header or body field; identical retry replays, conflicting payload returns 409 | `test_isms.py`, `test_workflow.py` |
| POST | `/api/purchase-orders/<poid>/cancel` | Authenticated; role/status/reason gate | — |
| GET | `/api/approvals` | `approval.history`; approver-scoped pending queues | `test_api_permissions.py` |
| POST | `/api/approvals/stock/<rid>/<decision>` | `stock.adjust.approve`; decision/reason and no self-approval | — |
| GET | `/api/alerts` | `alert.view` | `test_workflow.py` |
| POST | `/api/alerts/create-po` | `alert.createpo`; creates normal draft POs | `test_workflow.py` |
| GET | `/api/notifications` | Authenticated; own notifications only | — |
| POST | `/api/notifications/read-all` | Authenticated; own notifications only | — |
| PUT | `/api/notifications/<nid>/read` | Authenticated; own notification only | — |
| GET | `/api/sales` | `sale.view.all`; Sales Staff filtered to own | `test_api_permissions.py` |
| POST | `/api/sales` | `sale.create`; server price, discount and stock validation; required `Idempotency-Key` header or body field (428 missing, 409 conflicting payload; replay returns 200) | `test_workflow.py`, price and idempotency regression tests |
| GET | `/api/sales/<sid>` | `sale.view.all`; Sales Staff own record only | — |
| POST | `/api/sales/<sid>/returns/<lineid>` | `sale.return`; original sale line, remaining quantity, salesperson ownership; required idempotency key (428 missing, 409 conflicting payload) | `test_workflow.py`, ownership and idempotency regression tests |
| GET | `/api/users` | `user.manage` | `test_api_permissions.py` |
| POST | `/api/users` | `user.manage`; username/email/password/role validation | — |
| PUT | `/api/users/<uid>` | `user.manage`; supported role and last-admin guard | — |
| POST | `/api/users/<uid>/reset-password` | `user.manage`; 8+ character temporary password | — |
| GET | `/api/settings` | `settings.manage` | `test_api_permissions.py` |
| PUT | `/api/settings` | `settings.manage`; numeric bounds and text length | `test_api_permissions.py` |
| GET | `/api/login-log` | `loginlog.view` (Administrator) | `test_isms.py`; auditor denial test |
| GET | `/api/audit-log` | `audit.business.read` (Administrator, Auditor) | Added auditor permission test |
| GET | `/api/reports/<kind>` | Manual per-kind report permission check; Sales Staff sales are own-only | — |
| GET | `/api/reports/<kind>.csv` | Same permission check as report endpoint | — |

The legacy blueprints add 51 Jinja route rules in `routes/main.py` and four auth route rules in `auth.py` (some accept both GET and POST). Their full registered methods are discoverable at runtime with `app.url_map`; route-specific coverage and parity remain unverified. Legacy role decorators are documented in the audit as a remaining security review item.