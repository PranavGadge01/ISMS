import pytest
from app import create_app
from models import db, User, SalesTransaction, StockLevel, StockTransaction, StockAdjustmentRequest

@pytest.fixture()
def app(tmp_path):
    app=create_app({'TESTING':True,'SQLALCHEMY_DATABASE_URI':f"sqlite:///{tmp_path / 'api.db'}",'SECRET_KEY':'api-secret'})
    with app.app_context():
        db.drop_all(); db.create_all()
        from seed import seed_data
        seed_data()
    yield app
    with app.app_context(): db.session.remove(); db.drop_all()

def api_login(client,username):
    response=client.post('/api/auth/login',json={'username':username,'password':'Pass@123'}); assert response.status_code==200; return response.get_json()['user']

def test_permissions_and_auditor_writes_blocked(app):
    c=app.test_client(); assert c.get('/api/auth/me').status_code==401
    user=api_login(c,'auditor'); assert 'product.view' in user['permissions']; assert c.get('/api/products').status_code==200
    assert c.get('/api/purchase-orders').status_code==200
    assert c.post('/api/products',json={'name':'Nope'}).status_code==403
    assert c.post('/api/stock/adjust',json={'product_id':1,'warehouse_id':1,'quantity':1,'direction':'Increase','reason':'Other'}).status_code==403
    assert c.post('/api/purchase-orders/2/approve').status_code==403
    assert c.post('/api/sales',json={}).status_code==403
    assert c.put('/api/settings',json={'tax_rate':0}).status_code==403

def test_sales_staff_only_sees_own_transactions_and_permissions(app):
    c=app.test_client(); api_login(c,'sales'); response=c.get('/api/sales'); assert response.status_code==200
    with app.app_context():
        me=User.query.filter_by(username='sales').first(); assert all(x['processed_by']==me.id for x in response.get_json()['items'])
        assert SalesTransaction.query.count()>=10
    assert c.get('/api/users').status_code==403

def test_po_approval_and_warehouse_adjustment_permissions(app):
    c=app.test_client(); api_login(c,'procure'); assert c.get('/api/purchase-orders').status_code==200; assert c.post('/api/purchase-orders/2/approve').status_code==403
    assert c.get('/api/approvals').status_code==403
    c=app.test_client(); api_login(c,'warehouse'); response=c.post('/api/stock/adjust',json={'product_id':1,'warehouse_id':1,'quantity':150,'direction':'Decrease','reason':'Damage'}); assert response.status_code==201; assert response.get_json()['status']=='Pending'
    with app.app_context():
        p=__import__('models').Product.query.get(1); lvl=StockLevel.query.filter_by(product_id=p.id,warehouse_id=1).first(); assert lvl.quantity_on_hand==3
        assert StockAdjustmentRequest.query.filter_by(requested_by=User.query.filter_by(username='warehouse').first().id,status='Pending').count()==1

def test_stock_quantity_matches_transaction_ledger_after_service_sequence(app):
    from services import adjust_stock,transfer_stock
    from models import Product, Warehouse
    with app.app_context():
        p=Product.query.filter_by(sku='FOO-0003').first(); warehouses=Warehouse.query.order_by(Warehouse.id).all(); uid=User.query.filter_by(username='invmgr').first().id
        adjust_stock(p.id,warehouses[0].id,3,uid,'Other'); transfer_stock(p.id,warehouses[0].id,warehouses[1].id,2,uid)
        for level in StockLevel.query.all():
            total=sum(t.quantity for t in StockTransaction.query.filter_by(product_id=level.product_id,warehouse_id=level.warehouse_id).all())
            assert total==level.quantity_on_hand

@pytest.mark.parametrize('username,allowed,denied',[
    ('admin','/api/users',None),
    ('invmgr','/api/approvals','/api/settings'),
    ('procure','/api/suppliers','POST /api/products'),
    ('sales','/api/sales','POST /api/stock/transfer'),
    ('warehouse','/api/purchase-orders','POST /api/purchase-orders'),
    ('auditor','/api/login-log','/api/users'),
])
def test_role_read_permissions(app,username,allowed,denied):
    c=app.test_client(); api_login(c,username)
    assert c.get(allowed).status_code==200
    if denied:
        if denied.startswith('POST '): assert c.post(denied[5:],json={}).status_code==403
        else: assert c.get(denied).status_code==403
