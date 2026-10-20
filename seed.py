import os
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash
from models import db, User, Category, Warehouse, Product, StockLevel, StockTransaction, Supplier, ProductSupplier, PurchaseOrder, PurchaseOrderLine, SalesTransaction, SalesTransactionLine, Setting, LowStockAlert, StockAdjustmentRequest, Notification, POHistory, GoodsReceipt, GoodsReceiptLine

ROLES=['Administrator','Inventory Manager','Procurement Officer','Sales Staff','Warehouse Staff','Auditor']
def seed_data():
    if os.environ.get('ISMS_ENV','').lower()=='production':
        raise RuntimeError('Demo seed data is disabled in production. Set up the first administrator through the deployment setup process.')
    if User.query.first():
        aliases={'admin':'Administrator','invmgr':'Inventory Manager','procure':'Procurement Officer','sales':'Sales Staff','warehouse':'Warehouse Staff','auditor':'Auditor'}
        for username,role in aliases.items():
            user=User.query.filter_by(username=username).first()
            if not user:
                user=User.query.filter_by(role=role).first()
                if user and user.username==username: pass
                else:
                    user=User(username=username,email='demo.'+username+'@isms.local',full_name=role,password_hash=generate_password_hash('Pass@123'),role=role,is_approver=role in ('Administrator','Inventory Manager')); db.session.add(user); db.session.flush()
            if role in ('Administrator','Inventory Manager'):
                approver=User.query.filter_by(username=username).first()
                if approver: approver.is_approver=True
            demo=User.query.filter_by(username=username).first()
            if demo and not Notification.query.filter_by(user_id=demo.id).first(): db.session.add(Notification(user_id=demo.id,message=f'Welcome to the {role} workspace. Your demo data is ready.',link='/'))
        for key,value in [('adjustment_approval_threshold','100'),('company_name','Stockroom Supply Co.'),('company_address','Mumbai, India')]:
            if not db.session.get(Setting,key): db.session.add(Setting(key=key,value=value))
        db.session.commit(); return
    users=[]
    for role in ROLES:
        u=User(username=role.lower().replace(' ','_'),email=role.lower().replace(' ','_')+'@isms.local',full_name=role,password_hash=generate_password_hash('Pass@123'),role=role,is_approver=role in ('Administrator','Inventory Manager')); db.session.add(u); users.append(u)
    aliases={'admin':0,'invmgr':1,'procure':2,'sales':3,'warehouse':4,'auditor':5}; demo_users={}
    for username,index in aliases.items():
        role=ROLES[index]
        if users[index].username==username: demo_users[username]=users[index]; continue
        u=User(username=username,email='demo.'+username+'@isms.local',full_name=role,password_hash=generate_password_hash('Pass@123'),role=role,is_approver=role in ('Administrator','Inventory Manager')); db.session.add(u); demo_users[username]=u
    cats=[Category(name=x) for x in ['Electronics','Home','Food','Office']]; wh=[Warehouse(name='Main Warehouse',location='Mumbai'),Warehouse(name='Outlet Store',location='Pune')]; db.session.add_all(cats+wh); db.session.flush(); cats[3].parent_id=cats[1].id
    products=[]
    for i in range(12):
        p=Product(sku=f'{cats[i%4].name[:3].upper()}-{i+1:04d}',name=['Wireless Mouse','USB Keyboard','HDMI Cable','Desk Lamp','Storage Box','Water Bottle','Coffee Pack','Tea Pack','Notebook','Pen Set','Phone Charger','Earbuds'][i],category_id=cats[i%4].id,cost_price=100+i*35,selling_price=160+i*50,reorder_level=8,reorder_qty=30); db.session.add(p); products.append(p)
    suppliers=[Supplier(name=x,contact_person='Sales Desk',phone='9000000000',email=x.lower().replace(' ','')+'@example.local',gstin=f'27ABCDE1234{x[0]}1Z5',payment_terms='Net 30') for x in ['Acme Wholesale','Metro Distributors','Goodlife Supply']]; db.session.add_all(suppliers); db.session.flush(); db.session.flush()
    for i,p in enumerate(products): db.session.add(ProductSupplier(product_id=p.id,supplier_id=suppliers[i%3].id,supplier_price=p.cost_price,lead_time_days=5+i%5,is_preferred=True))
    db.session.add_all([Setting(key='po_approval_limit',value='50000'),Setting(key='tax_rate',value='18'),Setting(key='max_discount_percent',value='10'),Setting(key='adjustment_approval_threshold',value='100'),Setting(key='company_name',value='Stockroom Supply Co.'),Setting(key='company_address',value='Mumbai, India')]); db.session.flush()
    for i,p in enumerate(products):
        for j,w in enumerate(wh):
            qty=(3 if i in (0,1) and j==0 else 35+i+j*4); db.session.add(StockLevel(product_id=p.id,warehouse_id=w.id,quantity_on_hand=qty))
            db.session.add(__import__('models').StockTransaction(product_id=p.id,warehouse_id=w.id,user_id=users[0].id,type='RECEIPT',quantity=qty,balance_after=qty,reason='Opening balance',reference='SEED'))
            if qty<=p.reorder_level: db.session.add(LowStockAlert(product_id=p.id,warehouse_id=w.id))
    db.session.flush()
    statuses=['Draft','Pending Approval','Approved','Sent to Supplier','Partially Received','Fully Received','Cancelled']
    for idx,status in enumerate(statuses):
        qty=600 if status=='Pending Approval' else 10; received=(5 if status=='Partially Received' else qty if status=='Fully Received' else 0)
        po=PurchaseOrder(po_number=f'PO-{idx+1:04d}',supplier_id=suppliers[idx%3].id,created_by=demo_users['procure'].id,status=status,subtotal=1000,tax=180,total=1180,approved_by=demo_users['admin'].id if status in ('Approved','Sent to Supplier','Partially Received','Fully Received') else None,cancel_reason='Demo cancellation' if status=='Cancelled' else None)
        if status=='Pending Approval': po.subtotal=60000; po.tax=10800; po.total=70800
        db.session.add(po); db.session.flush(); po.lines.append(PurchaseOrderLine(product_id=products[idx].id,ordered_qty=qty,received_qty=received,unit_price=100)); db.session.add(POHistory(po_id=po.id,action='Created',user_id=demo_users['procure'].id));
        if status=='Partially Received' or status=='Fully Received':
            receipt=GoodsReceipt(po_id=po.id,warehouse_id=wh[0].id,received_by=users[4].id); db.session.add(receipt); db.session.flush(); receipt.lines.append(GoodsReceiptLine(product_id=products[idx].id,quantity=received)); level=StockLevel.query.filter_by(product_id=products[idx].id,warehouse_id=wh[0].id).first(); level.quantity_on_hand+=received; db.session.add(StockTransaction(product_id=products[idx].id,warehouse_id=wh[0].id,user_id=users[4].id,type='RECEIPT',quantity=received,balance_after=level.quantity_on_hand,reason='Seed purchase receipt',reference=po.po_number)); db.session.add(POHistory(po_id=po.id,action='Received',user_id=users[4].id,note='Demo receipt'))
    rejected=PurchaseOrder(po_number='PO-0008',supplier_id=suppliers[0].id,created_by=demo_users['procure'].id,status='Draft',subtotal=100,tax=18,total=118,rejection_reason='Please confirm the quantity.',rejected_by=demo_users['admin'].id); rejected.lines.append(PurchaseOrderLine(product_id=products[7].id,ordered_qty=1,unit_price=100)); db.session.add(rejected); db.session.flush(); db.session.add(POHistory(po_id=rejected.id,action='Rejected',user_id=demo_users['admin'].id,note=rejected.rejection_reason))
    db.session.flush()
    for i in range(10):
        p=products[(i+2)%12]; sale=SalesTransaction(invoice_number=f'INV-{i+1:04d}',processed_by=demo_users['sales'].id,sale_date=datetime.utcnow()-timedelta(days=i%7,hours=i),customer_name=f'Customer {i+1}',payment_method=['Cash','Card','UPI'][i%3],total=p.selling_price*2*1.18,tax=p.selling_price*2*.18); sale.lines.append(SalesTransactionLine(product_id=p.id,warehouse_id=wh[0].id,quantity=2,unit_price=p.selling_price)); db.session.add(sale); level=StockLevel.query.filter_by(product_id=p.id,warehouse_id=wh[0].id).first(); level.quantity_on_hand-=2; db.session.add(StockTransaction(product_id=p.id,warehouse_id=wh[0].id,user_id=demo_users['sales'].id,type='SALE',quantity=-2,balance_after=level.quantity_on_hand,reason='Demo sale',reference=sale.invoice_number))
    # One pending warehouse request plus one approved, flagged adjustment for the demo.
    db.session.add(StockAdjustmentRequest(product_id=products[8].id,warehouse_id=wh[0].id,direction='Decrease',quantity=150,reason='Damage',comment='Awaiting manager review',requested_by=users[4].id,status='Pending'))
    level=StockLevel.query.filter_by(product_id=products[9].id,warehouse_id=wh[0].id).first(); level.quantity_on_hand+=150; db.session.add(StockTransaction(product_id=products[9].id,warehouse_id=wh[0].id,user_id=users[1].id,type='ADJUSTMENT',quantity=150,balance_after=level.quantity_on_hand,reason='Approved large adjustment',reference='SEED-ADJ',is_flagged=True)); db.session.add(StockAdjustmentRequest(product_id=products[9].id,warehouse_id=wh[0].id,direction='Increase',quantity=150,reason='Count Correction',requested_by=users[4].id,status='Approved',decided_by=users[1].id,decided_at=datetime.utcnow(),decision_note='Verified count'))
    for role in ROLES:
        demo=demo_users[{'Administrator':'admin','Inventory Manager':'invmgr','Procurement Officer':'procure','Sales Staff':'sales','Warehouse Staff':'warehouse','Auditor':'auditor'}[role]]
        db.session.add(Notification(user_id=demo.id,message=f'Welcome to the {role} workspace. Your demo data is ready.',link='/'))
    db.session.commit()
