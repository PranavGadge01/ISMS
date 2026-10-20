"""All inventory mutations are centralized here and committed atomically."""
from models import db, StockLevel, StockTransaction, LowStockAlert, Product, PurchaseOrder, SalesTransaction, SalesTransactionLine, Setting, GoodsReceipt, GoodsReceiptLine, POHistory, Notification, User, StockAdjustmentRequest, AuditLog, IdempotencyRecord
import hashlib, json
from sqlalchemy.exc import IntegrityError

class StockError(ValueError): pass
class IdempotencyConflict(StockError): pass

def _claim_idempotency(operation,user_id,key,payload):
    key=(key or '').strip()
    if not key or len(key)>128: raise StockError('A valid Idempotency-Key is required (1-128 characters).')
    try: request_hash=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    except (TypeError,ValueError) as e: raise StockError('The request cannot be safely retried.') from e
    lookup={'operation':operation,'user_id':user_id,'key':key}
    record=IdempotencyRecord.query.filter_by(**lookup).first()
    if record:
        if record.request_hash!=request_hash: raise IdempotencyConflict('This Idempotency-Key was already used with a different request.')
        if not record.resource_id: raise IdempotencyConflict('This request is already in progress.')
        return record,True
    record=IdempotencyRecord(**lookup,request_hash=request_hash)
    db.session.add(record)
    try: db.session.flush()
    except IntegrityError:
        db.session.rollback(); record=IdempotencyRecord.query.filter_by(**lookup).first()
        if not record or record.request_hash!=request_hash or not record.resource_id: raise IdempotencyConflict('This request key is already in use.')
        return record,True
    return record,False

def _replay_resource(record,model):
    try: resource=db.session.get(model,int(record.resource_id))
    except (TypeError,ValueError): resource=None
    if not resource: raise IdempotencyConflict('The result for this request key is no longer available.')
    resource._idempotency_replayed=True
    return resource

def record_audit(event_type, actor_id, target_type, target_id=None, details=None):
    db.session.add(AuditLog(actor_id=actor_id,event_type=event_type,target_type=target_type,target_id=str(target_id) if target_id is not None else None,details=json.dumps(details or {},separators=(',',':'))))

def _level(product_id, warehouse_id):
    row=StockLevel.query.filter_by(product_id=product_id,warehouse_id=warehouse_id).with_for_update().first()
    if row is None:
        row=StockLevel(product_id=product_id,warehouse_id=warehouse_id,quantity_on_hand=0); db.session.add(row); db.session.flush()
    return row

def _change(product_id, warehouse_id, delta, user_id, kind, reason='', reference='', flagged=False):
    product=db.session.get(Product,product_id)
    if not product: raise StockError('Product not found.')
    delta=int(delta)
    level=_level(product_id,warehouse_id)
    changed=StockLevel.query.filter_by(id=level.id).filter(StockLevel.quantity_on_hand+delta>=0).update(
        {StockLevel.quantity_on_hand:StockLevel.quantity_on_hand+delta},synchronize_session=False
    )
    if changed!=1: raise StockError(f'Insufficient stock for {product.sku} — {product.name}.')
    db.session.flush()
    db.session.refresh(level)
    updated=level.quantity_on_hand
    db.session.add(StockTransaction(product_id=product_id,warehouse_id=warehouse_id,user_id=user_id,type=kind,quantity=int(delta),balance_after=updated,reason=reason,reference=reference,is_flagged=flagged))
    record_audit('STOCK_MOVEMENT',user_id,'product',product_id,{'warehouse_id':warehouse_id,'type':kind,'quantity_delta':delta,'balance_after':updated,'reason':reason,'reference':reference,'flagged':bool(flagged)})
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
        user=db.session.get(User,user_id); threshold_row=db.session.get(Setting,'adjustment_approval_threshold'); threshold=float(threshold_row.value) if threshold_row else 100
        if user and user.role=='Warehouse Staff' and abs(int(quantity))>threshold: raise StockError('Large warehouse adjustments must be submitted for approval.')
        balance=_change(product_id,warehouse_id,quantity,user_id,'ADJUSTMENT',reason+(': '+comment if comment else ''),flagged=flagged); db.session.commit(); return balance
    except Exception:
        db.session.rollback(); raise

def request_stock_adjustment(product_id,warehouse_id,direction,quantity,user_id,reason,comment=''):
    quantity=int(quantity)
    if direction not in {'Increase','Decrease'} or quantity<=0: raise StockError('Choose a direction and a positive quantity.')
    if reason not in {'Opening Stock','Damage','Theft','Count Correction','Other'}: raise StockError('Choose a valid adjustment reason.')
    try:
        row=StockAdjustmentRequest(product_id=product_id,warehouse_id=warehouse_id,direction=direction,quantity=quantity,reason=reason,comment=comment,requested_by=user_id,status='Pending')
        db.session.add(row); db.session.flush()
        record_audit('STOCK_ADJUSTMENT_REQUESTED',user_id,'stock_adjustment_request',row.id,{'product_id':product_id,'warehouse_id':warehouse_id,'direction':direction,'quantity':quantity,'reason':reason})
        notify_roles(['Administrator','Inventory Manager'],'A large stock adjustment needs approval.','/approvals?tab=stock',exclude=user_id,approvers=True)
        db.session.commit(); return row
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

def receive_goods(po_id, warehouse_id, quantities, user_id, accept_overdelivery=False, idempotency_key=None):
    try:
        po=PurchaseOrder.query.get_or_404(po_id)
        line_ids={str(line.id) for line in po.lines}
        normalized={str(k):int(v or 0) for k,v in (quantities or {}).items() if str(k) in line_ids}
        record,replayed=_claim_idempotency('goods_receipt',user_id,idempotency_key,{'po_id':po.id,'warehouse_id':int(warehouse_id),'quantities':normalized,'accept_overdelivery':bool(accept_overdelivery)})
        if replayed: return _replay_resource(record,PurchaseOrder)
        if po.status not in {'Sent to Supplier','Partially Received'}: raise StockError('This PO must be sent to the supplier before goods can be received.')
        accepted=[]; receipt=GoodsReceipt(po_id=po.id,warehouse_id=warehouse_id,received_by=user_id); db.session.add(receipt); db.session.flush()
        for line in po.lines:
            qty=int(normalized.get(str(line.id),0))
            if qty<0 or (line.received_qty+qty>line.ordered_qty and not accept_overdelivery): raise StockError(f'Received quantity exceeds ordered quantity for {line.product.name}. Confirm over-delivery to continue.')
            if qty:
                _change(line.product_id,warehouse_id,qty,user_id,'RECEIPT','Purchase order receipt',po.po_number); line.received_qty+=qty; receipt.lines.append(GoodsReceiptLine(product_id=line.product_id,quantity=qty)); accepted.append(line.product.name)
        po.status='Fully Received' if all(l.received_qty>=l.ordered_qty for l in po.lines) else 'Partially Received'
        db.session.add(POHistory(po_id=po.id,action='Received',user_id=user_id,note=', '.join(accepted)))
        record_audit('PURCHASE_ORDER_RECEIVED',user_id,'purchase_order',po.id,{'po_number':po.po_number,'warehouse_id':warehouse_id,'lines':len(accepted),'status':po.status})
        notify_user(po.created_by,f'{po.po_number} goods were received.','/purchase-orders/'+str(po.id))
        record.resource_type='purchase_order'; record.resource_id=str(po.id)
        db.session.commit(); return po
    except Exception:
        db.session.rollback(); raise

def complete_sale(cart, user_id, warehouse_id, payment_method, discount=0, customer_name='', customer_phone='', idempotency_key=None):
    try:
        request_items=[{'product_id':int(item['product_id']),'quantity':int(item['quantity'])} for item in (cart or [])]
        record,replayed=_claim_idempotency('sale',user_id,idempotency_key,{'items':request_items,'warehouse_id':int(warehouse_id),'payment_method':payment_method,'discount':float(discount),'customer_name':customer_name or '','customer_phone':customer_phone or ''})
        if replayed: return _replay_resource(record,SalesTransaction)
        setting= db.session.get(__import__('models').Setting,'tax_rate'); tax_rate=float(setting.value) if setting else 18
        if not request_items: raise StockError('Add at least one item to the sale.')
        if payment_method not in {'Cash','Card','UPI','Other'}: raise StockError('Choose a supported payment method.')
        discount=float(discount)
        priced=[]
        for item in request_items:
            qty=int(item['quantity']); p=db.session.get(Product,int(item['product_id']))
            if not p or not p.is_active: raise StockError('A selected product is unavailable.')
            if qty<=0: raise StockError('Sale quantities must be positive.')
            priced.append((p,qty,float(p.selling_price)))
        subtotal=sum(qty*price for _,qty,price in priced)
        if discount<0 or discount>subtotal: raise StockError('Discount must be between zero and the sale subtotal.')
        sale=SalesTransaction(processed_by=user_id,invoice_number='PENDING',payment_method=payment_method,discount=discount,customer_name=customer_name,customer_phone=customer_phone)
        sale.tax=max(0,subtotal-discount)*tax_rate/100; sale.total=max(0,subtotal-discount)+sale.tax; db.session.add(sale); db.session.flush(); sale.invoice_number=f'INV-{sale.id:04d}'
        for p,qty,price in priced:
            line=SalesTransactionLine(product_id=p.id,warehouse_id=warehouse_id,quantity=qty,unit_price=price); sale.lines.append(line)
            _change(p.id,warehouse_id,-qty,user_id,'SALE','Retail sale',sale.invoice_number)
        record_audit('SALE_COMPLETED',user_id,'sale',sale.id,{'invoice_number':sale.invoice_number,'line_count':len(priced),'total':round(sale.total,2)})
        record.resource_type='sale'; record.resource_id=str(sale.id)
        db.session.commit(); return sale
    except Exception:
        db.session.rollback(); raise

def process_return(line_id, quantity, user_id, warehouse_id=None, idempotency_key=None):
    try:
        line=SalesTransactionLine.query.get_or_404(line_id); qty=int(quantity)
        warehouse_id=warehouse_id or line.warehouse_id
        record,replayed=_claim_idempotency('sale_return',user_id,idempotency_key,{'line_id':line.id,'quantity':qty,'warehouse_id':int(warehouse_id) if warehouse_id else None})
        if replayed: return _replay_resource(record,SalesTransactionLine)
        if qty<=0: raise StockError('Return quantity must be greater than zero.')
        if not warehouse_id: raise StockError('Choose a warehouse for this return.')
        changed=SalesTransactionLine.query.filter_by(id=line_id).filter(
            SalesTransactionLine.returned_qty+qty<=SalesTransactionLine.quantity
        ).update({SalesTransactionLine.returned_qty:SalesTransactionLine.returned_qty+qty},synchronize_session=False)
        if changed!=1: raise StockError('Return exceeds the quantity originally sold.')
        _change(line.product_id,warehouse_id,qty,user_id,'RETURN','Customer return',line.sale.invoice_number)
        db.session.flush(); db.session.refresh(line); record_audit('SALE_RETURN',user_id,'sale_line',line.id,{'quantity':qty,'invoice_number':line.sale.invoice_number}); record.resource_type='sale_line'; record.resource_id=str(line.id); db.session.commit(); return line
    except Exception:
        db.session.rollback(); raise

def notify_user(user_id, message, link=None):
    if user_id: db.session.add(Notification(user_id=user_id,message=message,link=link))

def notify_roles(roles, message, link=None, exclude=None, approvers=False):
    query=User.query.filter(User.is_active.is_(True),User.role.in_(roles))
    if exclude: query=query.filter(User.id!=exclude)
    if approvers: query=query.filter(User.is_approver.is_(True))
    for user in query.all(): notify_user(user.id,message,link)