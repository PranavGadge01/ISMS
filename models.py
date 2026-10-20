from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class User(db.Model):
    id=db.Column(db.Integer, primary_key=True); username=db.Column(db.String(80), unique=True, nullable=False); full_name=db.Column(db.String(160)); email=db.Column(db.String(160), unique=True, nullable=False)
    password_hash=db.Column(db.String(255), nullable=False); role=db.Column(db.String(40), nullable=False); is_approver=db.Column(db.Boolean, default=False); is_active=db.Column(db.Boolean, default=True)
    failed_attempts=db.Column(db.Integer, default=0); locked_until=db.Column(db.DateTime)
class LoginLog(db.Model):
    id=db.Column(db.Integer, primary_key=True); username=db.Column(db.String(80)); user_id=db.Column(db.Integer, db.ForeignKey('user.id')); success=db.Column(db.Boolean); ip_address=db.Column(db.String(80)); timestamp=db.Column(db.DateTime, default=datetime.utcnow)
class Category(db.Model):
    id=db.Column(db.Integer, primary_key=True); name=db.Column(db.String(100), unique=True, nullable=False); parent_id=db.Column(db.Integer, db.ForeignKey('category.id')); parent=db.relationship('Category', remote_side=[id])
class Warehouse(db.Model):
    id=db.Column(db.Integer, primary_key=True); name=db.Column(db.String(100), unique=True, nullable=False); location=db.Column(db.String(200)); is_active=db.Column(db.Boolean,default=True)
class Product(db.Model):
    id=db.Column(db.Integer, primary_key=True); sku=db.Column(db.String(40), unique=True, nullable=False); name=db.Column(db.String(160), nullable=False); category_id=db.Column(db.Integer, db.ForeignKey('category.id'))
    unit=db.Column(db.String(30), default='pcs'); cost_price=db.Column(db.Float, default=0); selling_price=db.Column(db.Float, default=0); reorder_level=db.Column(db.Integer, default=5); reorder_qty=db.Column(db.Integer, default=20); is_active=db.Column(db.Boolean, default=True)
    category=db.relationship('Category'); stock_levels=db.relationship('StockLevel', cascade='all, delete-orphan', back_populates='product')
class StockLevel(db.Model):
    id=db.Column(db.Integer, primary_key=True); product_id=db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False); warehouse_id=db.Column(db.Integer, db.ForeignKey('warehouse.id'), nullable=False); quantity_on_hand=db.Column(db.Integer, default=0)
    __table_args__=(db.UniqueConstraint('product_id','warehouse_id'),)
    product=db.relationship('Product', back_populates='stock_levels'); warehouse=db.relationship('Warehouse')
class StockTransaction(db.Model):
    id=db.Column(db.Integer, primary_key=True); product_id=db.Column(db.Integer, db.ForeignKey('product.id')); warehouse_id=db.Column(db.Integer, db.ForeignKey('warehouse.id')); user_id=db.Column(db.Integer, db.ForeignKey('user.id')); is_flagged=db.Column(db.Boolean,default=False)
    type=db.Column(db.String(30)); quantity=db.Column(db.Integer); balance_after=db.Column(db.Integer); reason=db.Column(db.String(200)); reference=db.Column(db.String(80)); timestamp=db.Column(db.DateTime, default=datetime.utcnow)
    product=db.relationship('Product'); warehouse=db.relationship('Warehouse'); user=db.relationship('User')
class Supplier(db.Model):
    id=db.Column(db.Integer, primary_key=True); name=db.Column(db.String(160), nullable=False); contact_person=db.Column(db.String(120)); phone=db.Column(db.String(40)); email=db.Column(db.String(160)); address=db.Column(db.String(250)); gstin=db.Column(db.String(30)); payment_terms=db.Column(db.String(100)); is_active=db.Column(db.Boolean, default=True); purchase_orders=db.relationship('PurchaseOrder', back_populates='supplier')
class ProductSupplier(db.Model):
    id=db.Column(db.Integer, primary_key=True); product_id=db.Column(db.Integer, db.ForeignKey('product.id')); supplier_id=db.Column(db.Integer, db.ForeignKey('supplier.id')); supplier_price=db.Column(db.Float); lead_time_days=db.Column(db.Integer, default=7); is_preferred=db.Column(db.Boolean, default=False)
    product=db.relationship('Product'); supplier=db.relationship('Supplier')
class PurchaseOrder(db.Model):
    id=db.Column(db.Integer, primary_key=True); po_number=db.Column(db.String(30), unique=True); supplier_id=db.Column(db.Integer, db.ForeignKey('supplier.id')); status=db.Column(db.String(30), default='Draft'); created_by=db.Column(db.Integer, db.ForeignKey('user.id')); order_date=db.Column(db.Date, default=datetime.utcnow); subtotal=db.Column(db.Float, default=0); tax=db.Column(db.Float, default=0); total=db.Column(db.Float, default=0); cancel_reason=db.Column(db.String(250)); rejection_reason=db.Column(db.String(250)); notes=db.Column(db.String(500)); approved_by=db.Column(db.Integer, db.ForeignKey('user.id')); approved_at=db.Column(db.DateTime); submitted_at=db.Column(db.DateTime); rejected_by=db.Column(db.Integer,db.ForeignKey('user.id')); sent_at=db.Column(db.DateTime); cancelled_by=db.Column(db.Integer,db.ForeignKey('user.id'))
    supplier=db.relationship('Supplier', back_populates='purchase_orders'); creator=db.relationship('User', foreign_keys=[created_by]); approver=db.relationship('User', foreign_keys=[approved_by]); lines=db.relationship('PurchaseOrderLine', cascade='all, delete-orphan')
class PurchaseOrderLine(db.Model):
    id=db.Column(db.Integer, primary_key=True); po_id=db.Column(db.Integer, db.ForeignKey('purchase_order.id')); product_id=db.Column(db.Integer, db.ForeignKey('product.id')); ordered_qty=db.Column(db.Integer); received_qty=db.Column(db.Integer, default=0); unit_price=db.Column(db.Float)
    product=db.relationship('Product')
class SalesTransaction(db.Model):
    id=db.Column(db.Integer, primary_key=True); invoice_number=db.Column(db.String(30), unique=True); processed_by=db.Column(db.Integer, db.ForeignKey('user.id')); sale_date=db.Column(db.DateTime, default=datetime.utcnow); customer_name=db.Column(db.String(120)); customer_phone=db.Column(db.String(40)); payment_method=db.Column(db.String(20)); discount=db.Column(db.Float, default=0); tax=db.Column(db.Float, default=0); total=db.Column(db.Float, default=0)
    staff=db.relationship('User'); lines=db.relationship('SalesTransactionLine', cascade='all, delete-orphan', back_populates='sale')
class SalesTransactionLine(db.Model):
    id=db.Column(db.Integer, primary_key=True); sale_id=db.Column(db.Integer, db.ForeignKey('sales_transaction.id')); product_id=db.Column(db.Integer, db.ForeignKey('product.id')); warehouse_id=db.Column(db.Integer, db.ForeignKey('warehouse.id')); quantity=db.Column(db.Integer); unit_price=db.Column(db.Float); returned_qty=db.Column(db.Integer, default=0)
    product=db.relationship('Product'); sale=db.relationship('SalesTransaction', back_populates='lines')
class LowStockAlert(db.Model):
    id=db.Column(db.Integer, primary_key=True); product_id=db.Column(db.Integer, db.ForeignKey('product.id')); warehouse_id=db.Column(db.Integer, db.ForeignKey('warehouse.id')); triggered_date=db.Column(db.DateTime, default=datetime.utcnow); cleared_at=db.Column(db.DateTime); status=db.Column(db.String(20), default='Open')
    product=db.relationship('Product'); warehouse=db.relationship('Warehouse')
class Setting(db.Model):
    key=db.Column(db.String(80), primary_key=True); value=db.Column(db.String(200), nullable=False)

class StockAdjustmentRequest(db.Model):
    id=db.Column(db.Integer,primary_key=True); product_id=db.Column(db.Integer,db.ForeignKey('product.id'),nullable=False); warehouse_id=db.Column(db.Integer,db.ForeignKey('warehouse.id'),nullable=False); direction=db.Column(db.String(20),nullable=False); quantity=db.Column(db.Integer,nullable=False); reason=db.Column(db.String(200)); comment=db.Column(db.String(500)); requested_by=db.Column(db.Integer,db.ForeignKey('user.id')); requested_at=db.Column(db.DateTime,default=datetime.utcnow); status=db.Column(db.String(20),default='Pending'); decided_by=db.Column(db.Integer,db.ForeignKey('user.id')); decided_at=db.Column(db.DateTime); decision_note=db.Column(db.String(500)); product=db.relationship('Product'); warehouse=db.relationship('Warehouse'); requester=db.relationship('User',foreign_keys=[requested_by]); decider=db.relationship('User',foreign_keys=[decided_by])

class POHistory(db.Model):
    id=db.Column(db.Integer,primary_key=True); po_id=db.Column(db.Integer,db.ForeignKey('purchase_order.id'),nullable=False); action=db.Column(db.String(40),nullable=False); user_id=db.Column(db.Integer,db.ForeignKey('user.id')); note=db.Column(db.String(500)); timestamp=db.Column(db.DateTime,default=datetime.utcnow); user=db.relationship('User'); po=db.relationship('PurchaseOrder')

class GoodsReceipt(db.Model):
    id=db.Column(db.Integer,primary_key=True); po_id=db.Column(db.Integer,db.ForeignKey('purchase_order.id')); warehouse_id=db.Column(db.Integer,db.ForeignKey('warehouse.id')); received_by=db.Column(db.Integer,db.ForeignKey('user.id')); received_at=db.Column(db.DateTime,default=datetime.utcnow); po=db.relationship('PurchaseOrder'); warehouse=db.relationship('Warehouse'); user=db.relationship('User'); lines=db.relationship('GoodsReceiptLine',cascade='all, delete-orphan')

class GoodsReceiptLine(db.Model):
    id=db.Column(db.Integer,primary_key=True); receipt_id=db.Column(db.Integer,db.ForeignKey('goods_receipt.id')); product_id=db.Column(db.Integer,db.ForeignKey('product.id')); quantity=db.Column(db.Integer); product=db.relationship('Product')

class Notification(db.Model):
    id=db.Column(db.Integer,primary_key=True); user_id=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False); message=db.Column(db.String(500),nullable=False); link=db.Column(db.String(300)); is_read=db.Column(db.Boolean,default=False); created_at=db.Column(db.DateTime,default=datetime.utcnow); user=db.relationship('User')

class AuditLog(db.Model):
    id=db.Column(db.Integer,primary_key=True); actor_id=db.Column(db.Integer,db.ForeignKey('user.id')); event_type=db.Column(db.String(80),nullable=False); target_type=db.Column(db.String(80),nullable=False); target_id=db.Column(db.String(80)); outcome=db.Column(db.String(20),nullable=False,default='success'); details=db.Column(db.Text); timestamp=db.Column(db.DateTime,default=datetime.utcnow,nullable=False); actor=db.relationship('User')

class IdempotencyRecord(db.Model):
    id=db.Column(db.Integer,primary_key=True); operation=db.Column(db.String(40),nullable=False); user_id=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False); key=db.Column(db.String(128),nullable=False); request_hash=db.Column(db.String(64),nullable=False); resource_type=db.Column(db.String(40)); resource_id=db.Column(db.String(80)); created_at=db.Column(db.DateTime,default=datetime.utcnow,nullable=False)
    __table_args__=(db.UniqueConstraint('operation','user_id','key',name='uq_idempotency_operation_user_key'),)
    user=db.relationship('User')