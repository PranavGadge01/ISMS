"""All inventory mutations are centralized here and committed atomically."""
from models import db, StockLevel, StockTransaction, LowStockAlert, Product, PurchaseOrder, SalesTransaction, SalesTransactionLine, Setting, GoodsReceipt, GoodsReceiptLine, POHistory, Notification, User, StockAdjustmentRequest

class StockError(ValueError): pass

def _level(product_id, warehouse_id):
    row=StockLevel.query.filter_by(product_id=product_id,warehouse_id=warehouse_id).with_for_update().first()
    if row is None:
        row=StockLevel(product_id=product_id,warehouse_id=warehouse_id,quantity_on_hand=0); db.session.add(row); db.session.flush()
    return row

def _change(product_id, warehouse_id, delta, user_id, kind, reason='', reference='', flagged=False):
    product=db.session.get(Product,product_id)
    if not product: raise StockError('Product not found.')
    level=_level(product_id,warehouse_id); updated=level.quantity_on_hand+int(delta)
    if updated<0: raise StockError(f'Insufficient stock for {product.sku} — {product.name}.')
    level.quantity_on_hand=updated
    db.session.add(StockTransaction(product_id=product_id,warehouse_id=warehouse_id,user_id=user_id,type=kind,quantity=int(delta),balance_after=updated,reason=reason,reference=reference,is_flagged=flagged))
    check_low_stock(product_id,warehouse_id,updated)
    return updated

def check_low_stock(product_id, warehouse_id, quantity=None):
    product=db.session.get(Product,product_id)
    if quantity is None: quantity=_level(product_id,warehouse_id).quantity_on_hand
    alert=LowStockAlert.query.filter_by(product_id=product_id,warehouse_id=warehouse_id,status='Open').first()
    if quantity<=product.reorder_level:
        if not alert:
            db.session.add(LowStockAlert(product_id=product_id,warehouse_id=warehouse_id))
            notify_roles(['Procurement Officer','Inventory Manager'],f'Low stock: {product.name} at {quantity}.','/alerts')
        if quantity<=0: notify_roles(['Inventory Manager'],'Out of stock: '+product.name,'/stock')
    elif alert: alert.status='Cleared'; alert.cleared_at=__import__('datetime').datetime.utcnow()

def adjust_stock(product_id, warehouse_id, quantity, user_id, reason, comment='', flagged=False):
    if reason not in {'Opening Stock','Damage','Theft','Count Correction','Other'}: raise StockError('Choose a valid adjustment reason.')
    if not int(quantity): raise StockError('Adjustment quantity cannot be zero.')
    try:
        balance=_change(product_id,warehouse_id,quantity,user_id,'ADJUSTMENT',reason+(': '+comment if comment else ''),flagged=flagged); db.session.commit(); return balance
    except Exception:
        db.session.rollback(); raise

def transfer_stock(product_id, from_warehouse_id, to_warehouse_id, quantity, user_id):
    try:
        quantity=int(quantity)
        if quantity<=0: raise StockError('Transfer quantity must be greater than zero.')
        if from_warehouse_id==to_warehouse_id: raise StockError('Choose two different warehouses.')
        _change(product_id,from_warehouse_id,-quantity,user_id,'TRANSFER_OUT','Stock transfer out')
        _change(product_id,to_warehouse_id,quantity,user_id,'TRANSFER_IN','Stock transfer in')
        db.session.commit()
    except Exception:
        db.session.rollback(); raise

def stock_count(warehouse_id, counts, user_id):
    try:
        user=db.session.get(User,user_id); threshold_row=db.session.get(Setting,'adjustment_approval_threshold'); threshold=float(threshold_row.value) if threshold_row else 100
        for product in Product.query.filter_by(is_active=True).all():
            if str(product.id) not in counts: continue
            counted=int(counts[str(product.id)])
            if counted<0: raise StockError(f'Count for {product.sku} cannot be negative.')
            current=_level(product.id,warehouse_id).quantity_on_hand
            delta=counted-current
            if delta:
                if abs(delta)>threshold and user and user.role=='Warehouse Staff':
                    request=StockAdjustmentRequest(product_id=product.id,warehouse_id=warehouse_id,direction='Increase' if delta>0 else 'Decrease',quantity=abs(delta),reason='Count Correction',requested_by=user_id,status='Pending'); db.session.add(request); db.session.flush(); notify_roles(['Administrator','Inventory Manager'],'A large stock count adjustment needs approval.','/approvals?tab=stock',exclude=user_id,approvers=True)
                else: _change(product.id,warehouse_id,delta,user_id,'ADJUSTMENT','Count Correction',flagged=abs(delta)>threshold)
        db.session.commit()
    except Exception:
        db.session.rollback(); raise

def receive_goods(po_id, warehouse_id, quantities, user_id, accept_overdelivery=False):
    try:
        po=PurchaseOrder.query.get_or_404(po_id)
        if po.status not in {'Sent to Supplier','Partially Received'}: raise StockError('This PO must be sent to the supplier before goods can be received.')
        accepted=[]; receipt=GoodsReceipt(po_id=po.id,warehouse_id=warehouse_id,received_by=user_id); db.session.add(receipt); db.session.flush()
        for line in po.lines:
            qty=int(quantities.get(str(line.id),0))
            if qty<0 or (line.received_qty+qty>line.ordered_qty and not accept_overdelivery): raise StockError(f'Received quantity exceeds ordered quantity for {line.product.name}. Confirm over-delivery to continue.')
            if qty:
                _change(line.product_id,warehouse_id,qty,user_id,'RECEIPT','Purchase order receipt',po.po_number); line.received_qty+=qty; receipt.lines.append(GoodsReceiptLine(product_id=line.product_id,quantity=qty)); accepted.append(line.product.name)
        po.status='Fully Received' if all(l.received_qty>=l.ordered_qty for l in po.lines) else 'Partially Received'
        db.session.add(POHistory(po_id=po.id,action='Received',user_id=user_id,note=', '.join(accepted)))
        notify_user(po.created_by,f'{po.po_number} goods were received.','/purchase-orders/'+str(po.id))
        db.session.commit(); return po
    except Exception:
        db.session.rollback(); raise

def complete_sale(cart, user_id, warehouse_id, payment_method, discount=0, customer_name='', customer_phone=''):
    try:
        setting= db.session.get(__import__('models').Setting,'tax_rate'); tax_rate=float(setting.value) if setting else 18
        discount=float(discount); subtotal=sum(int(x['quantity'])*float(x['unit_price']) for x in cart)
        sale=SalesTransaction(processed_by=user_id,invoice_number='PENDING',payment_method=payment_method,discount=discount,customer_name=customer_name,customer_phone=customer_phone)
        sale.tax=max(0,subtotal-discount)*tax_rate/100; sale.total=max(0,subtotal-discount)+sale.tax; db.session.add(sale); db.session.flush(); sale.invoice_number=f'INV-{sale.id:04d}'
        for item in cart:
            qty=int(item['quantity']); p=db.session.get(Product,item['product_id'])
            if qty<=0: raise StockError('Sale quantities must be positive.')
            line=SalesTransactionLine(product_id=p.id,warehouse_id=warehouse_id,quantity=qty,unit_price=float(item['unit_price'])); sale.lines.append(line)
            _change(p.id,warehouse_id,-qty,user_id,'SALE','Retail sale',sale.invoice_number)
        db.session.commit(); return sale
    except Exception:
        db.session.rollback(); raise

def process_return(line_id, quantity, user_id, warehouse_id=None):
    try:
        line=SalesTransactionLine.query.get_or_404(line_id); qty=int(quantity)
        if qty<=0 or line.returned_qty+qty>line.quantity: raise StockError('Return exceeds the quantity originally sold.')
        warehouse_id=warehouse_id or line.warehouse_id
        if not warehouse_id: raise StockError('Choose a warehouse for this return.')
        _change(line.product_id,warehouse_id,qty,user_id,'RETURN','Customer return',line.sale.invoice_number); line.returned_qty+=qty; db.session.commit(); return line
    except Exception:
        db.session.rollback(); raise

def notify_user(user_id, message, link=None):
    if user_id: db.session.add(Notification(user_id=user_id,message=message,link=link))

def notify_roles(roles, message, link=None, exclude=None, approvers=False):
    query=User.query.filter(User.is_active.is_(True),User.role.in_(roles))
    if exclude: query=query.filter(User.id!=exclude)
    if approvers: query=query.filter(User.is_approver.is_(True))
    for user in query.all(): notify_user(user.id,message,link)
