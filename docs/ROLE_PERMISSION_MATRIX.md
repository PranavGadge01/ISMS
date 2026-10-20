# Role Permission Matrix

Permissions are action keys in `permissions.py` and are checked on the JSON API. The legacy Jinja routes still include role-list checks; they require a parity review before this matrix can be treated as exhaustive for every UI route.

| Role | Read | Create | Modify | Approve / special | Export |
|---|---|---|---|---|---|
| Administrator | All business records, settings, business audit, and login security log | Users, products, suppliers, POs, sales, adjustments | Master data, settings, accounts, permitted stock operations | PO and stock approvals; configured over-delivery; price override | Reports and CSV |
| Inventory Manager | Products, stock, suppliers, POs, sales and reports | Products/categories, stock adjustments/transfers/counts | Catalogue and stock operations | PO and stock adjustment approval when marked approver; large adjustments allowed | Inventory, movement, sales, purchase, supplier reports |
| Procurement Officer | Suppliers, POs, stock, alerts, purchase reports | Suppliers, POs and PO lines | Own draft PO, supplier links, dispatch/cancel/receipt actions | No PO approval by default; may confirm over-delivery | Purchase, low-stock and supplier reports |
| Sales Staff | Products, sellable stock, own sales/invoices | Sales and eligible returns | No completed-sale edits | No price override; server calculates selling price | Own sales report |
| Warehouse Staff | Products, stock, eligible POs, movement/low-stock reports | Receipts, counts, transfers, small stock adjustments | Pending adjustment requests | Large adjustment requires approval; cannot accept over-delivery | Movement and low-stock reports |
| Auditor | Business records, reports, approval history, business audit log | None | None | None | Read-only permitted reports |

## Important default rules

- `Auditor` does not receive `loginlog.view`; that security log is available to Administrator only. Auditor has `audit.business.read`.
- Purchase order creation and approval remain distinct. A creator cannot approve their own pending PO while another active approver exists. The existing documented fallback permits self-approval only when they are the sole active approver.
- `po_approval_limit` defaults to INR 50,000. At or below the threshold the existing workflow auto-approves; above it, a permissioned approver is required.
- `adjustment_approval_threshold` defaults to 100 units. Warehouse Staff requests larger changes for approval. Inventory Manager and Administrator may make flagged large adjustments.
- Over-delivery requires `po.receive.over`; Procurement Officer and Administrator receive that key by default. Warehouse Staff cannot self-authorize it.
- `max_discount_percent` defaults to 10%. The server checks the percentage and computes the discount; no actual payment or refund gateway is implemented.
- The added `po.price.override` permission is Administrator-only by default. Procurement can create lines at linked supplier price (or product cost fallback).
