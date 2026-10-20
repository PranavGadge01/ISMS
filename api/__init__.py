"""JSON API consumed by the React client. Legacy Jinja routes remain available."""
from datetime import datetime, date, timedelta
import csv, io, uuid, os, math
from flask import Blueprint, jsonify, request, session, Response
from werkzeug.security import check_password_hash, generate_password_hash
from models import db, User, LoginLog, Product, Category, Warehouse, StockLevel, StockTransaction, Supplier, ProductSupplier, PurchaseOrder, PurchaseOrderLine, SalesTransaction, SalesTransactionLine, LowStockAlert, Setting, Notification, StockAdjustmentRequest, POHistory, GoodsReceipt, AuditLog
from permissions import permission_required, permissions_for
from services import adjust_stock, transfer_stock, stock_count, receive_goods, complete_sale, process_return, check_low_stock, StockError, IdempotencyConflict, notify_user, notify_roles, _change, record_audit, request_stock_adjustment

api=Blueprint('api',__name__,url_prefix='/api')

def _user(): return db.session.get(User,session.get('user_id')) if session.get('user_id') else None
def _page(query, serializer):
    page=max(1,request.args.get('page',1,type=int)); per=min(100,max(1,request.args.get('per_page',20,type=int))); result=query.paginate(page=page,per_page=per,error_out=False)
    return jsonify(items=[serializer(x) for x in result.items],page=result.page,pages=result.pages,total=result.total)
def _product(p): return {'id':p.id,'sku':p.sku,'name':p.name,'category_id':p.category_id,'category':p.category.name if p.category else None,'unit':p.unit,'cost_price':p.cost_price,'selling_price':p.selling_price,'reorder_level':p.reorder_level,'reorder_qty':p.reorder_qty,'is_active':p.is_active,'stock':sum(x.quantity_on_hand for x in p.stock_levels)}
def _po(p): return {'id':p.id,'po_number':p.po_number,'supplier_id':p.supplier_id,'supplier':p.supplier.name,'status':p.status,'created_by':p.created_by,'creator':p.creator.username if p.creator else None,'order_date':p.order_date.isoformat(),'subtotal':p.subtotal,'tax':p.tax,'total':p.total,'notes':p.notes,'rejection_reason':p.rejection_reason,'cancel_reason':p.cancel_reason,'lines':[{'id':x.id,'product_id':x.product_id,'sku':x.product.sku,'product':x.product.name,'ordered_qty':x.ordered_qty,'received_qty':x.received_qty,'unit_price':x.unit_price} for x in p.lines]}
def _sale(s): return {'id':s.id,'invoice_number':s.invoice_number,'date':s.sale_date.isoformat(),'staff':s.staff.username,'processed_by':s.processed_by,'customer_name':s.customer_name,'payment_method':s.payment_method,'subtotal':sum(l.quantity*l.unit_price for l in s.lines),'discount':s.discount,'tax':s.tax,'total':s.total,'lines':[{'id':l.id,'product_id':l.product_id,'product':l.product.name,'quantity':l.quantity,'returned_qty':l.returned_qty,'unit_price':l.unit_price,'warehouse_id':l.warehouse_id} for l in s.lines]}
def _record(po,action,user_id,note=''):
    db.session.add(POHistory(po_id=po.id,action=action,user_id=user_id,note=note))
    record_audit('PURCHASE_ORDER_'+action.upper().replace(' ','_'),user_id,'purchase_order',po.id,{'po_number':po.po_number,'status':po.status,'note':note})

@api.post('/auth/login')
def login_api():
    body=request.get_json(silent=True) or {}; username=(body.get('username') or '').strip(); user=User.query.filter_by(username=username).first(); now=datetime.utcnow()
    ok=bool(user and user.is_active and (not user.locked_until or user.locked_until<=now) and check_password_hash(user.password_hash,body.get('password','')))
    db.session.add(LoginLog(username=username,user_id=user.id if user else None,success=ok,ip_address=request.remote_addr))
    if not ok:
        msg='Invalid username or password.'
        if user and user.locked_until and user.locked_until>now: msg=f"Account is locked. Try again in {max(1,int((user.locked_until-now).total_seconds()/60)+1)} minute(s)."
        elif user and user.is_active:
            user.failed_attempts+=1
            if user.failed_attempts>=5: user.locked_until=now+timedelta(minutes=15); user.failed_attempts=0; msg='Account is locked. Try again in 15 minute(s).'
        db.session.commit(); return jsonify(error=msg),401
    user.failed_attempts=0; user.locked_until=None; db.session.commit(); session.clear(); session['user_id']=user.id; session['last_seen']=now.isoformat(); return jsonify(user={'id':user.id,'username':user.username,'role':user.role,'permissions':permissions_for(user)})

@api.post('/auth/logout')
def logout_api(): session.clear(); return jsonify(ok=True)

@api.get('/auth/me')
def me_api():
    user=_user()
    if not user or not user.is_active: return jsonify(error='Authentication required.'),401
    return jsonify(user={'id':user.id,'username':user.username,'role':user.role,'is_approver':user.is_approver,'permissions':permissions_for(user)})

@api.post('/auth/forgot')
def forgot_api():
    body=request.get_json(silent=True) or {}; user=User.query.filter_by(username=(body.get('username') or '').strip()).first()
    generic={'message':'If the account exists, a reset link is ready.'}
    if not user or os.environ.get('ISMS_ENV','').lower()=='production': return jsonify(**generic)
    import secrets
    token=secrets.token_urlsafe(24); session['reset_'+token]=user.id; session['reset_exp_'+token]=(datetime.utcnow()+timedelta(minutes=15)).isoformat()
    return jsonify(message='Reset link ready.',reset_url='/reset/'+token)

@api.post('/auth/reset/<token>')
def reset_api(token):
    uid=session.get('reset_'+token); exp=session.get('reset_exp_'+token); body=request.get_json(silent=True) or {}; password=body.get('password','')
    if not uid or not exp or datetime.utcnow()>datetime.fromisoformat(exp): return jsonify(error='Reset link is invalid or expired.'),400
    if len(password)<8: return jsonify(error='Use at least 8 characters.',fields={'password':'Use at least 8 characters.'}),400
    user=db.session.get(User,uid); user.password_hash=generate_password_hash(password); session.pop('reset_'+token,None); session.pop('reset_exp_'+token,None); db.session.commit(); return jsonify(message='Password updated.')

@api.get('/dashboard')
@permission_required('dashboard.view')
def dashboard_api():
    user=_user(); now=date.today(); low=LowStockAlert.query.filter_by(status='Open').count(); pending=PurchaseOrder.query.filter_by(status='Pending Approval').count(); sales=SalesTransaction.query
    if user.role=='Sales Staff': sales=sales.filter_by(processed_by=user.id)
    today_total=sum(x.total for x in sales.all() if x.sale_date.date()==now)
    tasks=[]
    if user.role in {'Administrator','Inventory Manager'}: tasks += [{'label':'PO approvals','count':pending,'link':'/approvals?tab=po','color':'warning'},{'label':'Stock adjustments','count':StockAdjustmentRequest.query.filter_by(status='Pending').count(),'link':'/approvals?tab=stock','color':'danger'}]
    if user.role=='Procurement Officer': tasks += [{'label':'Low-stock items to reorder','count':low,'link':'/alerts','color':'warning'},{'label':'My Draft POs','count':PurchaseOrder.query.filter_by(created_by=user.id,status='Draft').count(),'link':'/purchase-orders?status=Draft','color':'primary'},{'label':'Rejected POs to fix','count':PurchaseOrder.query.filter_by(created_by=user.id,status='Draft').filter(PurchaseOrder.rejection_reason.isnot(None)).count(),'link':'/purchase-orders?status=Draft','color':'danger'}]
    if user.role=='Warehouse Staff': tasks += [{'label':'POs to receive','count':PurchaseOrder.query.filter(PurchaseOrder.status.in_(['Sent to Supplier','Partially Received'])).count(),'link':'/purchase-orders?scope=open','color':'primary'},{'label':'Low-stock items','count':low,'link':'/alerts','color':'warning'}]
    if user.role=='Sales Staff': tasks += [{'label':"Today's sales (mine)",'count':sum(1 for x in sales.all() if x.sale_date.date()==now),'link':'/sales','color':'success'},{'label':'New Sale','count':0,'link':'/sales/new','color':'primary'}]
    tasks += [{'label':'Low-stock items','count':low,'link':'/alerts','color':'warning'}]
    level_rows=StockLevel.query.all(); stock_value=sum(x.quantity_on_hand*x.product.cost_price for x in level_rows); recent_pos=PurchaseOrder.query.order_by(PurchaseOrder.id.desc()).limit(5).all(); recent_sales=sales.order_by(SalesTransaction.sale_date.desc()).limit(5).all()
    days=[now-timedelta(days=i) for i in reversed(range(7))]
    return jsonify(tasks=tasks,kpis={'stock_value':stock_value,'today_sales':today_total,'pending_pos':pending,'low_stock':low},sales_chart={'labels':[d.strftime('%a') for d in days],'values':[sum(x.total for x in sales.all() if x.sale_date.date()==d) for d in days]},recent_pos=[_po(x) for x in recent_pos],recent_sales=[_sale(x) for x in recent_sales])

@api.get('/products')
@permission_required('product.view')
def products_api():
    q=Product.query
    term=request.args.get('search','').strip()
    if term: q=q.filter(db.or_(Product.sku.ilike(f'%{term}%'),Product.name.ilike(f'%{term}%')))
    if request.args.get('category_id',type=int): q=q.filter_by(category_id=request.args.get('category_id',type=int))
    if request.args.get('status')=='inactive': q=q.filter_by(is_active=False)
    elif request.args.get('status')=='active': q=q.filter_by(is_active=True)
    return _page(q.order_by(Product.name),_product)

@api.get('/products/<int:pid>')
@permission_required('product.view')
def product_api(pid):
    p=Product.query.get_or_404(pid); data=_product(p); data['levels']=[{'warehouse_id':x.warehouse_id,'warehouse':x.warehouse.name,'quantity':x.quantity_on_hand} for x in p.stock_levels]; data['suppliers']=[{'id':x.supplier_id,'name':x.supplier.name,'price':x.supplier_price,'lead_time_days':x.lead_time_days,'is_preferred':x.is_preferred} for x in ProductSupplier.query.filter_by(product_id=pid)]; data['history']=[{'type':x.type,'quantity':x.quantity,'balance':x.balance_after,'reason':x.reason,'reference':x.reference,'timestamp':x.timestamp.isoformat(),'user':x.user.username if x.user else None} for x in StockTransaction.query.filter_by(product_id=pid).order_by(StockTransaction.timestamp.desc()).limit(200)]; return jsonify(data)

@api.post('/products')
@permission_required('product.manage')
def create_product_api():
    b=request.get_json(silent=True) or {}; name=(b.get('name') or '').strip(); sku=(b.get('sku') or '').strip().upper()
    if not name: return jsonify(error='Product name is required.',fields={'name':'Required.'}),400
    if not sku: sku=f"SKU-{uuid.uuid4().hex[:12].upper()}"
    if Product.query.filter_by(sku=sku).first(): return jsonify(error='SKU already exists.',fields={'sku':'SKU already exists.'}),400
    try:
        p=Product(sku=sku,name=name,category_id=b.get('category_id'),unit=b.get('unit') or 'pcs',cost_price=float(b.get('cost_price') or 0),selling_price=float(b.get('selling_price') or 0),reorder_level=int(b.get('reorder_level') or 0),reorder_qty=int(b.get('reorder_qty') or 0)); db.session.add(p); db.session.flush()
        qty=int(b.get('opening_quantity') or 0)
        if qty: adjust_stock(p.id,int(b.get('opening_warehouse_id')),qty,_user().id,'Opening Stock')
        else: db.session.commit()
        return jsonify(_product(p)),201
    except Exception as e: db.session.rollback(); return jsonify(error=str(e)),400

@api.put('/products/<int:pid>')
@permission_required('product.manage')
def edit_product_api(pid):
    p=Product.query.get_or_404(pid); b=request.get_json(silent=True) or {}
    for key in ['name','category_id','unit','cost_price','selling_price','reorder_level','reorder_qty','is_active']:
        if key in b: setattr(p,key,b[key])
    db.session.commit(); return jsonify(_product(p))
@api.post('/products/<int:pid>/suppliers')
@permission_required('supplier.manage')
def product_supplier_api(pid):
    p=Product.query.get_or_404(pid); b=request.get_json(silent=True) or {}; s=Supplier.query.get_or_404(b.get('supplier_id'))
    if not s.is_active: return jsonify(error='Inactive suppliers cannot be selected.'),400
    link=ProductSupplier.query.filter_by(product_id=pid,supplier_id=s.id).first() or ProductSupplier(product_id=pid,supplier_id=s.id)
    if b.get('is_preferred'): ProductSupplier.query.filter_by(product_id=pid).update({'is_preferred':False})
    link.supplier_price=float(b.get('price') if b.get('price') is not None else p.cost_price); link.lead_time_days=int(b.get('lead_time_days') or 7); link.is_preferred=bool(b.get('is_preferred')); db.session.add(link); db.session.commit(); return jsonify(id=link.id),201
@api.get('/products.csv')
@permission_required('product.view')
def products_csv_api():
    out=io.StringIO(); w=csv.writer(out); w.writerow(['SKU','Name','Category','Cost','Price','Stock','Active'])
    for p in Product.query: w.writerow([p.sku,p.name,p.category.name if p.category else '',p.cost_price,p.selling_price,sum(x.quantity_on_hand for x in p.stock_levels),p.is_active])
    return Response(out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=products.csv'})

@api.get('/categories')
@permission_required('product.view')
def categories_api(): return jsonify(items=[{'id':x.id,'name':x.name,'parent_id':x.parent_id,'parent':x.parent.name if x.parent else None} for x in Category.query.order_by(Category.name)])
@api.post('/categories')
@permission_required('product.manage')
def create_category_api():
    b=request.get_json(silent=True) or {}; name=(b.get('name') or '').strip()
    if not name or Category.query.filter_by(name=name).first(): return jsonify(error='A unique category name is required.',fields={'name':'Name is required and must be unique.'}),400
    x=Category(name=name,parent_id=b.get('parent_id')); db.session.add(x); db.session.commit(); return jsonify(id=x.id,name=x.name,parent_id=x.parent_id),201
@api.put('/categories/<int:cid>')
@permission_required('product.manage')
def edit_category_api(cid):
    x=Category.query.get_or_404(cid); b=request.get_json(silent=True) or {}; name=(b.get('name') or '').strip()
    if not name: return jsonify(error='Name is required.',fields={'name':'Required.'}),400
    x.name=name; x.parent_id=b.get('parent_id'); db.session.commit(); return jsonify(id=x.id,name=x.name,parent_id=x.parent_id)
@api.delete('/categories/<int:cid>')
@permission_required('product.manage')
def delete_category_api(cid):
    x=Category.query.get_or_404(cid)
    if Product.query.filter_by(category_id=cid).first() or Category.query.filter_by(parent_id=cid).first(): return jsonify(error='Category has products or child categories.'),400
    db.session.delete(x); db.session.commit(); return jsonify(ok=True)
@api.get('/warehouses')
@permission_required('stock.view')
def warehouses_api(): return jsonify(items=[{'id':x.id,'name':x.name,'location':x.location,'is_active':x.is_active} for x in Warehouse.query.order_by(Warehouse.name)])
@api.post('/warehouses')
@permission_required('product.manage')
def create_warehouse_api():
    b=request.get_json(silent=True) or {}; name=(b.get('name') or '').strip()
    if not name: return jsonify(error='Warehouse name is required.',fields={'name':'Required.'}),400
    x=Warehouse(name=name,location=b.get('location','')); db.session.add(x); db.session.commit(); return jsonify(id=x.id,name=x.name,location=x.location),201
@api.put('/warehouses/<int:wid>')
@permission_required('product.manage')
def edit_warehouse_api(wid):
    x=Warehouse.query.get_or_404(wid); b=request.get_json(silent=True) or {}
    for key in ['name','location','is_active']:
        if key in b: setattr(x,key,b[key])
    db.session.commit(); return jsonify(id=x.id,name=x.name,location=x.location,is_active=x.is_active)
@api.delete('/warehouses/<int:wid>')
@permission_required('product.manage')
def delete_warehouse_api(wid):
    x=Warehouse.query.get_or_404(wid)
    if StockLevel.query.filter_by(warehouse_id=wid).first() or StockTransaction.query.filter_by(warehouse_id=wid).first(): return jsonify(error='Warehouse has stock or history.'),400
    db.session.delete(x); db.session.commit(); return jsonify(ok=True)

@api.get('/stock')
@permission_required('stock.view')
def stock_api():
    rows=[]
    for p in Product.query.filter_by(is_active=True):
        for w in Warehouse.query.filter_by(is_active=True):
            level=StockLevel.query.filter_by(product_id=p.id,warehouse_id=w.id).first(); qty=level.quantity_on_hand if level else 0
            rows.append({'product_id':p.id,'sku':p.sku,'product':p.name,'warehouse_id':w.id,'warehouse':w.name,'quantity':qty,'reorder_level':p.reorder_level,'status':'Out' if qty<=0 else 'Low' if qty<=p.reorder_level else 'OK'})
    return jsonify(items=rows,warehouses=[{'id':x.id,'name':x.name} for x in Warehouse.query.all()])

@api.post('/stock/adjust')
@permission_required('stock.adjust')
def stock_adjust_api():
    b=request.get_json(silent=True) or {}; user=_user(); qty=int(b.get('quantity') or 0); direction=b.get('direction'); amount=qty if direction=='Increase' else -qty if direction=='Decrease' else 0; threshold=float((db.session.get(Setting,'adjustment_approval_threshold') or Setting(value='100')).value or 100)
    if qty<=0 or not amount: return jsonify(error='Enter a positive quantity and direction.',fields={'quantity':'Must be greater than zero.'}),400
    if qty>threshold and user.role=='Warehouse Staff':
        try: row=request_stock_adjustment(int(b.get('product_id')),int(b.get('warehouse_id')),direction,qty,user.id,b.get('reason'),b.get('comment','')); return jsonify(request_id=row.id,status=row.status),201
        except Exception as e: return jsonify(error=str(e)),400
    if qty>threshold and 'stock.adjust.large' not in permissions_for(user): return jsonify(error='Large adjustments are not permitted for your role.'),403
    try: balance=adjust_stock(int(b.get('product_id')),int(b.get('warehouse_id')),amount,user.id,b.get('reason','Other'),b.get('comment',''),flagged=qty>threshold); return jsonify(balance=balance,flagged=qty>threshold)
    except Exception as e: return jsonify(error=str(e)),400

@api.post('/stock/transfer')
@permission_required('stock.transfer')
def stock_transfer_api():
    b=request.get_json(silent=True) or {}
    try: transfer_stock(int(b.get('product_id')),int(b.get('from_warehouse_id')),int(b.get('to_warehouse_id')),int(b.get('quantity')), _user().id); return jsonify(ok=True)
    except Exception as e: return jsonify(error=str(e)),400
@api.post('/stock/count')
@permission_required('stock.count')
def stock_count_api():
    b=request.get_json(silent=True) or {}
    try: stock_count(int(b.get('warehouse_id')),b.get('counts',{}),_user().id); return jsonify(ok=True)
    except Exception as e: return jsonify(error=str(e)),400

@api.get('/stock/requests')
@permission_required('stock.view')
def adjustment_requests_api():
    u=_user(); q=StockAdjustmentRequest.query
    if 'stock.adjust.approve' not in permissions_for(u): q=q.filter_by(requested_by=u.id)
    return jsonify(items=[{'id':r.id,'product':r.product.name,'warehouse':r.warehouse.name,'quantity':r.quantity,'direction':r.direction,'reason':r.reason,'status':r.status,'requester':r.requester.username,'decision_note':r.decision_note} for r in q.order_by(StockAdjustmentRequest.requested_at.desc())])

@api.get('/suppliers')
@permission_required('supplier.view')
def suppliers_api():
    q=Supplier.query; search=request.args.get('search','')
    if search: q=q.filter(db.or_(Supplier.name.ilike(f'%{search}%'),Supplier.gstin.ilike(f'%{search}%')))
    if request.args.get('status') in ('active','inactive'): q=q.filter_by(is_active=request.args['status']=='active')
    return _page(q.order_by(Supplier.name),lambda x:{'id':x.id,'name':x.name,'gstin':x.gstin,'contact_person':x.contact_person,'phone':x.phone,'email':x.email,'is_active':x.is_active,'po_count':len(x.purchase_orders)})
@api.post('/suppliers')
@permission_required('supplier.manage')
def supplier_create_api():
    b=request.get_json(silent=True) or {}; name=(b.get('name') or '').strip()
    if not name: return jsonify(error='Name is required.',fields={'name':'Required.'}),400
    x=Supplier(name=name,contact_person=b.get('contact_person'),phone=b.get('phone'),email=b.get('email'),gstin=b.get('gstin'),address=b.get('address'),payment_terms=b.get('payment_terms')); db.session.add(x); db.session.commit(); return jsonify(id=x.id,name=x.name),201
@api.get('/suppliers/<int:sid>')
@permission_required('supplier.view')
def supplier_detail_api(sid):
    s=Supplier.query.get_or_404(sid); return jsonify(id=s.id,name=s.name,contact_person=s.contact_person,email=s.email,phone=s.phone,gstin=s.gstin,address=s.address,is_active=s.is_active,products=[{'id':l.id,'product_id':l.product_id,'product':l.product.name,'sku':l.product.sku,'price':l.supplier_price,'lead_time_days':l.lead_time_days,'is_preferred':l.is_preferred} for l in ProductSupplier.query.filter_by(supplier_id=sid)],pos=[_po(p) for p in s.purchase_orders])
@api.put('/suppliers/<int:sid>')
@permission_required('supplier.manage')
def supplier_edit_api(sid):
    s=Supplier.query.get_or_404(sid); b=request.get_json(silent=True) or {}
    for key in ['name','contact_person','phone','email','address','gstin','payment_terms','is_active']:
        if key in b: setattr(s,key,b[key])
    db.session.commit(); return jsonify(id=s.id,name=s.name,is_active=s.is_active)
@api.post('/suppliers/<int:sid>/links')
@permission_required('supplier.manage')
def supplier_link_api(sid):
    s=Supplier.query.get_or_404(sid); b=request.get_json(silent=True) or {}; p=Product.query.get_or_404(b.get('product_id'))
    if not s.is_active: return jsonify(error='Inactive suppliers cannot be linked.'),400
    link=ProductSupplier.query.filter_by(supplier_id=sid,product_id=p.id).first() or ProductSupplier(supplier_id=sid,product_id=p.id)
    if b.get('is_preferred'):
        ProductSupplier.query.filter_by(product_id=p.id).update({'is_preferred':False})
    link.supplier_price=float(b.get('price') if b.get('price') is not None else p.cost_price); link.lead_time_days=int(b.get('lead_time_days') or 7); link.is_preferred=bool(b.get('is_preferred')); db.session.add(link); db.session.commit(); return jsonify(id=link.id),201
@api.delete('/suppliers/<int:sid>/links/<int:lid>')
@permission_required('supplier.manage')
def supplier_unlink_api(sid,lid):
    x=ProductSupplier.query.filter_by(id=lid,supplier_id=sid).first_or_404(); db.session.delete(x); db.session.commit(); return jsonify(ok=True)

@api.get('/purchase-orders')
@permission_required('po.view')
def purchase_orders_api():
    q=PurchaseOrder.query
    for key in ['status','supplier_id']:
        if request.args.get(key): q=q.filter(getattr(PurchaseOrder,key)==request.args[key])
    if request.args.get('scope')=='open': q=q.filter(PurchaseOrder.status.in_(['Approved','Sent to Supplier','Partially Received']))
    term=request.args.get('search','')
    if term: q=q.filter(PurchaseOrder.po_number.ilike(f'%{term}%'))
    return _page(q.order_by(PurchaseOrder.id.desc()),_po)
@api.post('/purchase-orders')
@permission_required('po.create')
def po_create_api():
    b=request.get_json(silent=True) or {}; supplier=Supplier.query.get_or_404(b.get('supplier_id'))
    if not supplier.is_active: return jsonify(error='Inactive supplier cannot be selected.'),400
    po=PurchaseOrder(po_number='PENDING',supplier_id=supplier.id,created_by=_user().id,status='Draft',order_date=date.fromisoformat(b.get('order_date') or date.today().isoformat()),notes=b.get('notes')); db.session.add(po); db.session.flush(); po.po_number=f'PO-{po.id:04d}'; _record(po,'Created',_user().id); db.session.commit(); return jsonify(_po(po)),201
@api.get('/purchase-orders/<int:poid>')
@permission_required('po.view')
def po_detail_api(poid):
    p=PurchaseOrder.query.get_or_404(poid); data=_po(p); data['history']=[{'action':h.action,'note':h.note,'user':h.user.username if h.user else None,'timestamp':h.timestamp.isoformat()} for h in POHistory.query.filter_by(po_id=poid).order_by(POHistory.timestamp)]; data['receipts']=[{'warehouse':r.warehouse.name,'received_by':r.user.username if r.user else None,'received_at':r.received_at.isoformat(),'lines':[{'product':l.product.name,'quantity':l.quantity} for l in r.lines]} for r in GoodsReceipt.query.filter_by(po_id=poid).order_by(GoodsReceipt.received_at)]; return jsonify(data)
@api.post('/purchase-orders/<int:poid>/lines')
@permission_required('po.create')
def po_line_add_api(poid):
    po=PurchaseOrder.query.get_or_404(poid); b=request.get_json(silent=True) or {}; qty=int(b.get('quantity') or 0); user=_user()
    if po.created_by!=user.id: return jsonify(error='Only the draft creator can edit its lines.'),403
    if po.status!='Draft' or qty<=0: return jsonify(error='Draft PO and positive quantity required.'),400
    product=Product.query.get_or_404(b.get('product_id')); link=ProductSupplier.query.filter_by(product_id=product.id,supplier_id=po.supplier_id).first(); default_price=float(link.supplier_price if link else product.cost_price); requested=b.get('unit_price')
    if requested is not None and float(requested)!=default_price and 'po.price.override' not in permissions_for(user): return jsonify(error='Price overrides require explicit permission.'),403
    price=float(requested if requested is not None else default_price)
    if price<0: return jsonify(error='Unit price cannot be negative.'),400
    line=PurchaseOrderLine.query.filter_by(po_id=po.id,product_id=product.id).first()
    if line: line.ordered_qty+=qty; line.unit_price=price
    else: po.lines.append(PurchaseOrderLine(product_id=product.id,ordered_qty=qty,unit_price=price))
    _recalc(po); db.session.commit(); return jsonify(_po(po))
@api.put('/purchase-orders/<int:poid>/lines/<int:lid>')
@permission_required('po.create')
def po_line_edit_api(poid,lid):
    po=PurchaseOrder.query.get_or_404(poid); line=PurchaseOrderLine.query.filter_by(id=lid,po_id=poid).first_or_404(); b=request.get_json(silent=True) or {}; user=_user()
    if po.created_by!=user.id: return jsonify(error='Only the draft creator can edit its lines.'),403
    qty=int(b.get('quantity') or 0); price=float(b['unit_price']) if 'unit_price' in b else float(line.unit_price)
    if po.status!='Draft' or qty<=0 or price<0: return jsonify(error='Draft PO, positive quantity and non-negative price required.'),400
    if price!=float(line.unit_price) and 'po.price.override' not in permissions_for(user): return jsonify(error='Price overrides require explicit permission.'),403
    line.ordered_qty=qty; line.unit_price=price; _recalc(po); db.session.commit(); return jsonify(_po(po))
@api.delete('/purchase-orders/<int:poid>/lines/<int:lid>')
@permission_required('po.create')
def po_line_remove_api(poid,lid):
    po=PurchaseOrder.query.get_or_404(poid); line=PurchaseOrderLine.query.filter_by(id=lid,po_id=poid).first_or_404()
    if po.created_by!=_user().id: return jsonify(error='Only the draft creator can edit its lines.'),403
    if po.status!='Draft': return jsonify(error='PO lines are locked after submission.'),400
    db.session.delete(line); db.session.flush(); _recalc(po); db.session.commit(); return jsonify(_po(po))
def _recalc(po):
    po.subtotal=sum(x.ordered_qty*x.unit_price for x in po.lines); row=db.session.get(Setting,'tax_rate'); po.tax=po.subtotal*(float(row.value) if row else 18)/100; po.total=po.subtotal+po.tax
@api.post('/purchase-orders/<int:poid>/submit')
@permission_required('po.create')
def po_submit_api(poid):
    po=PurchaseOrder.query.get_or_404(poid); user=_user()
    if po.created_by!=user.id: return jsonify(error='Only the draft creator can submit this PO.'),403
    if po.status!='Draft' or not po.lines: return jsonify(error='Only a non-empty Draft PO can be submitted.'),400
    _recalc(po); row=db.session.get(Setting,'po_approval_limit'); limit=float(row.value) if row else 50000; po.submitted_at=datetime.utcnow()
    if po.total<=limit: po.status='Approved'; po.approved_by=user.id; po.approved_at=datetime.utcnow(); action='Auto-approved'; notify_user(po.created_by,f'{po.po_number} was auto-approved.','/purchase-orders/'+str(po.id))
    else: po.status='Pending Approval'; action='Submitted'; notify_roles(['Administrator','Inventory Manager'],'PO awaiting approval: '+po.po_number,'/approvals?tab=po',exclude=user.id,approvers=True)
    _record(po,action,user.id,'Auto-approved, under limit' if action=='Auto-approved' else ''); db.session.commit(); return jsonify(_po(po))
@api.post('/purchase-orders/<int:poid>/approve')
@permission_required('po.approve')
def po_approve_api(poid):
    po=PurchaseOrder.query.get_or_404(poid); u=_user()
    if not u.is_approver: return jsonify(error='Approver role required.'),403
    others=User.query.filter_by(is_approver=True,is_active=True).count()
    if po.status!='Pending Approval': return jsonify(error='PO is not awaiting approval.'),400
    if po.created_by==u.id and others>1: return jsonify(error='You cannot approve your own PO while another approver is available.'),403
    po.status='Approved'; po.approved_by=u.id; po.approved_at=datetime.utcnow(); _record(po,'Approved',u.id); notify_user(po.created_by,po.po_number+' was approved.','/purchase-orders/'+str(po.id)); db.session.commit(); return jsonify(_po(po))
@api.post('/purchase-orders/<int:poid>/reject')
@permission_required('po.approve')
def po_reject_api(poid):
    po=PurchaseOrder.query.get_or_404(poid); u=_user(); reason=(request.get_json(silent=True) or {}).get('reason','').strip()
    if not u.is_approver: return jsonify(error='Approver role required.'),403
    if po.status!='Pending Approval' or not reason: return jsonify(error='Pending PO and rejection reason are required.',fields={'reason':'Required.'}),400
    po.status='Draft'; po.rejection_reason=reason; po.rejected_by=u.id; po.approved_by=None; po.approved_at=None; _record(po,'Rejected',u.id,reason); notify_user(po.created_by,f'{po.po_number} rejected: {reason}','/purchase-orders/'+str(po.id)); db.session.commit(); return jsonify(_po(po))
@api.post('/purchase-orders/<int:poid>/send')
@permission_required('po.send')
def po_send_api(poid):
    po=PurchaseOrder.query.get_or_404(poid)
    if po.status!='Approved': return jsonify(error='Only approved POs can be sent.'),400
    po.status='Sent to Supplier'; po.sent_at=datetime.utcnow(); _record(po,'Sent',_user().id); notify_roles(['Warehouse Staff'],'Expected delivery for '+po.po_number,'/purchase-orders/'+str(po.id)); db.session.commit(); return jsonify(_po(po))
@api.post('/purchase-orders/<int:poid>/receive')
@permission_required('po.receive')
def po_receive_api(poid):
    b=request.get_json(silent=True) or {}; user=_user()
    key=request.headers.get('Idempotency-Key') or b.get('idempotency_key')
    if not key: return jsonify(error='Idempotency-Key is required for goods receipt.'),428
    if b.get('accept_overdelivery') and 'po.receive.over' not in permissions_for(user): return jsonify(error='Only Procurement or Administrator can accept over-delivery.'),403
    try: p=receive_goods(poid,int(b.get('warehouse_id')),b.get('quantities',{}),user.id,bool(b.get('accept_overdelivery')),key); return jsonify(_po(p))
    except IdempotencyConflict as e: return jsonify(error=str(e)),409
    except Exception as e: return jsonify(error=str(e)),400
@api.post('/purchase-orders/<int:poid>/cancel')
def po_cancel_api(poid):
    u=_user(); po=PurchaseOrder.query.get_or_404(poid)
    if not u: return jsonify(error='Authentication required.'),401
    b=request.get_json(silent=True) or {}; reason=(b.get('reason') or '').strip()
    if not reason: return jsonify(error='Cancellation reason is required.',fields={'reason':'Required.'}),400
    allowed=('po.cancel' in permissions_for(u) and po.status in {'Draft','Pending Approval'}) or ('po.cancel.approved' in permissions_for(u) and u.is_approver and po.status=='Approved' and not any(x.received_qty for x in po.lines))
    if not allowed: return jsonify(error='You cannot cancel this PO.'),403
    po.status='Cancelled'; po.cancel_reason=reason; po.cancelled_by=u.id; _record(po,'Cancelled',u.id,reason)
    if po.created_by!=u.id: notify_user(po.created_by,'PO cancelled: '+po.po_number,'/purchase-orders/'+str(po.id))
    db.session.commit(); return jsonify(_po(po))

@api.get('/approvals')
@permission_required('approval.history')
def approvals_api():
    u=_user(); can_approve='po.approve' in permissions_for(u); can_adjust='stock.adjust.approve' in permissions_for(u)
    return jsonify(pos=[_po(x) for x in PurchaseOrder.query.filter_by(status='Pending Approval').order_by(PurchaseOrder.submitted_at)] if can_approve else [],adjustments=[{'id':x.id,'product':x.product.name,'sku':x.product.sku,'warehouse':x.warehouse.name,'direction':x.direction,'quantity':x.quantity,'reason':x.reason,'comment':x.comment,'requester':x.requester.username,'requested_at':x.requested_at.isoformat(),'status':x.status} for x in StockAdjustmentRequest.query.filter_by(status='Pending').order_by(StockAdjustmentRequest.requested_at)] if can_adjust else [],history=[{'po':h.po.po_number,'action':h.action,'user':h.user.username if h.user else None,'note':h.note,'timestamp':h.timestamp.isoformat()} for h in POHistory.query.order_by(POHistory.timestamp.desc()).limit(100)])
@api.post('/approvals/stock/<int:rid>/<decision>')
@permission_required('stock.adjust.approve')
def adjustment_decide_api(rid,decision):
    row=StockAdjustmentRequest.query.get_or_404(rid); u=_user(); b=request.get_json(silent=True) or {}; note=(b.get('note') or '').strip()
    if decision not in {'approve','reject'} or row.status!='Pending': return jsonify(error='Invalid decision or request is no longer pending.'),400
    if row.requested_by==u.id: return jsonify(error='You cannot decide your own request.'),403
    if decision=='approve':
        delta=row.quantity if row.direction=='Increase' else -row.quantity
        try: _change(row.product_id,row.warehouse_id,delta,u.id,'ADJUSTMENT',(row.reason or 'Other')+(': '+row.comment if row.comment else ''),flagged=True); row.status='Approved'
        except Exception as e: db.session.rollback(); return jsonify(error=str(e)),400
    else:
        if not note: return jsonify(error='A rejection note is required.',fields={'note':'Required.'}),400
        row.status='Rejected'
    row.decided_by=u.id; row.decided_at=datetime.utcnow(); row.decision_note=note; notify_user(row.requested_by,'Stock adjustment '+row.status.lower()+'.','/stock/requests'); db.session.commit(); return jsonify(status=row.status)

@api.get('/alerts')
@permission_required('alert.view')
def alerts_api(): return jsonify(items=[{'id':a.id,'product_id':a.product_id,'sku':a.product.sku,'product':a.product.name,'warehouse_id':a.warehouse_id,'warehouse':a.warehouse.name,'quantity':(StockLevel.query.filter_by(product_id=a.product_id,warehouse_id=a.warehouse_id).first().quantity_on_hand if StockLevel.query.filter_by(product_id=a.product_id,warehouse_id=a.warehouse_id).first() else 0),'reorder_level':a.product.reorder_level,'suggested_qty':a.product.reorder_qty,'date':a.triggered_date.isoformat(),'preferred_supplier':next((l.supplier.name for l in ProductSupplier.query.filter_by(product_id=a.product_id,is_preferred=True)),None)} for a in LowStockAlert.query.filter_by(status='Open')])
@api.post('/alerts/create-po')
@permission_required('alert.createpo')
def alert_to_po_api():
    b=request.get_json(silent=True) or {}; grouped={}
    for a in LowStockAlert.query.filter(LowStockAlert.id.in_(b.get('alert_ids',[])),LowStockAlert.status=='Open').all():
        link=ProductSupplier.query.filter_by(product_id=a.product_id,is_preferred=True).join(Supplier).filter(Supplier.is_active.is_(True)).first()
        if link: grouped.setdefault(link.supplier_id,[]).append((a.product_id,a.product.reorder_qty,link.supplier_price))
    made=[]
    try:
        for sid,lines in grouped.items():
            po=PurchaseOrder(po_number='PENDING',supplier_id=sid,created_by=_user().id,status='Draft'); db.session.add(po); db.session.flush(); po.po_number=f'PO-{po.id:04d}'
            for pid,qty,price in lines: po.lines.append(PurchaseOrderLine(product_id=pid,ordered_qty=qty,unit_price=price))
            _recalc(po); _record(po,'Created',_user().id,'Created from low-stock alerts'); made.append(_po(po))
        db.session.commit(); return jsonify(items=made),201
    except Exception as e: db.session.rollback(); return jsonify(error=str(e)),400

@api.get('/notifications')
def notifications_api():
    u=_user()
    if not u: return jsonify(error='Authentication required.'),401
    query=Notification.query.filter_by(user_id=u.id).order_by(Notification.created_at.desc()); rows=query.all() if request.args.get('all')=='1' else query.limit(10).all(); return jsonify(unread=Notification.query.filter_by(user_id=u.id,is_read=False).count(),items=[{'id':x.id,'message':x.message,'link':x.link,'is_read':x.is_read,'created_at':x.created_at.isoformat()} for x in rows])
@api.post('/notifications/read-all')
def notifications_read_api():
    u=_user()
    if not u: return jsonify(error='Authentication required.'),401
    Notification.query.filter_by(user_id=u.id,is_read=False).update({'is_read':True}); db.session.commit(); return jsonify(ok=True)
@api.put('/notifications/<int:nid>/read')
def notification_read_api(nid):
    u=_user()
    if not u: return jsonify(error='Authentication required.'),401
    n=Notification.query.filter_by(id=nid,user_id=u.id).first_or_404(); n.is_read=True; db.session.commit(); return jsonify(ok=True)

@api.get('/sales')
@permission_required('sale.view.all')
def sales_api():
    q=SalesTransaction.query
    if _user().role=='Sales Staff': q=q.filter_by(processed_by=_user().id)
    if request.args.get('search'): q=q.filter(db.or_(SalesTransaction.invoice_number.ilike('%'+request.args['search']+'%'),SalesTransaction.customer_name.ilike('%'+request.args['search']+'%')))
    return _page(q.order_by(SalesTransaction.sale_date.desc()),_sale)
@api.post('/sales')
@permission_required('sale.create')
def sale_create_api():
    b=request.get_json(silent=True) or {}; row=db.session.get(Setting,'max_discount_percent'); maxpct=float(row.value) if row else 10; items=[]
    key=request.headers.get('Idempotency-Key') or b.get('idempotency_key')
    if not key: return jsonify(error='Idempotency-Key is required for sale creation.'),428
    try:
        for item in b.get('items',[]):
            p=Product.query.get_or_404(int(item.get('product_id')))
            if not p.is_active: return jsonify(error=f'{p.name} is inactive.'),400
            items.append({'product_id':p.id,'quantity':int(item.get('quantity') or 0),'unit_price':p.selling_price})
        subtotal=sum(x['quantity']*x['unit_price'] for x in items); discount_percent=float(b.get('discount_percent',0)); discount=subtotal*discount_percent/100
        if discount_percent<0 or discount_percent>maxpct: return jsonify(error=f'Discount exceeds the allowed {maxpct}%.',fields={'discount_percent':'Exceeds configured limit.'}),400
        s=complete_sale(items,_user().id,int(b.get('warehouse_id')),b.get('payment_method'),discount,b.get('customer_name'),b.get('customer_phone'),key); return jsonify(_sale(s)),200 if getattr(s,'_idempotency_replayed',False) else 201
    except IdempotencyConflict as e: return jsonify(error=str(e)),409
    except Exception as e: return jsonify(error=str(e)),400
@api.get('/sales/<int:sid>')
@permission_required('sale.view.all')
def sale_detail_api(sid):
    s=SalesTransaction.query.get_or_404(sid)
    if _user().role=='Sales Staff' and s.processed_by!=_user().id: return jsonify(error='Not found.'),404
    return jsonify(_sale(s))
@api.post('/sales/<int:sid>/returns/<int:lineid>')
@permission_required('sale.return')
def return_api(sid,lineid):
    line=SalesTransactionLine.query.filter_by(id=lineid,sale_id=sid).first_or_404()
    user=_user()
    if user.role=='Sales Staff' and line.sale.processed_by!=user.id: return jsonify(error='Not found.'),404
    b=request.get_json(silent=True) or {}; key=request.headers.get('Idempotency-Key') or b.get('idempotency_key')
    if not key: return jsonify(error='Idempotency-Key is required for returns.'),428
    try: process_return(lineid,int(b.get('quantity',0)),_user().id,idempotency_key=key); return jsonify(_sale(line.sale))
    except IdempotencyConflict as e: return jsonify(error=str(e)),409
    except Exception as e: return jsonify(error=str(e)),400

@api.get('/users')
@permission_required('user.manage')
def users_api(): return jsonify(items=[{'id':x.id,'username':x.username,'email':x.email,'role':x.role,'is_active':x.is_active,'is_approver':x.is_approver} for x in User.query.order_by(User.username)])
@api.post('/users')
@permission_required('user.manage')
def user_create_api():
    b=request.get_json(silent=True) or {}; username=(b.get('username') or '').strip(); email=(b.get('email') or '').strip()
    if not username or not email or not b.get('password'): return jsonify(error='Username, email and temporary password are required.'),400
    if b.get('role') not in {'Administrator','Inventory Manager','Procurement Officer','Sales Staff','Warehouse Staff','Auditor'}: return jsonify(error='Choose a supported role.',fields={'role':'Invalid role.'}),400
    if User.query.filter(db.or_(User.username==username,User.email==email)).first(): return jsonify(error='Username or email already exists.'),400
    u=User(username=username,email=email,password_hash=generate_password_hash(b['password']),role=b.get('role'),is_approver=bool(b.get('is_approver'))); db.session.add(u); db.session.commit(); return jsonify(id=u.id,username=u.username),201
@api.put('/users/<int:uid>')
@permission_required('user.manage')
def user_edit_api(uid):
    u=User.query.get_or_404(uid); b=request.get_json(silent=True) or {}
    if 'is_active' in b and not b['is_active'] and u.id==_user().id: return jsonify(error='You cannot deactivate yourself.'),400
    if 'role' in b and b['role'] not in {'Administrator','Inventory Manager','Procurement Officer','Sales Staff','Warehouse Staff','Auditor'}: return jsonify(error='Choose a supported role.',fields={'role':'Invalid role.'}),400
    removes_admin=(u.is_active and u.role=='Administrator' and (b.get('role',u.role)!='Administrator' or b.get('is_active',u.is_active) is False))
    if removes_admin and User.query.filter_by(role='Administrator',is_active=True).count()<=1: return jsonify(error='The last active administrator cannot be removed or deactivated.'),400
    before={'role':u.role,'is_active':u.is_active,'is_approver':u.is_approver}
    for key in ['email','full_name','role','is_approver','is_active']:
        if key in b: setattr(u,key,b[key])
    record_audit('USER_UPDATED',_user().id,'user',u.id,{'before':before,'after':{'role':u.role,'is_active':u.is_active,'is_approver':u.is_approver}})
    db.session.commit(); return jsonify(id=u.id,username=u.username,role=u.role,is_active=u.is_active,is_approver=u.is_approver)
@api.post('/users/<int:uid>/reset-password')
@permission_required('user.manage')
def user_reset_password_api(uid):
    u=User.query.get_or_404(uid); password=(request.get_json(silent=True) or {}).get('password','')
    if len(password)<8: return jsonify(error='Temporary password must be at least 8 characters.',fields={'password':'At least 8 characters.'}),400
    u.password_hash=generate_password_hash(password); db.session.commit(); return jsonify(ok=True)
@api.get('/settings')
@permission_required('settings.manage')
def settings_api(): return jsonify(settings={x.key:x.value for x in Setting.query.all()})
@api.put('/settings')
@permission_required('settings.manage')
def settings_save_api():
    b=request.get_json(silent=True) or {}
    changed={}
    for key,value in b.items():
        if key in {'po_approval_limit','tax_rate','max_discount_percent','adjustment_approval_threshold','company_name','company_address'}:
            if key in {'po_approval_limit','tax_rate','max_discount_percent','adjustment_approval_threshold'}:
                try: number=float(value)
                except (TypeError,ValueError): return jsonify(error=f'{key} must be a number.',fields={key:'Enter a valid number.'}),400
                if not math.isfinite(number) or number<0 or (key in {'tax_rate','max_discount_percent'} and number>100): return jsonify(error=f'{key} is outside the allowed range.',fields={key:'Enter a value from 0 to 100.'}),400
            elif len(str(value))>200: return jsonify(error=f'{key} is too long.',fields={key:'Maximum length is 200 characters.'}),400
            row=db.session.get(Setting,key) or Setting(key=key,value=''); changed[key]={'before':row.value,'after':str(value)}; row.value=str(value); db.session.add(row)
    if changed: record_audit('SYSTEM_SETTINGS_UPDATED',_user().id,'settings','system',changed)
    db.session.commit(); return jsonify(ok=True)
@api.get('/login-log')
@permission_required('loginlog.view')
def loginlog_api(): return _page(LoginLog.query.order_by(LoginLog.timestamp.desc()),lambda x:{'id':x.id,'username':x.username,'success':x.success,'ip':x.ip_address,'timestamp':x.timestamp.isoformat()})

@api.get('/audit-log')
@permission_required('audit.business.read')
def audit_log_api():
    return _page(AuditLog.query.order_by(AuditLog.timestamp.desc()),lambda x:{'id':x.id,'actor':x.actor.username if x.actor else None,'event_type':x.event_type,'target_type':x.target_type,'target_id':x.target_id,'outcome':x.outcome,'details':x.details,'timestamp':x.timestamp.isoformat()})

@api.get('/reports/<kind>')
def report_api(kind):
    perms={'stock':'report.stock','sales':'report.sales','purchase':'report.purchase','movement':'report.movement','low':'report.low','supplier':'report.supplier'}
    required=perms.get(kind)
    u=_user()
    if not u: return jsonify(error='Authentication required.'),401
    if kind=='sales' and u.role=='Sales Staff': required='report.sales.own'
    if not required or required not in permissions_for(u): return jsonify(error='Not allowed.'),403
    if kind=='stock': return jsonify(items=[{'sku':x.product.sku,'product':x.product.name,'warehouse':x.warehouse.name,'quantity':x.quantity_on_hand,'value':x.quantity_on_hand*x.product.cost_price} for x in StockLevel.query])
    if kind=='movement': return jsonify(items=[{'timestamp':x.timestamp.isoformat(),'product':x.product.name,'sku':x.product.sku,'warehouse':x.warehouse.name,'type':x.type,'quantity':x.quantity,'balance':x.balance_after,'reason':x.reason} for x in StockTransaction.query.order_by(StockTransaction.timestamp.desc())])
    if kind=='low': return jsonify(items=[{'product':a.product.name,'warehouse':a.warehouse.name,'status':a.status,'reorder_level':a.product.reorder_level} for a in LowStockAlert.query])
    if kind=='purchase': return jsonify(items=[_po(x) for x in PurchaseOrder.query.order_by(PurchaseOrder.id.desc())])
    if kind=='supplier': return jsonify(items=[{'supplier':s.name,'po_count':len(s.purchase_orders),'total_value':sum(x.total for x in s.purchase_orders)} for s in Supplier.query])
    q=SalesTransaction.query
    if u.role=='Sales Staff': q=q.filter_by(processed_by=u.id)
    return jsonify(items=[_sale(x) for x in q.order_by(SalesTransaction.sale_date.desc())])

@api.get('/reports/<kind>.csv')
def report_csv_api(kind):
    response=report_api(kind)
    if isinstance(response,tuple): return response
    if not isinstance(response,Response): return response
    payload=response.get_json(); rows=payload.get('items',[]); out=io.StringIO(); writer=csv.writer(out)
    if rows:
        writer.writerow(rows[0].keys())
        for row in rows: writer.writerow(row.values())
    else: writer.writerow(['No data'])
    return Response(out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':f'attachment; filename={kind}-report.csv'})