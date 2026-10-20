import pytest
from werkzeug.security import check_password_hash
from app import create_app
from models import db, User, Product, StockLevel, Warehouse, PurchaseOrder, PurchaseOrderLine, SalesTransaction, SalesTransactionLine, LoginLog
from services import adjust_stock, receive_goods, complete_sale, process_return, StockError

@pytest.fixture()
def app(tmp_path):
    app=create_app({'TESTING':True,'SQLALCHEMY_DATABASE_URI':f"sqlite:///{tmp_path / 'test.db'}",'SECRET_KEY':'test-secret'})
    with app.app_context():
        db.drop_all(); db.create_all()
        from seed import seed_data
        seed_data()
    yield app
    with app.app_context(): db.session.remove(); db.drop_all()

@pytest.fixture()
def client(app): return app.test_client()

def login(client, username, password='Pass@123'):
    return client.post('/login',data={'username':username,'password':password},follow_redirects=True)

def test_duplicate_sku_rejected(app,client):
    login(client,'inventory_manager')
    with app.app_context(): category=__import__('models').Category.query.first(); cid=category.id
    response=client.post('/products/new',data={'sku':'ELE-0001','name':'Duplicate','category_id':cid},follow_redirects=True)
    assert b'SKU already exists' in response.data

def test_sale_blocked_at_zero_stock(app):
    with app.app_context():
        p=Product.query.first(); w=Warehouse.query.first(); u=User.query.filter_by(role='Sales Staff').first(); level=StockLevel.query.filter_by(product_id=p.id,warehouse_id=w.id).first(); level.quantity_on_hand=0; db.session.commit()
        with pytest.raises(StockError,match='Insufficient stock'):
            complete_sale([{'product_id':p.id,'quantity':1,'unit_price':p.selling_price}],u.id,w.id,'Cash')
        assert SalesTransaction.query.count()==10

def test_receipt_increases_stock(app):
    with app.app_context():
        po=PurchaseOrder.query.filter_by(status='Approved').first(); po.status='Sent to Supplier'; line=po.lines[0]; w=Warehouse.query.first(); u=User.query.filter_by(role='Warehouse Staff').first(); before=StockLevel.query.filter_by(product_id=line.product_id,warehouse_id=w.id).first().quantity_on_hand
        receive_goods(po.id,w.id,{str(line.id):2},u.id)
        assert StockLevel.query.filter_by(product_id=line.product_id,warehouse_id=w.id).first().quantity_on_hand==before+2

def test_return_cannot_exceed_sold_quantity(app):
    with app.app_context():
        line=SalesTransaction.query.first().lines[0]; w=Warehouse.query.first(); u=User.query.filter_by(role='Sales Staff').first()
        with pytest.raises(StockError,match='Return exceeds'):
            process_return(line.id,line.quantity+1,u.id,w.id)

def test_account_locks_after_five_failed_logins(client,app):
    for _ in range(5): client.post('/login',data={'username':'sales_staff','password':'wrong'})
    with app.app_context():
        u=User.query.filter_by(username='sales_staff').first(); assert u.locked_until is not None; assert LoginLog.query.filter_by(username='sales_staff',success=False).count()==5
    response=client.post('/login',data={'username':'sales_staff','password':'Pass@123'},follow_redirects=True)
    assert b'Account is locked' in response.data
