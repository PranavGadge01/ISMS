from functools import wraps
from flask import abort, jsonify, session
from models import db, User

ROLE_PERMISSIONS = {
    'Administrator': {'*','po.price.override'},
    'Inventory Manager': {'product.view','stock.view','stock.adjust','stock.adjust.large','stock.adjust.approve','stock.transfer','stock.count','supplier.view','po.view','po.approve','po.cancel.approved','sale.view.all','alert.view','dashboard.view','report.stock','report.sales','report.purchase','report.movement','report.low','report.supplier'},
    'Procurement Officer': {'product.view','stock.view','supplier.view','supplier.manage','po.view','po.create','po.send','po.cancel','po.receive','po.receive.over','alert.view','alert.createpo','dashboard.view','report.purchase','report.supplier','report.low'},
    'Sales Staff': {'product.view','stock.view','sale.create','sale.return','sale.view.own','sale.view.all','dashboard.view','report.sales.own'},
    'Warehouse Staff': {'product.view','stock.view','stock.adjust','stock.transfer','stock.count','po.view','po.receive','alert.view','dashboard.view','report.movement','report.low'},
    'Auditor': {'product.view','stock.view','supplier.view','po.view','sale.view.all','alert.view','audit.business.read','dashboard.view','report.stock','report.sales','report.purchase','report.movement','report.low','report.supplier','approval.history'},
}
ROLE_PERMISSIONS['Administrator'] |= {'user.manage','settings.manage','loginlog.view','product.manage','stock.adjust','stock.adjust.large','stock.adjust.approve','stock.transfer','stock.count','supplier.view','supplier.manage','po.view','po.create','po.send','po.approve','po.cancel','po.cancel.approved','po.receive','po.receive.over','alert.view','alert.createpo','sale.create','sale.return','sale.view.all','approval.history'}
ROLE_PERMISSIONS['Inventory Manager'] |= {'product.manage','approval.history'}
ROLE_PERMISSIONS['Administrator'] |= {'audit.business.read','po.price.override'}

def permissions_for(user):
    perms=set(ROLE_PERMISSIONS.get(user.role,set()))
    if user.is_approver and user.role in {'Administrator','Inventory Manager'}: perms.add('po.approve'); perms.add('stock.adjust.approve')
    if '*' in perms: perms |= {p for values in ROLE_PERMISSIONS.values() for p in values if p!='*'}
    return sorted(perms)

def permission_required(permission):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args,**kwargs):
            user=db.session.get(User,session.get('user_id')) if session.get('user_id') else None
            if not user or not user.is_active: return jsonify(error='Authentication required.'),401
            if permission not in permissions_for(user): return jsonify(error='You do not have permission to perform this action.'),403
            return fn(*args,**kwargs)
        return wrapped
    return decorate
