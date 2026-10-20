import pytest
from app import create_app
from models import db, Supplier, Product, Warehouse, PurchaseOrder, LowStockAlert, StockLevel, SalesTransaction

@pytest.fixture()
def app(tmp_path):
    test_app=create_app({'TESTING':True,'SQLALCHEMY_DATABASE_URI':f"sqlite:///{tmp_path / 'workflow.db'}",'SECRET_KEY':'workflow-secret'})
    with test_app.app_context():
        db.drop_all(); db.create_all()
        from seed import seed_data
        seed_data()
    yield test_app
    with test_app.app_context(): db.session.remove(); db.drop_all()

def sign_in(client, username):
    response=client.post('/login',data={'username':username,'password':'Pass@123'},follow_redirects=True)
    assert response.status_code==200
    return response

def test_requested_po_receipt_sale_alert_auditor_workflow(app):
    client=app.test_client()
    with app.app_context():
        supplier=Supplier.query.first(); products=Product.query.order_by(Product.id).all(); warehouse=Warehouse.query.first(); second=Warehouse.query.order_by(Warehouse.id).offset(1).first()
        supplier_id=supplier.id; product_a=products[4].id; product_b=products[5].id; warehouse_id=warehouse.id; second_id=second.id; sale_product=products[2]; sale_pid=sale_product.id
    sign_in(client,'procurement_officer')
    response=client.post('/purchase-orders/new',data={'supplier_id':supplier_id},follow_redirects=True)
    assert b'draft created' in response.data
    with app.app_context(): po=PurchaseOrder.query.order_by(PurchaseOrder.id.desc()).first(); po_id=po.id
    for pid,qty in [(product_a,600),(product_b,1)]:
        response=client.post(f'/purchase-orders/{po_id}/lines',data={'product_id':pid,'quantity':qty},follow_redirects=True); assert response.status_code==200
    with app.app_context():
        po=PurchaseOrder.query.get(po_id); assert po.total>50000
    response=client.post(f'/purchase-orders/{po_id}/submit',follow_redirects=True); assert b'Pending Approval' in response.data
    assert client.get('/purchase-orders?status=Pending%20Approval').status_code==200
    assert client.post(f'/purchase-orders/{po_id}/approve',follow_redirects=True).status_code==403
    sign_in(client,'administrator'); assert client.post(f'/purchase-orders/{po_id}/approve',follow_redirects=True).status_code==200
    sign_in(client,'procurement_officer'); assert client.post(f'/purchase-orders/{po_id}/send',follow_redirects=True).status_code==200
    assert client.get(f'/purchase-orders/{po_id}/print').status_code==200
    sign_in(client,'warehouse_staff')
    with app.app_context(): line_ids=[l.id for l in PurchaseOrder.query.get(po_id).lines]
    response=client.post(f'/purchase-orders/{po_id}/receive',data={'warehouse_id':warehouse_id,'idempotency_key':'workflow-partial-receipt',str(line_ids[0]):'100',str(line_ids[1]):'1'},follow_redirects=True); assert b'Partially Received' in response.data
    response=client.post(f'/purchase-orders/{po_id}/receive',data={'warehouse_id':warehouse_id,'idempotency_key':'workflow-final-receipt',str(line_ids[0]):'500'},follow_redirects=True); assert b'Fully Received' in response.data
    with app.app_context():
        initial=StockLevel.query.filter_by(product_id=sale_pid,warehouse_id=warehouse_id).first().quantity_on_hand; reorder=Product.query.get(sale_pid).reorder_level
    sign_in(client,'sales_staff')
    response=client.post('/sales/new',data={'warehouse_id':warehouse_id,'idempotency_key':'workflow-sale','product_id':[sale_pid],'quantity':[str(initial-reorder)],'payment_method':'Cash','discount':'0','customer_name':'Walkthrough'},follow_redirects=True)
    assert b'Invoice' in response.data
    with app.app_context():
        sale=SalesTransaction.query.order_by(SalesTransaction.id.desc()).first(); sale_id=sale.id; line_id=sale.lines[0].id
        assert LowStockAlert.query.filter_by(product_id=sale_pid,warehouse_id=warehouse_id,status='Open').first()
    sign_in(client,'procurement_officer')
    with app.app_context(): alert=LowStockAlert.query.filter_by(product_id=sale_pid,warehouse_id=warehouse_id,status='Open').first(); alert_id=alert.id
    response=client.post('/alerts/create-po',data={'alert_id':[str(alert_id)]},follow_redirects=True); assert b'draft purchase order' in response.data.lower()
    sign_in(client,'sales_staff'); response=client.post(f'/sales/{sale_id}/return/{line_id}',data={'quantity':'1','idempotency_key':'workflow-return'},follow_redirects=True); assert b'Return recorded' in response.data
    with app.app_context(): assert SalesTransaction.query.get(sale_id).lines[0].returned_qty==1
    sign_in(client,'inventory_manager')
    response=client.post('/products/new',data={'sku':'TEST-OPEN','name':'Opening stock product','category_id':'1','cost_price':'10','selling_price':'20','reorder_level':'2','reorder_qty':'5','opening_quantity':'7','opening_warehouse_id':str(warehouse_id)},follow_redirects=True); assert b'created' in response.data
    with app.app_context(): new=Product.query.filter_by(sku='TEST-OPEN').first(); new_id=new.id
    assert client.get('/stock').status_code==200
    assert client.post('/stock/adjust',data={'product_id':new_id,'warehouse_id':warehouse_id,'direction':'Increase','quantity':'3','reason':'Other'},follow_redirects=True).status_code==200
    assert client.post('/stock/transfer',data={'product_id':new_id,'from_warehouse_id':warehouse_id,'to_warehouse_id':second_id,'quantity':'2'},follow_redirects=True).status_code==200
    assert client.post('/stock/count',data={'warehouse_id':warehouse_id,str(new_id):'7'},follow_redirects=True).status_code==200
    sign_in(client,'auditor')
    assert client.get('/').status_code==200 and client.get('/reports').status_code==200
    assert b'New purchase order' not in client.get('/purchase-orders').data
    assert b'Add product' not in client.get('/products').data