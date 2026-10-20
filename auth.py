import os
from datetime import datetime, timedelta
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, abort
from werkzeug.security import check_password_hash, generate_password_hash
from models import db, User, LoginLog

auth=Blueprint('auth',__name__)
def role_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapped(*a,**kw):
            user=db.session.get(User,session.get('user_id')) if session.get('user_id') else None
            if not user: return redirect(url_for('auth.login'))
            if roles and user.role not in roles: abort(403)
            return fn(*a,**kw)
        return wrapped
    return deco
@auth.before_app_request
def idle_timeout():
    if session.get('user_id'):
        last=session.get('last_seen')
        if last and datetime.utcnow()-datetime.fromisoformat(last)>timedelta(minutes=30): session.clear(); flash('Session expired after 30 minutes of inactivity. Please sign in again.','warning')
        elif session.get('user_id'): session['last_seen']=datetime.utcnow().isoformat()
@auth.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        username=request.form.get('username','').strip(); user=User.query.filter_by(username=username).first(); now=datetime.utcnow()
        ok=bool(user and user.is_active and (not user.locked_until or user.locked_until<=now) and check_password_hash(user.password_hash,request.form.get('password','')))
        db.session.add(LoginLog(username=username,user_id=user.id if user else None,success=ok,ip_address=request.remote_addr))
        if ok: user.failed_attempts=0; user.locked_until=None; db.session.commit(); session.clear(); session['user_id']=user.id; session['last_seen']=now.isoformat(); return redirect(url_for('main.dashboard'))
        if user and user.is_active:
            if user.locked_until and user.locked_until>now: msg=f'Account is locked. Try again in {max(1,int((user.locked_until-now).total_seconds()/60)+1)} minute(s).'
            else:
                user.failed_attempts+=1
                if user.failed_attempts>=5: user.locked_until=now+timedelta(minutes=15); user.failed_attempts=0
                msg='Invalid username or password.'
        else: msg='Invalid username or password.'
        db.session.commit(); flash(msg,'danger')
    return render_template('login.html')
@auth.route('/logout')
def logout(): session.clear(); return redirect(url_for('auth.login'))
@auth.route('/forgot',methods=['GET','POST'])
def forgot():
    if request.method=='POST':
        user=User.query.filter_by(username=request.form.get('username','').strip()).first()
        if user and os.environ.get('ISMS_ENV','').lower()!='production':
            import secrets
            token=secrets.token_urlsafe(24); session['reset_'+token]=user.id; session['reset_exp_'+token]=(datetime.utcnow()+timedelta(minutes=15)).isoformat(); return render_template('forgot.html',reset_url=url_for('auth.reset',token=token,_external=True))
        flash('If that account exists, a reset link is ready.','info')
    return render_template('forgot.html')
@auth.route('/reset/<token>',methods=['GET','POST'])
def reset(token):
    uid=session.get('reset_'+token); exp=session.get('reset_exp_'+token)
    if not uid or not exp or datetime.utcnow()>datetime.fromisoformat(exp): flash('Reset link is invalid or expired.','danger'); return redirect(url_for('auth.forgot'))
    if request.method=='POST':
        password=request.form.get('password','')
        if len(password)<8: flash('Use at least 8 characters.','danger')
        else:
            user=db.session.get(User,uid); user.password_hash=generate_password_hash(password); session.pop('reset_'+token,None); session.pop('reset_exp_'+token,None); db.session.commit(); flash('Password updated. Please sign in.','success'); return redirect(url_for('auth.login'))
    return render_template('reset.html')
