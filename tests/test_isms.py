import pytest
from werkzeug.security import check_password_hash
from app import create_app
from models import db, User, Product, StockLevel, Warehouse, PurchaseOrder, PurchaseOrderLine, SalesTransaction, SalesTransactionLine, LoginLog, GoodsReceipt, StockTransaction
from services import adjust_stock, receive_goods, complete_sale, process_return, StockError, IdempotencyConflict

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
            complete_sale([{'product_id':p.id,'quantity':1,'unit_price':p.selling_price}],u.id,w.id,'Cash',idempotency_key='zero-stock-test')
        assert SalesTransaction.query.count()==10

def test_receipt_increases_stock(app):
    with app.app_context():
        po=PurchaseOrder.query.filter_by(status='Approved').first(); po.status='Sent to Supplier'; line=po.lines[0]; w=Warehouse.query.first(); u=User.query.filter_by(role='Warehouse Staff').first(); before=StockLevel.query.filter_by(product_id=line.product_id,warehouse_id=w.id).first().quantity_on_hand
        receive_goods(po.id,w.id,{str(line.id):2},u.id,idempotency_key='receipt-increase-test')
        assert StockLevel.query.filter_by(product_id=line.product_id,warehouse_id=w.id).first().quantity_on_hand==before+2

def test_return_cannot_exceed_sold_quantity(app):
    with app.app_context():
        line=SalesTransaction.query.first().lines[0]; w=Warehouse.query.first(); u=User.query.filter_by(role='Sales Staff').first()
        with pytest.raises(StockError,match='Return exceeds'):
            process_return(line.id,line.quantity+1,u.id,w.id,idempotency_key='excess-return-test')

def test_sale_idempotency_replays_without_duplicate_stock_or_ledger(app):
    with app.app_context():
        p=Product.query.filter_by(sku='FOO-0003').first(); w=Warehouse.query.first(); u=User.query.filter_by(role='Sales Staff').first()
        before=StockLevel.query.filter_by(product_id=p.id,warehouse_id=w.id).first().quantity_on_hand
        request=[{'product_id':p.id,'quantity':1}]
        first=complete_sale(request,u.id,w.id,'Cash',idempotency_key='sale-replay')
        sale_id=first.id
        replay=complete_sale(request,u.id,w.id,'Cash',idempotency_key='sale-replay')
        assert replay.id==sale_id and replay._idempotency_replayed
        assert StockLevel.query.filter_by(product_id=p.id,warehouse_id=w.id).first().quantity_on_hand==before-1
        assert StockTransaction.query.filter_by(type='SALE',reference=first.invoice_number).count()==1
        with pytest.raises(IdempotencyConflict,match='different request'):
            complete_sale([{'product_id':p.id,'quantity':2}],u.id,w.id,'Cash',idempotency_key='sale-replay')

def test_goods_receipt_idempotency_replays_without_duplicate_receipt_or_stock(app):
    with app.app_context():
        po=PurchaseOrder.query.filter_by(status='Approved').first(); po.status='Sent to Supplier'; line=po.lines[0]
        w=Warehouse.query.first(); u=User.query.filter_by(role='Warehouse Staff').first()
        before=StockLevel.query.filter_by(product_id=line.product_id,warehouse_id=w.id).first().quantity_on_hand
        quantities={str(line.id):1}
        first=receive_goods(po.id,w.id,quantities,u.id,idempotency_key='receipt-replay'); po_id=first.id
        replay=receive_goods(po.id,w.id,quantities,u.id,idempotency_key='receipt-replay')
        assert replay.id==po_id and replay._idempotency_replayed
        assert StockLevel.query.filter_by(product_id=line.product_id,warehouse_id=w.id).first().quantity_on_hand==before+1
        assert GoodsReceipt.query.filter_by(po_id=po.id).count()==1
        with pytest.raises(IdempotencyConflict,match='different request'):
            receive_goods(po.id,w.id,{str(line.id):2},u.id,idempotency_key='receipt-replay')

def test_return_idempotency_replays_without_duplicate_stock_or_return_quantity(app):
    with app.app_context():
        line=SalesTransaction.query.first().lines[0]; w=Warehouse.query.get(line.warehouse_id); u=User.query.filter_by(role='Sales Staff').first()
        before=StockLevel.query.filter_by(product_id=line.product_id,warehouse_id=w.id).first().quantity_on_hand
        original=line.returned_qty
        first=process_return(line.id,1,u.id,w.id,idempotency_key='return-replay')
        replay=process_return(line.id,1,u.id,w.id,idempotency_key='return-replay')
        assert replay.id==first.id and replay._idempotency_replayed
        assert db.session.get(SalesTransactionLine,line.id).returned_qty==original+1
        assert StockLevel.query.filter_by(product_id=line.product_id,warehouse_id=w.id).first().quantity_on_hand==before+1
        with pytest.raises(IdempotencyConflict,match='different request'):
            process_return(line.id,2,u.id,w.id,idempotency_key='return-replay')

def test_account_locks_after_five_failed_logins(client,app):
    for _ in range(5): client.post('/login',data={'username':'sales_staff','password':'wrong'})
    with app.app_context():
        u=User.query.filter_by(username='sales_staff').first(); assert u.locked_until is not None; assert LoginLog.query.filter_by(username='sales_staff',success=False).count()==5
    response=client.post('/login',data={'username':'sales_staff','password':'Pass@123'},follow_redirects=True)
    assert b'Account is locked' in response.data