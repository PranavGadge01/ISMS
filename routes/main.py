import csv, io, uuid
from datetime import date, datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, Response, session, abort
from werkzeug.security import generate_password_hash
from models import db, User, Category, Warehouse, Product, StockLevel, StockTransaction, Supplier, ProductSupplier, PurchaseOrder, PurchaseOrderLine, SalesTransaction, SalesTransactionLine, LowStockAlert, Setting, LoginLog
from auth import role_required
from services import adjust_stock, transfer_stock, stock_count, receive_goods, complete_sale, process_return, StockError, request_stock_adjustment
from permissions import permissions_for

bp=Blueprint('main',__name__)
ADMIN=('Administrator',); INV=('Inventory Manager',); PROC=('Procurement Officer',); SALE=('Sales Staff',); WH=('Warehouse Staff',); READ=('Administrator','Inventory Manager','Procurement Officer','Sales Staff','Warehouse Staff','Auditor')
@bp.app_context_processor
def common():
    u=db.session.get(User,session.get('user_id')) if session.get('user_id') else None
    return {'current_user':u,'open_alert_count':LowStockAlert.query.filter_by(status='Open').count()}
@bp.route('/')
@role_required(*READ)
def dashboard():
    today=date.today(); days=[today-timedelta(days=x) for x in reversed(range(7))]
    sales=[round(sum(s.total for s in SalesTransaction.query.filter(db.func.date(SalesTransaction.sale_date)==d).all()),2) for d in days]
    levels=StockLevel.query.all(); low=[x for x in levels if x.quantity_on_hand<=x.product.reorder_level]
    return render_template('dashboard.html',stock_value=sum(x.quantity_on_hand*x.product.cost_price for x in levels),today_sales=sales[-1],pending=PurchaseOrder.query.filter(PurchaseOrder.status.in_(['Pending Approval','Approved','Sent to Supplier'])).count(),low=low,days=[d.strftime('%a') for d in days],sales=sales)

@bp.route('/products')
@role_required(*READ)
def products():
    q=Product.query
    if request.args.get('q'): q=q.filter(db.or_(Product.name.ilike('%'+request.args['q']+'%'),Product.sku.ilike('%'+request.args['q']+'%')))
    if request.args.get('category'): q=q.filter_by(category_id=request.args['category'])
    stock_sum=db.session.query(StockLevel.product_id.label('pid'),db.func.sum(StockLevel.quantity_on_hand).label('qty')).group_by(StockLevel.product_id).subquery()
    q=q.outerjoin(stock_sum,Product.id==stock_sum.c.pid)
    if request.args.get('stock_status')=='in': q=q.filter(stock_sum.c.qty>0)
    elif request.args.get('stock_status')=='low': q=q.filter(stock_sum.c.qty>0,stock_sum.c.qty<=Product.reorder_level)
    elif request.args.get('stock_status')=='out': q=q.filter(db.or_(stock_sum.c.qty==None,stock_sum.c.qty<=0))
    if request.args.get('status')=='inactive': q=q.filter_by(is_active=False)
    elif request.args.get('status')=='active': q=q.filter_by(is_active=True)
    items=q.order_by(Product.name).paginate(page=request.args.get('page',1,type=int),per_page=20,error_out=False)
    return render_template('products.html',items=items,categories=Category.query.all())
@bp.route('/products/new',methods=['GET','POST'])
@role_required(*INV)
def product_new():
    cats=Category.query.all()
    if request.method=='POST':
        sku=request.form.get('sku','').strip().upper()
        if not sku:
            prefix=(Category.query.get(request.form.get('category_id',type=int)).name[:3].upper() if request.form.get('category_id',type=int) and Category.query.get(request.form.get('category_id',type=int)) else 'SKU')
            sku=f'{prefix}-{(Product.query.filter(Product.sku.like(prefix+"-%")).count()+1):04d}'
        if Product.query.filter_by(sku=sku).first(): flash('SKU already exists.','danger')
        elif not request.form.get('name'): flash('Product name is required.','danger')
        else:
            p=Product(sku=sku,name=request.form['name'],category_id=request.form.get('category_id',type=int),unit=request.form.get('unit') or 'pcs',cost_price=request.form.get('cost_price',type=float) or 0,selling_price=request.form.get('selling_price',type=float) or 0,reorder_level=request.form.get('reorder_level',type=int) or 5,reorder_qty=request.form.get('reorder_qty',type=int) or 20); db.session.add(p); db.session.flush()
            opening_qty=request.form.get('opening_quantity',type=int) or 0; opening_wh=request.form.get('opening_warehouse_id',type=int)
            if opening_qty<0 or (opening_qty>0 and not opening_wh):
                db.session.rollback(); flash('Choose a warehouse for opening stock and enter a non-negative quantity.','danger'); return render_template('product_form.html',categories=cats,warehouses=Warehouse.query.all())
            if opening_qty: adjust_stock(p.id,opening_wh,opening_qty,session['user_id'],'Opening Stock')
            else: db.session.commit()
            flash(f'Product {sku} created.','success'); return redirect(url_for('main.products'))
    return render_template('product_form.html',categories=cats,warehouses=Warehouse.query.all())
@bp.route('/products/<int:pid>')
@role_required(*READ)
def product_detail(pid):
    p=Product.query.get_or_404(pid); return render_template('product_detail.html',p=p,levels=StockLevel.query.filter_by(product_id=pid).all(),warehouses=Warehouse.query.all(),history=StockTransaction.query.filter_by(product_id=pid).order_by(StockTransaction.timestamp.desc()).all(),supplier_links=ProductSupplier.query.filter_by(product_id=pid).all(),suppliers=Supplier.query.filter_by(is_active=True).order_by(Supplier.name).all())
@bp.route('/products/<int:pid>/edit',methods=['GET','POST'])
@role_required(*INV)
def product_edit(pid):
    p=Product.query.get_or_404(pid)
    if request.method=='POST':
        if not request.form.get('name','').strip(): flash('Product name is required.','danger')
        else:
            p.name=request.form['name'].strip(); p.category_id=request.form.get('category_id',type=int); p.unit=request.form.get('unit','pcs').strip() or 'pcs'; p.cost_price=request.form.get('cost_price',type=float) or 0; p.selling_price=request.form.get('selling_price',type=float) or 0; p.reorder_level=request.form.get('reorder_level',type=int) or 0; p.reorder_qty=request.form.get('reorder_qty',type=int) or 0; db.session.commit(); flash('Product updated.','success'); return redirect(url_for('main.product_detail',pid=p.id))
    return render_template('product_edit.html',p=p,categories=Category.query.all())
@bp.route('/products/<int:pid>/active',methods=['POST'])
@role_required(*INV)
def product_active(pid):
    p=Product.query.get_or_404(pid); p.is_active=not p.is_active; db.session.commit(); return redirect(url_for('main.products'))
@bp.route('/products/<int:pid>/adjust',methods=['POST'])
@role_required(*INV,'Warehouse Staff')
def adjustment(pid):
    try:
        user=session_user(); qty=request.form.get('quantity',type=int); direction=request.form.get('direction')
        if direction not in {'Increase','Decrease'}: raise StockError('Choose an adjustment direction.')
        signed=qty if direction=='Increase' else -qty; threshold=float((db.session.get(Setting,'adjustment_approval_threshold') or Setting(value='100')).value or 100)
        if user.role=='Warehouse Staff' and abs(signed)>threshold: request_stock_adjustment(pid,request.form.get('warehouse_id',type=int),direction,abs(signed),user.id,request.form.get('reason'),request.form.get('comment','')); flash('Large adjustment submitted for approval.','success')
        else: adjust_stock(pid,request.form.get('warehouse_id',type=int),signed,user.id,request.form.get('reason'),request.form.get('comment','')); flash('Stock adjusted.','success')
    except Exception as e: db.session.rollback(); flash(str(e),'danger')
    return redirect(url_for('main.product_detail',pid=pid))
@bp.route('/products.csv')
@role_required(*READ)
def products_csv():
    out=io.StringIO(); w=csv.writer(out); w.writerow(['SKU','Name','Category','Cost','Price','Stock','Status'])
    for p in Product.query.order_by(Product.sku): w.writerow([p.sku,p.name,p.category.name if p.category else '',p.cost_price,p.selling_price,sum(x.quantity_on_hand for x in StockLevel.query.filter_by(product_id=p.id)), 'Active' if p.is_active else 'Inactive'])
    return Response(out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=products.csv'})

@bp.route('/stock')
@role_required(*READ)
def stock_page():
    products=Product.query.order_by(Product.sku).all(); warehouses=Warehouse.query.order_by(Warehouse.name).all(); levels={(x.product_id,x.warehouse_id):x.quantity_on_hand for x in StockLevel.query.all()}
    return render_template('stock.html',products=products,warehouses=warehouses,levels=levels)
@bp.route('/stock/adjust',methods=['GET','POST'])
@role_required(*INV,'Warehouse Staff')
def stock_adjust():
    if request.method=='POST':
        try:
            qty=request.form.get('quantity',type=int); direction=request.form.get('direction'); signed=qty if direction=='Increase' else -qty if direction=='Decrease' else 0
            if not qty or qty<=0: raise ValueError('Quantity must be greater than zero.')
            user=session_user(); threshold=float((db.session.get(Setting,'adjustment_approval_threshold') or Setting(value='100')).value or 100)
            if user.role=='Warehouse Staff' and qty>threshold: request_stock_adjustment(request.form.get('product_id',type=int),request.form.get('warehouse_id',type=int),direction,qty,user.id,request.form.get('reason'),request.form.get('comment','')); flash('Large adjustment submitted for approval.','success')
            else: adjust_stock(request.form.get('product_id',type=int),request.form.get('warehouse_id',type=int),signed,user.id,request.form.get('reason'),request.form.get('comment','')); flash('Stock adjustment saved.','success')
            return redirect(url_for('main.stock_page'))
        except Exception as e: db.session.rollback(); flash(str(e),'danger')
    return render_template('stock_adjust.html',products=Product.query.filter_by(is_active=True).all(),warehouses=Warehouse.query.all())
@bp.route('/stock/transfer',methods=['GET','POST'])
@role_required(*INV,'Warehouse Staff')
def stock_transfer():
    if request.method=='POST':
        try: transfer_stock(request.form.get('product_id',type=int),request.form.get('from_warehouse_id',type=int),request.form.get('to_warehouse_id',type=int),request.form.get('quantity',type=int),session['user_id']); flash('Stock transfer completed.','success'); return redirect(url_for('main.stock_page'))
        except Exception as e: db.session.rollback(); flash(str(e),'danger')
    return render_template('stock_transfer.html',products=Product.query.filter_by(is_active=True).all(),warehouses=Warehouse.query.all())
@bp.route('/stock/count',methods=['GET','POST'])
@role_required(*INV,'Warehouse Staff')
def stock_count_page():
    warehouses=Warehouse.query.all(); warehouse_id=request.values.get('warehouse_id',type=int)
    if request.method=='POST':
        try: stock_count(warehouse_id,request.form,session['user_id']); flash('Stock count saved.','success'); return redirect(url_for('main.stock_page'))
        except Exception as e: flash(str(e),'danger')
    if not warehouse_id and warehouses: warehouse_id=warehouses[0].id
    return render_template('stock_count.html',warehouses=warehouses,warehouse_id=warehouse_id,products=Product.query.filter_by(is_active=True).order_by(Product.name).all(),levels={(x.product_id,x.warehouse_id):x.quantity_on_hand for x in StockLevel.query.all()})

@bp.route('/categories',methods=['GET','POST'])
@role_required(*INV)
def categories():
    if request.method=='POST':
        name=request.form.get('name','').strip()
        if name and not Category.query.filter_by(name=name).first(): db.session.add(Category(name=name,parent_id=request.form.get('parent_id',type=int))); db.session.commit(); flash('Category saved.','success')
        else: flash('Enter a unique category name.','danger')
    return render_template('categories.html',items=Category.query.all())
@bp.route('/categories/<int:cid>/edit',methods=['GET','POST'])
@role_required(*INV)
def category_edit(cid):
    item=Category.query.get_or_404(cid)
    if request.method=='POST':
        name=request.form.get('name','').strip(); parent=request.form.get('parent_id',type=int)
        if not name or parent==item.id: flash('Enter a name and choose a different parent.','danger')
        else: item.name=name; item.parent_id=parent; db.session.commit(); flash('Category updated.','success'); return redirect(url_for('main.categories'))
    return render_template('category_form.html',item=item,categories=Category.query.filter(Category.id!=cid).all())
@bp.route('/categories/<int:cid>/delete',methods=['POST'])
@role_required(*INV)
def category_delete(cid):
    item=Category.query.get_or_404(cid)
    if Product.query.filter_by(category_id=cid).first() or Category.query.filter_by(parent_id=cid).first(): flash('Category has products or sub-categories and cannot be deleted.','danger')
    else: db.session.delete(item); db.session.commit(); flash('Category deleted.','success')
    return redirect(url_for('main.categories'))
@bp.route('/warehouses',methods=['GET','POST'])
@role_required(*INV)
def warehouses():
    if request.method=='POST':
        name=request.form.get('name','').strip()
        if name and not Warehouse.query.filter_by(name=name).first(): db.session.add(Warehouse(name=name,location=request.form.get('location'))); db.session.commit(); flash('Warehouse saved.','success')
        else: flash('Enter a unique warehouse name.','danger')
    return render_template('warehouses.html',items=Warehouse.query.all())
@bp.route('/warehouses/<int:wid>/edit',methods=['GET','POST'])
@role_required(*INV)
def warehouse_edit(wid):
    item=Warehouse.query.get_or_404(wid)
    if request.method=='POST':
        name=request.form.get('name','').strip()
        if not name: flash('Warehouse name is required.','danger')
        else: item.name=name; item.location=request.form.get('location','').strip(); db.session.commit(); flash('Warehouse updated.','success'); return redirect(url_for('main.warehouses'))
    return render_template('warehouse_form.html',item=item)
@bp.route('/warehouses/<int:wid>/delete',methods=['POST'])
@role_required(*INV)
def warehouse_delete(wid):
    item=Warehouse.query.get_or_404(wid)
    if StockLevel.query.filter_by(warehouse_id=wid).first() or StockTransaction.query.filter_by(warehouse_id=wid).first(): flash('Warehouse has stock or history and cannot be deleted.','danger')
    else: db.session.delete(item); db.session.commit(); flash('Warehouse deleted.','success')
    return redirect(url_for('main.warehouses'))

@bp.route('/suppliers',methods=['GET','POST'])
@role_required(*PROC,'Administrator','Auditor')
def suppliers():
    query=Supplier.query
    if request.args.get('q'): query=query.filter(db.or_(Supplier.name.ilike('%'+request.args['q']+'%'),Supplier.gstin.ilike('%'+request.args['q']+'%'),Supplier.contact_person.ilike('%'+request.args['q']+'%')))
    if request.args.get('status')=='active': query=query.filter_by(is_active=True)
    elif request.args.get('status')=='inactive': query=query.filter_by(is_active=False)
    if request.method=='POST':
        if session_user().role!='Procurement Officer': return 'Forbidden',403
        name=request.form.get('name','').strip(); gst=request.form.get('gstin','').strip()
        if not name: flash('Supplier name is required.','danger')
        else:
            if Supplier.query.filter(db.or_(db.func.lower(Supplier.name)==name.lower(),db.and_(Supplier.gstin!='',Supplier.gstin==gst))).first(): flash('Warning: a supplier with a similar name or GSTIN already exists.','warning')
            s=Supplier(name=name,contact_person=request.form.get('contact_person'),phone=request.form.get('phone'),email=request.form.get('email'),address=request.form.get('address'),gstin=gst,payment_terms=request.form.get('payment_terms')); db.session.add(s); db.session.commit(); return redirect(url_for('main.suppliers'))
    supplier_q=Supplier.query
    if request.args.get('q'): supplier_q=supplier_q.filter(db.or_(Supplier.name.ilike('%'+request.args['q']+'%'),Supplier.gstin.ilike('%'+request.args['q']+'%')))
    all_suppliers=query.order_by(Supplier.name).all()
    performance={}
    for supplier in all_suppliers:
        pos=PurchaseOrder.query.filter_by(supplier_id=supplier.id).all(); on_time=[]; lead_values=[l.lead_time_days for l in ProductSupplier.query.filter_by(supplier_id=supplier.id).all()]
        for po in pos:
            if po.status!='Fully Received': continue
            receipt=StockTransaction.query.filter_by(reference=po.po_number,type='RECEIPT').order_by(StockTransaction.timestamp.desc()).first()
            if receipt:
                lead=max([l.lead_time_days for l in ProductSupplier.query.filter_by(supplier_id=supplier.id).all()] or [7]); on_time.append((receipt.timestamp.date()-po.order_date).days<=lead)
        performance[supplier.id]={'on_time':(100*sum(on_time)/len(on_time) if on_time else None),'lead':(sum(lead_values)/len(lead_values) if lead_values else None)}
    return render_template('suppliers.html',items=all_suppliers,links=ProductSupplier.query.all(),products=Product.query.filter_by(is_active=True).all(),purchase_counts={s.id:PurchaseOrder.query.filter_by(supplier_id=s.id).count() for s in all_suppliers},purchase_values={s.id:sum(p.total for p in PurchaseOrder.query.filter_by(supplier_id=s.id)) for s in all_suppliers},performance=performance)

@bp.route('/suppliers/<int:sid>')
@role_required(*PROC,'Administrator','Auditor')
def supplier_detail(sid):
    supplier=Supplier.query.get_or_404(sid)
    orders=PurchaseOrder.query.filter_by(supplier_id=sid).order_by(PurchaseOrder.id.desc()).all()
    links=ProductSupplier.query.filter_by(supplier_id=sid).all()
    on_time=[]; lead_values=[l.lead_time_days for l in links]
    for po in orders:
        if po.status!='Fully Received': continue
        receipt=StockTransaction.query.filter_by(reference=po.po_number,type='RECEIPT').order_by(StockTransaction.timestamp.desc()).first()
        if receipt: on_time.append((receipt.timestamp.date()-po.order_date).days<=max(lead_values or [7]))
    return render_template('supplier_detail.html',supplier=supplier,orders=orders,links=links,products=Product.query.filter_by(is_active=True).all(),on_time=(sum(on_time)*100/len(on_time) if on_time else None),average_lead=(sum(lead_values)/len(lead_values) if lead_values else None))
@bp.route('/suppliers/<int:sid>/edit',methods=['GET','POST'])
@role_required(*PROC)
def supplier_edit(sid):
    supplier=Supplier.query.get_or_404(sid)
    if request.method=='POST':
        name=request.form.get('name','').strip(); gst=request.form.get('gstin','').strip()
        if not name: flash('Supplier name is required.','danger')
        else:
            duplicate=Supplier.query.filter(Supplier.id!=sid,db.or_(db.func.lower(Supplier.name)==name.lower(),db.and_(gst!='',Supplier.gstin==gst))).first()
            if duplicate: flash('Warning: another supplier has a matching name or GSTIN. Review before saving.','warning')
            supplier.name=name; supplier.gstin=gst; supplier.contact_person=request.form.get('contact_person','').strip(); supplier.phone=request.form.get('phone','').strip(); supplier.email=request.form.get('email','').strip(); supplier.address=request.form.get('address','').strip(); supplier.payment_terms=request.form.get('payment_terms','').strip(); db.session.commit(); flash('Supplier updated.','success'); return redirect(url_for('main.supplier_detail',sid=sid))
    return render_template('supplier_form.html',supplier=supplier)

def session_user(): return db.session.get(User,session.get('user_id'))

@bp.route('/suppliers/<int:sid>/toggle',methods=['POST'])
@role_required(*PROC)
def supplier_toggle(sid):
    supplier=Supplier.query.get_or_404(sid); supplier.is_active=not supplier.is_active; db.session.commit(); flash('Supplier status updated.','success'); return redirect(url_for('main.suppliers'))
@bp.route('/suppliers/<int:sid>/link',methods=['POST'])
@role_required(*PROC)
def supplier_link(sid):
    p=Product.query.get_or_404(request.form.get('product_id',type=int)); preferred=request.form.get('is_preferred')=='on'
    if preferred:
        for x in ProductSupplier.query.filter_by(product_id=p.id): x.is_preferred=False
    link=ProductSupplier.query.filter_by(product_id=p.id,supplier_id=sid).first() or ProductSupplier(product_id=p.id,supplier_id=sid)
    link.supplier_price=request.form.get('supplier_price',type=float) if request.form.get('supplier_price') else p.cost_price; link.lead_time_days=request.form.get('lead_time_days',type=int) or 7; link.is_preferred=preferred; db.session.add(link); db.session.commit(); flash('Supplier link saved.','success'); return redirect(url_for('main.supplier_detail',sid=sid))
@bp.route('/suppliers/<int:sid>/links/<int:linkid>/remove',methods=['POST'])
@role_required(*PROC)
def supplier_unlink(sid,linkid):
    link=ProductSupplier.query.filter_by(id=linkid,supplier_id=sid).first_or_404(); db.session.delete(link); db.session.commit(); flash('Product link removed.','success'); return redirect(url_for('main.supplier_detail',sid=sid))
@bp.route('/products/<int:pid>/suppliers/link',methods=['POST'])
@role_required(*INV,'Procurement Officer')
def product_supplier_link(pid):
    product=Product.query.get_or_404(pid); sid=request.form.get('supplier_id',type=int); supplier=Supplier.query.get_or_404(sid)
    if not supplier.is_active: flash('Choose an active supplier.','danger')
    else:
        if request.form.get('is_preferred')=='on':
            for link in ProductSupplier.query.filter_by(product_id=pid): link.is_preferred=False
        link=ProductSupplier.query.filter_by(product_id=pid,supplier_id=sid).first() or ProductSupplier(product_id=pid,supplier_id=sid)
        link.supplier_price=request.form.get('supplier_price',type=float) if request.form.get('supplier_price') else product.cost_price; link.lead_time_days=request.form.get('lead_time_days',type=int) or 7; link.is_preferred=request.form.get('is_preferred')=='on'; db.session.add(link); db.session.commit(); flash('Supplier linked to product.','success')
    return redirect(url_for('main.product_detail',pid=pid))
@bp.route('/suppliers.csv')
@role_required(*PROC)
def suppliers_csv():
    o=io.StringIO(); w=csv.writer(o); w.writerow(['Name','Contact','Phone','Email','GSTIN','Active'])
    for s in Supplier.query: w.writerow([s.name,s.contact_person,s.phone,s.email,s.gstin,s.is_active])
    return Response(o.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=suppliers.csv'})

def recalculate_po(po):
    po.subtotal=sum(line.ordered_qty*line.unit_price for line in po.lines)
    setting=db.session.get(Setting,'tax_rate'); po.tax=po.subtotal*(float(setting.value) if setting else 18)/100; po.total=po.subtotal+po.tax

def make_po(supplier_id, creator, product_qty, status='Draft'):
    s=Supplier.query.get_or_404(supplier_id)
    if not s.is_active: raise ValueError('Inactive suppliers cannot be selected.')
    po=PurchaseOrder(po_number='PENDING',supplier_id=s.id,created_by=creator,status='Draft'); db.session.add(po); db.session.flush(); po.po_number=f'PO-{po.id:04d}'
    for row in product_qty:
        pid,qty=row[:2]
        link=ProductSupplier.query.filter_by(product_id=pid,supplier_id=s.id).first(); p=Product.query.get_or_404(pid); price=link.supplier_price if link else p.cost_price
        po.lines.append(PurchaseOrderLine(product_id=pid,ordered_qty=qty,unit_price=price)); po.subtotal+=qty*price
    recalculate_po(po)
    return po
@bp.route('/purchase-orders')
@role_required(*PROC,'Warehouse Staff','Auditor','Administrator')
def purchase_orders():
    q=PurchaseOrder.query
    if request.args.get('status'): q=q.filter_by(status=request.args['status'])
    elif request.args.get('scope')=='open': q=q.filter(PurchaseOrder.status.in_(['Approved','Sent to Supplier','Partially Received']))
    if request.args.get('supplier',type=int): q=q.filter_by(supplier_id=request.args.get('supplier',type=int))
    if request.args.get('q'): q=q.filter(PurchaseOrder.po_number.ilike('%'+request.args['q']+'%'))
    if request.args.get('product',type=int): q=q.join(PurchaseOrderLine).filter(PurchaseOrderLine.product_id==request.args.get('product',type=int))
    if request.args.get('from'): q=q.filter(PurchaseOrder.order_date>=request.args['from'])
    if request.args.get('to'): q=q.filter(PurchaseOrder.order_date<=request.args['to'])
    items=q.order_by(PurchaseOrder.id.desc()).paginate(page=request.args.get('page',1,type=int),per_page=20,error_out=False)
    pending=PurchaseOrder.query.filter_by(status='Pending Approval').count() if session_user().is_approver else 0
    return render_template('purchase_orders.html',items=items,suppliers=Supplier.query.all(),pending_approval=pending)
@bp.route('/purchase-orders/new',methods=['GET','POST'])
@role_required(*PROC)
def po_new():
    if request.method=='POST':
        try:
            sid=request.form.get('supplier_id',type=int); supplier=Supplier.query.get_or_404(sid)
            if not supplier.is_active: raise ValueError('Select an active supplier.')
            po=PurchaseOrder(po_number='PENDING',supplier_id=sid,created_by=session['user_id'],status='Draft',order_date=date.fromisoformat(request.form.get('order_date') or date.today().isoformat()),notes=request.form.get('notes','').strip())
            db.session.add(po); db.session.flush(); po.po_number=f'PO-{po.id:04d}'; db.session.commit(); flash(f'{po.po_number} draft created. Add line items, then submit it.','success'); return redirect(url_for('main.po_detail',poid=po.id))
        except Exception as e: db.session.rollback(); flash(str(e),'danger')
    return render_template('po_form.html',suppliers=Supplier.query.filter_by(is_active=True).all())
@bp.route('/purchase-orders/<int:poid>')
@role_required(*PROC,'Warehouse Staff','Auditor','Administrator')
def po_detail(poid):
    po=PurchaseOrder.query.get_or_404(poid); receipts=StockTransaction.query.filter_by(reference=po.po_number,type='RECEIPT').order_by(StockTransaction.timestamp.desc()).all()
    return render_template('po_detail.html',po=po,warehouses=Warehouse.query.all(),products=Product.query.filter_by(is_active=True).all(),links=ProductSupplier.query.filter_by(supplier_id=po.supplier_id).all(),receipts=receipts,receipt_idempotency_key=uuid.uuid4().hex,stock_levels={(x.product_id,x.warehouse_id):x.quantity_on_hand for x in StockLevel.query.all()})
@bp.route('/purchase-orders/<int:poid>/lines',methods=['POST'])
@role_required(*PROC)
def po_add_line(poid):
    po=PurchaseOrder.query.get_or_404(poid)
    try:
        if po.created_by!=session['user_id']: raise ValueError('Only the draft creator can edit its lines.')
        if po.status!='Draft': raise ValueError('Lines can only be changed on a draft PO.')
        pid=request.form.get('product_id',type=int); qty=request.form.get('quantity',type=int); price=request.form.get('unit_price',type=float)
        if not pid or not qty or qty<=0: raise ValueError('Choose a product and enter a quantity greater than zero.')
        product=Product.query.get_or_404(pid); link=ProductSupplier.query.filter_by(product_id=pid,supplier_id=po.supplier_id).first(); price=price if price is not None else (link.supplier_price if link else product.cost_price)
        default_price=link.supplier_price if link else product.cost_price
        if price!=default_price and 'po.price.override' not in permissions_for(session_user()): raise ValueError('Price overrides require explicit permission.')
        if price<0: raise ValueError('Unit price cannot be negative.')
        line=PurchaseOrderLine.query.filter_by(po_id=po.id,product_id=pid).first()
        if line: line.ordered_qty+=qty; line.unit_price=price
        else: po.lines.append(PurchaseOrderLine(product_id=pid,ordered_qty=qty,unit_price=price))
        recalculate_po(po); db.session.commit(); flash('Purchase order line saved.','success')
    except Exception as e: db.session.rollback(); flash(str(e),'danger')
    return redirect(url_for('main.po_detail',poid=poid))
@bp.route('/purchase-orders/<int:poid>/lines/<int:lineid>/edit',methods=['POST'])
@role_required(*PROC)
def po_edit_line(poid,lineid):
    po=PurchaseOrder.query.get_or_404(poid); line=PurchaseOrderLine.query.filter_by(id=lineid,po_id=poid).first_or_404()
    try:
        if po.created_by!=session['user_id']: raise ValueError('Only the draft creator can edit its lines.')
        if po.status!='Draft': raise ValueError('Lines can only be changed on a draft PO.')
        qty=request.form.get('quantity',type=int); price=request.form.get('unit_price',type=float)
        if not qty or qty<=0 or price is None or price<0: raise ValueError('Enter a positive quantity and non-negative price.')
        link=ProductSupplier.query.filter_by(product_id=line.product_id,supplier_id=po.supplier_id).first(); default_price=link.supplier_price if link else line.product.cost_price
        if price!=line.unit_price and price!=default_price and 'po.price.override' not in permissions_for(session_user()): raise ValueError('Price overrides require explicit permission.')
        line.ordered_qty=qty; line.unit_price=price; recalculate_po(po); db.session.commit(); flash('Line updated.','success')
    except Exception as e: db.session.rollback(); flash(str(e),'danger')
    return redirect(url_for('main.po_detail',poid=poid))
@bp.route('/purchase-orders/<int:poid>/lines/<int:lineid>/remove',methods=['POST'])
@role_required(*PROC)
def po_remove_line(poid,lineid):
    po=PurchaseOrder.query.get_or_404(poid); line=PurchaseOrderLine.query.filter_by(id=lineid,po_id=poid).first_or_404()
    if po.status=='Draft' and po.created_by==session['user_id']: db.session.delete(line); recalculate_po(po); db.session.commit(); flash('Line removed.','success')
    else: flash('Lines can only be removed from a draft PO.','danger')
    return redirect(url_for('main.po_detail',poid=poid))
@bp.route('/purchase-orders/<int:poid>/submit',methods=['POST'])
@role_required(*PROC)
def po_submit(poid):
    po=PurchaseOrder.query.get_or_404(poid)
    try:
        if po.status!='Draft': raise ValueError('Only a draft PO can be submitted.')
        if not po.lines: raise ValueError('Add at least one product line before submitting.')
        recalculate_po(po); setting=db.session.get(Setting,'po_approval_limit'); limit=float(setting.value) if setting else 50000
        if po.total<=limit: po.status='Approved'; po.approved_by=session['user_id']; po.approved_at=datetime.utcnow(); flash('Auto-approved, under the limit.','success')
        else: po.status='Pending Approval'; flash('Purchase order submitted for approval.','success')
        po.rejection_reason=None; db.session.commit()
    except Exception as e: db.session.rollback(); flash(str(e),'danger')
    return redirect(url_for('main.po_detail',poid=poid))
@bp.route('/purchase-orders/<int:poid>/approve',methods=['POST'])
@role_required(*READ)
def po_approve(poid):
    po=PurchaseOrder.query.get_or_404(poid)
    u=db.session.get(User,session['user_id'])
    other_approvers=User.query.filter_by(is_approver=True,is_active=True).count()
    if not u.is_approver: abort(403)
    elif po.status!='Pending Approval': flash('This PO is not awaiting approval.','danger')
    elif po.created_by==u.id and other_approvers>1: flash('You cannot approve your own PO while another approver is available.','danger')
    else: po.status='Approved'; po.approved_by=u.id; po.approved_at=datetime.utcnow(); db.session.commit(); flash('PO approved.','success')
    return redirect(url_for('main.po_detail',poid=poid))
@bp.route('/purchase-orders/<int:poid>/reject',methods=['POST'])
@role_required(*READ)
def po_reject(poid):
    po=PurchaseOrder.query.get_or_404(poid); u=session_user(); reason=request.form.get('reason','').strip()
    if not u.is_approver: abort(403)
    if po.status!='Pending Approval' or not reason: flash('A pending approval and rejection reason are required.','danger')
    elif po.created_by==u.id and User.query.filter_by(is_approver=True,is_active=True).count()>1: flash('You cannot reject your own PO while another approver is available.','danger')
    else: po.status='Draft'; po.rejection_reason=reason; po.approved_by=None; po.approved_at=None; db.session.commit(); flash('PO returned to Draft with your rejection reason.','success')
    return redirect(url_for('main.po_detail',poid=poid))
@bp.route('/purchase-orders/<int:poid>/send',methods=['POST'])
@role_required(*PROC,'Administrator')
def po_send(poid):
    po=PurchaseOrder.query.get_or_404(poid)
    if po.status=='Approved': po.status='Sent to Supplier'; db.session.commit(); flash('PO marked as sent to supplier.','success')
    else: flash('Only an approved PO can be sent.','danger')
    return redirect(url_for('main.po_detail',poid=poid))
@bp.route('/purchase-orders/<int:poid>/receive',methods=['POST'])
@role_required(*WH,'Procurement Officer','Inventory Manager')
def po_receive(poid):
    try:
        accept=request.form.get('accept_overdelivery')=='on'
        if accept and session_user().role not in {'Administrator','Procurement Officer'}: abort(403)
        receive_goods(poid,request.form.get('warehouse_id',type=int),request.form,session['user_id'],accept,request.form.get('idempotency_key')); flash('Receipt saved and stock updated.','success')
    except Exception as e: flash(str(e),'danger')
    return redirect(url_for('main.po_detail',poid=poid))
@bp.route('/purchase-orders/<int:poid>/cancel',methods=['POST'])
@role_required(*PROC)
def po_cancel(poid):
    po=PurchaseOrder.query.get_or_404(poid); reason=request.form.get('reason','').strip()
    if not reason: flash('Cancellation reason is required.','danger')
    elif po.status not in {'Draft','Pending Approval','Approved'} or any(l.received_qty for l in po.lines): flash('This PO cannot be cancelled.','danger')
    else: po.status='Cancelled'; po.cancel_reason=reason; db.session.commit(); flash('PO cancelled.','success')
    return redirect(url_for('main.po_detail',poid=poid))
@bp.route('/purchase-orders/<int:poid>/print')
@role_required(*READ)
def po_print(poid):
    po=PurchaseOrder.query.get_or_404(poid)
    if po.status not in {'Approved','Sent to Supplier','Partially Received','Fully Received'}: abort(403)
    return render_template('po_print.html',po=po,company_name=db.session.get(Setting,'company_name'),company_address=db.session.get(Setting,'company_address'))

@bp.route('/sales/new',methods=['GET','POST'])
@role_required(*SALE)
def sale_new():
    if request.method=='POST':
        try:
            items=[]
            for pid,qty in zip(request.form.getlist('product_id'),request.form.getlist('quantity')):
                if pid and qty and int(qty)>0:
                    p=Product.query.get_or_404(int(pid)); items.append({'product_id':p.id,'quantity':int(qty),'unit_price':p.selling_price})
            discount=request.form.get('discount',type=float) or 0; subtotal=sum(x['quantity']*x['unit_price'] for x in items); setting=db.session.get(Setting,'max_discount_percent'); maximum=float(setting.value) if setting else 10
            if not items: raise ValueError('Add at least one item.')
            if discount>subtotal*maximum/100: raise ValueError(f'Discount cannot exceed {maximum}% (₹{subtotal*maximum/100:.2f}).')
            sale=complete_sale(items,session['user_id'],request.form.get('warehouse_id',type=int),request.form.get('payment_method'),discount,request.form.get('customer_name'),request.form.get('customer_phone'),request.form.get('idempotency_key')); flash(f'Sale recorded: {sale.invoice_number}.','success'); return redirect(url_for('main.invoice',saleid=sale.id))
        except Exception as e: db.session.rollback(); flash(str(e),'danger')
    return render_template('sale_form.html',products=Product.query.filter_by(is_active=True).all(),warehouses=Warehouse.query.all(),idempotency_key=uuid.uuid4().hex)
@bp.route('/sales')
@role_required(*READ)
def sales():
    query=SalesTransaction.query
    if session_user().role=='Sales Staff': query=query.filter_by(processed_by=session['user_id'])
    return render_template('sales.html',items=query.order_by(SalesTransaction.sale_date.desc()).all())
@bp.route('/sales/<int:saleid>')
@role_required(*READ)
def invoice(saleid):
    sale=SalesTransaction.query.get_or_404(saleid)
    if session_user().role=='Sales Staff' and sale.processed_by!=session['user_id']: abort(404)
    return render_template('invoice.html',sale=sale,warehouses=Warehouse.query.all(),return_idempotency_keys={line.id:uuid.uuid4().hex for line in sale.lines})
@bp.route('/sales/<int:saleid>/return/<int:lineid>',methods=['POST'])
@role_required(*SALE)
def sale_return(saleid,lineid):
    line=SalesTransactionLine.query.filter_by(id=lineid,sale_id=saleid).first_or_404()
    if session_user().role=='Sales Staff' and line.sale.processed_by!=session['user_id']: abort(404)
    try:
        process_return(lineid,request.form.get('quantity',type=int),session['user_id'],request.form.get('warehouse_id',type=int),request.form.get('idempotency_key')); flash('Return recorded.','success')
    except Exception as e: flash(str(e),'danger')
    return redirect(url_for('main.invoice',saleid=saleid))

@bp.route('/alerts')
@role_required(*INV,'Procurement Officer','Auditor','Administrator')
def alerts():
    items=LowStockAlert.query.filter_by(status='Open').all()
    preferred={x.product_id:x.supplier.name for x in ProductSupplier.query.filter_by(is_preferred=True).all()}
    levels={(x.product_id,x.warehouse_id):x.quantity_on_hand for x in StockLevel.query.all()}
    return render_template('alerts.html',items=items,levels=levels,preferred=preferred)
@bp.route('/alerts/create-po',methods=['POST'])
@role_required(*PROC)
def alert_po():
    ids=request.form.getlist('alert_id'); groups={}
    try:
        for alert in LowStockAlert.query.filter(LowStockAlert.id.in_(ids),LowStockAlert.status=='Open').all():
            link=ProductSupplier.query.filter_by(product_id=alert.product_id,is_preferred=True).join(Supplier).filter(Supplier.is_active==True).first()
            if not link: continue
            groups.setdefault(link.supplier_id,[]).append((alert.product_id,alert.product.reorder_qty))
        for sid,lines in groups.items(): make_po(sid,session['user_id'],lines,'Draft')
        db.session.commit(); flash(f'Created {len(groups)} draft purchase order(s).','success')
    except Exception as e: db.session.rollback(); flash(str(e),'danger')
    return redirect(url_for('main.purchase_orders'))

@bp.route('/users',methods=['GET','POST'])
@role_required(*ADMIN)
def users():
    if request.method=='POST':
        username=request.form.get('username','').strip(); email=request.form.get('email','').strip()
        if User.query.filter(db.or_(User.username==username,User.email==email)).first(): flash('Username or email already exists.','danger')
        elif len(request.form.get('password',''))<8: flash('A temporary password of at least 8 characters is required.','danger')
        elif request.form.get('role') not in {'Administrator','Inventory Manager','Procurement Officer','Sales Staff','Warehouse Staff','Auditor'}: flash('Choose a supported role.','danger')
        else: db.session.add(User(username=username,email=email,password_hash=generate_password_hash(request.form['password']),role=request.form['role'],is_approver=request.form.get('is_approver')=='on')); db.session.commit(); flash('User created.','success')
    return render_template('users.html',items=User.query.order_by(User.username).all(),logs=LoginLog.query.order_by(LoginLog.timestamp.desc()).limit(100).all(),roles=['Administrator','Inventory Manager','Procurement Officer','Sales Staff','Warehouse Staff','Auditor'])
@bp.route('/users/<int:uid>/toggle',methods=['POST'])
@role_required(*ADMIN)
def user_toggle(uid):
    u=User.query.get_or_404(uid)
    if u.id==session['user_id']: flash('You cannot deactivate your own account.','danger')
    else: u.is_active=not u.is_active; db.session.commit()
    return redirect(url_for('main.users'))

@bp.route('/login-log')
@role_required(*ADMIN,'Auditor')
def login_log(): return render_template('login_log.html',logs=LoginLog.query.order_by(LoginLog.timestamp.desc()).limit(500).all())

@bp.route('/settings',methods=['GET','POST'])
@role_required(*ADMIN)
def settings():
    keys=['po_approval_limit','tax_rate','max_discount_percent']
    if request.method=='POST':
        try:
            for key in keys:
                value=float(request.form.get(key,''))
                if value<0: raise ValueError(f'{key} cannot be negative.')
                row=db.session.get(Setting,key) or Setting(key=key,value='0'); row.value=str(value); db.session.add(row)
            for key in ['company_name','company_address']:
                row=db.session.get(Setting,key) or Setting(key=key,value=''); row.value=request.form.get(key,'').strip(); db.session.add(row)
            db.session.commit(); flash('Settings saved.','success')
        except Exception as e: db.session.rollback(); flash(f'Invalid setting: {e}','danger')
    values={key:(db.session.get(Setting,key).value if db.session.get(Setting,key) else '') for key in keys}
    values.update({key:(db.session.get(Setting,key).value if db.session.get(Setting,key) else '') for key in ['company_name','company_address']})
    return render_template('settings.html',values=values)

@bp.route('/reports')
@role_required(*READ)
def reports():
    levels=StockLevel.query.all(); movements=StockTransaction.query.order_by(StockTransaction.timestamp.desc()).all(); sales=SalesTransaction.query.all(); pos=PurchaseOrder.query.all()
    days=[date.today()-timedelta(days=i) for i in reversed(range(7))]
    category_values={}
    for level in levels:
        name=level.product.category.name if level.product.category else 'Uncategorised'; category_values[name]=category_values.get(name,0)+level.quantity_on_hand*level.product.cost_price
    return render_template('reports.html',levels=levels,movements=movements,sales=sales,pos=pos,suppliers=Supplier.query.all(),alerts=LowStockAlert.query.filter_by(status='Open').all(),stock_cost=sum(x.quantity_on_hand*x.product.cost_price for x in levels),stock_labels=list(category_values),stock_values=list(category_values.values()),sales_days=[x.strftime('%a') for x in days],sales_values=[sum(s.total for s in sales if s.sale_date.date()==d) for d in days])
@bp.route('/reports/<kind>.csv')
@role_required(*READ)
def report_csv(kind):
    o=io.StringIO(); w=csv.writer(o)
    if kind=='stock':
        w.writerow(['SKU','Product','Warehouse','Quantity','Cost Value','Selling Value'])
        for x in StockLevel.query: w.writerow([x.product.sku,x.product.name,x.warehouse.name,x.quantity_on_hand,x.quantity_on_hand*x.product.cost_price,x.quantity_on_hand*x.product.selling_price])
    elif kind=='sales':
        w.writerow(['Invoice','Date','Customer','Revenue','Quantity','Estimated Profit','Profit Margin %','Tax','Discount','Staff'])
        for x in SalesTransaction.query:
            quantity=sum(line.quantity for line in x.lines); cost=sum(line.quantity*line.product.cost_price for line in x.lines); profit=x.total-x.tax-x.discount-cost; revenue=x.total-x.tax
            w.writerow([x.invoice_number,x.sale_date,x.customer_name,revenue,quantity,profit,(profit/revenue*100 if revenue else 0),x.tax,x.discount,x.staff.username])
    elif kind=='purchase':
        w.writerow(['PO','Supplier','Date','Status','Subtotal','Tax','Total'])
        for x in PurchaseOrder.query: w.writerow([x.po_number,x.supplier.name,x.order_date,x.status,x.subtotal,x.tax,x.total])
    elif kind=='movement':
        w.writerow(['Date','SKU','Product','Warehouse','Type','Quantity','Balance','Reason','Reference'])
        for x in StockTransaction.query: w.writerow([x.timestamp,x.product.sku,x.product.name,x.warehouse.name,x.type,x.quantity,x.balance_after,x.reason,x.reference])
    elif kind=='low':
        w.writerow(['Product','Warehouse','On Hand','Reorder Level','Status'])
        for a in LowStockAlert.query: w.writerow([a.product.name,a.warehouse.name,StockLevel.query.filter_by(product_id=a.product_id,warehouse_id=a.warehouse_id).first().quantity_on_hand,a.product.reorder_level,a.status])
    elif kind=='supplier':
        w.writerow(['Supplier','PO Count','Order Value'])
        for s in Supplier.query: w.writerow([s.name,len(s.purchase_orders) if hasattr(s,'purchase_orders') else PurchaseOrder.query.filter_by(supplier_id=s.id).count(),sum(x.total for x in PurchaseOrder.query.filter_by(supplier_id=s.id))])
    else: return 'Unknown report',404
    return Response(o.getvalue(),mimetype='text/csv',headers={'Content-Disposition':f'attachment; filename={kind}-report.csv'})