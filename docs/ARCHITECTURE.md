# ISMS Architecture

## Current structure

```text
React 18 / Vite (frontend/src)
        │ same-origin Axios requests under /api
        ▼
Flask app factory (app.py)
   ├── auth.py: login, logout, reset, idle timeout
   ├── api/__init__.py: JSON API and action permission checks
   ├── routes/main.py: legacy Jinja routes and role checks
   └── services.py: inventory, receipt, sale, return, alerts
        ▼
Flask-SQLAlchemy models (models.py)
        ▼
SQLite instance/isms.db
```

The production frontend build is served from `frontend/dist`; the README describes a Vite development proxy to Flask. The application keeps Jinja routes for legacy behavior and tests while React is the primary UI.

## Domain records found

`User`, `LoginLog`, `Category`, `Warehouse`, `Product`, `StockLevel`, `StockTransaction`, `Supplier`, `ProductSupplier`, `PurchaseOrder`, `PurchaseOrderLine`, `SalesTransaction`, `SalesTransactionLine`, `LowStockAlert`, `Setting`, `StockAdjustmentRequest`, `POHistory`, `GoodsReceipt`, `GoodsReceiptLine`, `Notification`, and the locally added `AuditLog`.

Product stock is represented by product/warehouse `StockLevel` rows and `StockTransaction` records. Sales, receipts, adjustments, and transfers call shared stock services. Purchase order approval/cancellation history is separate from the added business audit stream.

## Persistence and consistency

The existing application uses SQLite and SQLAlchemy. There is no migration framework; startup calls `db.create_all()` and applies handwritten missing-column `ALTER TABLE` statements. Stock operations and their related business records use a SQLAlchemy transaction. Stock balance deltas now use a conditional update that prevents the stored quantity from going negative. Sale, receipt, and return requests use persisted idempotency records to make identical retries replay their original result; concurrent retry stress and durable client key retention remain unverified. Reorder thresholds remain product-global rather than warehouse-specific.

Money is stored as floating point and needs a decimal migration. Constraints are mostly unique and foreign keys; positive quantities and valid state transitions are enforced primarily in service/API code. The current schema has no separate permission table; `permissions.py` maps action permission keys to roles.

## Security boundaries

The JSON API uses `permission_required`; legacy pages use `role_required`. The two mechanisms coexist and do not yet have complete parity. Flask signed-cookie sessions use an idle timeout and lockout tracking. Development runs use a random per-process secret if `SECRET_KEY` is omitted; production requires a configured key and does not auto-seed demo accounts. Production reset-link delivery is not available without an email provider.