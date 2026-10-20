import os, secrets
from flask import Flask, render_template, send_from_directory, request
from models import db
from auth import auth
from routes import bp
from api import api

def create_app(test_config=None):
    app=Flask(__name__); app.config.update(SECRET_KEY=os.environ.get('SECRET_KEY') or secrets.token_urlsafe(32),SQLALCHEMY_DATABASE_URI='sqlite:///'+os.path.join(app.instance_path,'isms.db'),SQLALCHEMY_TRACK_MODIFICATIONS=False,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',SESSION_COOKIE_SECURE=os.environ.get('ISMS_ENV','').lower()=='production')
    if test_config: app.config.update(test_config)
    if os.environ.get('ISMS_ENV','').lower()=='production' and not os.environ.get('SECRET_KEY'):
        raise RuntimeError('SECRET_KEY must be configured when ISMS_ENV=production.')
    os.makedirs(app.instance_path,exist_ok=True); db.init_app(app); app.register_blueprint(auth); app.register_blueprint(bp); app.register_blueprint(api)
    @app.before_request
    def serve_react_routes():
        built=os.path.join(os.path.dirname(__file__),'frontend','dist')
        if not app.config.get('TESTING') and os.path.isfile(os.path.join(built,'index.html')) and not request.path.startswith(('/api/','/static/','/assets/','/legacy/')):
            return send_from_directory(built,'index.html')
    @app.get('/assets/<path:filename>')
    def react_assets(filename):
        return send_from_directory(os.path.join(os.path.dirname(__file__),'frontend','dist','assets'),filename)
    @app.errorhandler(403)
    def forbidden(error): return render_template('error.html',code=403,title='Access denied',message='You do not have permission to view this page.'),403
    @app.errorhandler(404)
    def not_found(error): return render_template('error.html',code=404,title='Page not found',message='We could not find that page.'),404
    @app.after_request
    def security_headers(response):
        response.headers.setdefault('X-Content-Type-Options','nosniff')
        response.headers.setdefault('X-Frame-Options','DENY')
        response.headers.setdefault('Referrer-Policy','strict-origin-when-cross-origin')
        return response
    @app.cli.command('init-db')
    def init_db():
        from seed import seed_data
        db.create_all(); seed_data(); print('Database ready.')
    with app.app_context():
        db.create_all()
        from sqlalchemy import inspect, text
        schema=inspect(db.engine)
        additions={'user':{'full_name':'VARCHAR(160)'},'warehouse':{'is_active':'BOOLEAN DEFAULT 1'},'stock_transaction':{'is_flagged':'BOOLEAN DEFAULT 0'},'purchase_order':{'rejection_reason':'VARCHAR(250)','notes':'VARCHAR(500)','approved_at':'DATETIME','submitted_at':'DATETIME','rejected_by':'INTEGER','sent_at':'DATETIME','cancelled_by':'INTEGER'},'sales_transaction_line':{'warehouse_id':'INTEGER'},'low_stock_alert':{'cleared_at':'DATETIME'}}
        for table,columns in additions.items():
            existing={column['name'] for column in schema.get_columns(table)}
            for name,sql_type in columns.items():
                if name not in existing:
                    with db.engine.begin() as connection: connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {sql_type}'))
        if os.environ.get('ISMS_ENV','').lower()!='production':
            from seed import seed_data
            seed_data()
    return app

app=create_app()
if __name__=='__main__': app.run(host='127.0.0.1',port=int(os.environ.get('PORT','5000')),debug=os.environ.get('FLASK_DEBUG')=='1')
