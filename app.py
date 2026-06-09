import os
import json
import bcrypt
import secrets
import urllib.request
import re
import requests as http_requests
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import (Flask, render_template, request,
                   redirect, url_for, session, jsonify, flash, Response)

from database import db, User, Tool, UserTool, UsageLog, LaunchToken, EmailLog, PasswordReset, Bundle, BundleTool
import base64
import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from browser  import open_tool


# ── Try loading .env if present ───────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

PORT     = int(os.getenv('PORT', 5000))
NEON_URL = os.getenv('NEON_DB_URL', '').strip()

# ── App setup ─────────────────────────────────────────────────────────
app = Flask(__name__)

# Secret key – persisted in .secret_key so sessions survive restarts
secret = os.getenv('SECRET_KEY', '').strip()
if not secret or secret == 'change-this-to-a-random-secret':
    key_file = os.path.join(os.path.dirname(__file__), '.secret_key')
    if os.path.isfile(key_file):
        secret = open(key_file).read().strip()
    else:
        secret = secrets.token_hex(32)
        with open(key_file, 'w') as f:
            f.write(secret)
app.secret_key = secret

# ── Database: Neon (production) or SQLite (local) ────────────────────
if NEON_URL:
    if 'sslmode=' not in NEON_URL:
        sep = '&' if '?' in NEON_URL else '?'
        NEON_URL = f'{NEON_URL}{sep}sslmode=require'
    app.config['SQLALCHEMY_DATABASE_URI'] = NEON_URL
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True,
        'pool_recycle': 60,
        'pool_timeout': 60,
        'max_overflow': 5,
        'connect_args': {
            'connect_timeout': 30,
            'keepalives': 1,
            'keepalives_idle': 30,
            'keepalives_interval': 10,
            'keepalives_count': 5,
        },
    }
    print('[DB] Using Neon PostgreSQL')
else:
    # Local SQLite fallback – stored in instance/app.db
    db_path = os.path.join(os.path.dirname(__file__), 'instance', 'app.db')
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    print(f'[DB] Using local SQLite -> {db_path}')

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_HTTPONLY']  = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

db.init_app(app)


# ── Single-session check ──────────────────────────────────────────────
@app.before_request
def check_session():
    if 'uid' in session and 'session_token' in session:
        if request.endpoint in ('static', 'login', 'logout', 'forgot_password', 'reset_password', 'force_logout_verify', 'health', 'launch_page', 'launch_download', 'claim_launch', 'mobile_login', 'mobile_logout', 'mobile_tools', 'mobile_tool_image', 'mobile_launch'):
            return
        user = db.session.get(User, session['uid'])
        if not user or user.session_token != session['session_token']:
            if user and user.role == 'admin':
                return
            session.clear()
            from flask import redirect
            return redirect('/login')


# ── Decorators ────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def inner(*a, **kw):
        if 'uid' not in session:
            return redirect(url_for('login'))
        resp = f(*a, **kw)
        if isinstance(resp, Response):
            resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            resp.headers['Pragma'] = 'no-cache'
            resp.headers['Expires'] = '0'
        return resp
    return inner


def admin_required(f):
    @wraps(f)
    def inner(*a, **kw):
        if 'uid' not in session:
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            flash('Admin access only.', 'error')
            return redirect(url_for('user_dashboard'))
        resp = f(*a, **kw)
        if isinstance(resp, Response):
            resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            resp.headers['Pragma'] = 'no-cache'
            resp.headers['Expires'] = '0'
        return resp
    return inner


def retailer_required(f):
    @wraps(f)
    def inner(*a, **kw):
        if 'uid' not in session:
            return redirect(url_for('login'))
        if session.get('role') not in ('retailer', 'admin'):
            flash('Retailer access only.', 'error')
            return redirect(url_for('user_dashboard'))
        resp = f(*a, **kw)
        if isinstance(resp, Response):
            resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            resp.headers['Pragma'] = 'no-cache'
            resp.headers['Expires'] = '0'
        return resp
    return inner


# ── Health check ──────────────────────────────────────────────────────
@app.route('/health')
def health():
    return {'status': 'ok'}


# ── Auth routes ───────────────────────────────────────────────────────
@app.route('/')
def home():
    if 'uid' in session:
        if session['role'] == 'admin':
            return redirect(url_for('admin_dashboard'))
        if session['role'] == 'retailer':
            return redirect(url_for('retailer_dashboard'))
        return redirect(url_for('user_dashboard'))

    tools = Tool.query.filter_by(is_active=True).all()
    return render_template('home.html', tools=tools)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').encode()
        user = User.query.filter_by(username=username).first()
        if user and user.is_active and bcrypt.checkpw(password, user.password.encode()):
            if user.role != 'admin' and (user.session_token or user.api_token):
                if user.session_token and session.get('session_token') == user.session_token:
                    return redirect(url_for('home'))
                otp = f'{secrets.randbelow(900000) + 100000}'
                expires = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=10)
                PasswordReset.query.filter_by(user_id=user.id, used=False).delete()
                db.session.add(PasswordReset(user_id=user.id, otp=otp, expires_at=expires))
                db.session.commit()
                send_email_sync(
                    subject='Force Logout OTP - Saqib SEO Tools Agency',
                    html_body=render_template('emails/force_logout_otp.html', otp=otp, user=user),
                    recipient=user.username,
                )
                session['force_logout_user_id'] = user.id
                flash('This account is already logged in. An OTP has been sent to your email to verify and force logout.', 'info')
                return redirect(url_for('force_logout_verify'))
            tok = secrets.token_hex(32)
            user.session_token = tok
            db.session.commit()
            session['uid']           = user.id
            session['username']      = user.username
            session['role']          = user.role
            session['session_token'] = tok
            return redirect(url_for('home'))
        flash('Invalid username or password.', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    if 'uid' in session:
        user = db.session.get(User, session['uid'])
        if user:
            user.session_token = None
            user.api_token = None
            db.session.commit()
    session.clear()
    return redirect(url_for('login'))


# ═══════════════════════════════════════════════════════════
# PASSWORD RESET
# ═══════════════════════════════════════════════════════════
@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        user = User.query.filter_by(username=username).first()
        if not user:
            flash('No account found with that username.', 'error')
            return render_template('forgot_password.html')
        otp = f'{secrets.randbelow(900000) + 100000}'
        expires = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=10)
        PasswordReset.query.filter_by(user_id=user.id, used=False).delete()
        db.session.add(PasswordReset(user_id=user.id, otp=otp, expires_at=expires))
        db.session.commit()
        send_email_async(
            subject='Password Reset OTP - Saqib SEO Tools Agency',
            html_body=render_template('emails/reset_otp.html', otp=otp, user=user),
            recipient=user.username,
        )
        session['reset_user_id'] = user.id
        flash('An OTP has been sent to your email. It expires in 10 minutes.', 'success')
        return redirect(url_for('reset_password'))
    return render_template('forgot_password.html')


@app.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    user_id = session.get('reset_user_id')
    if not user_id:
        return redirect(url_for('forgot_password'))
    user = db.session.get(User, user_id)
    if not user:
        session.pop('reset_user_id', None)
        return redirect(url_for('forgot_password'))
    if request.method == 'POST':
        otp = request.form.get('otp', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')
        reset = PasswordReset.query.filter_by(
            user_id=user_id, otp=otp, used=False
        ).filter(PasswordReset.expires_at > datetime.now(timezone.utc).replace(tzinfo=None)).first()
        if not reset:
            flash('Invalid or expired OTP.', 'error')
            return render_template('reset_password.html')
        if len(password) < 4:
            flash('Password must be at least 4 characters.', 'error')
            return render_template('reset_password.html')
        if password != confirm:
            flash('Passwords do not match.', 'error')
            return render_template('reset_password.html')
        user.password = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        user.session_token = None
        user.api_token = None
        reset.used = True
        db.session.commit()
        session.pop('reset_user_id', None)
        flash('Password updated successfully. You can now log in.', 'success')
        return redirect(url_for('login'))
    return render_template('reset_password.html')


# ═══════════════════════════════════════════════════════════
# FORCE LOGOUT – OTP verify to kick old session
# ═══════════════════════════════════════════════════════════
@app.route('/force-logout-verify', methods=['GET', 'POST'])
def force_logout_verify():
    user_id = session.get('force_logout_user_id')
    if not user_id:
        return redirect(url_for('login'))
    user = db.session.get(User, user_id)
    if not user:
        session.pop('force_logout_user_id', None)
        return redirect(url_for('login'))
    if request.method == 'POST':
        otp = request.form.get('otp', '').strip()
        reset = PasswordReset.query.filter_by(
            user_id=user_id, otp=otp, used=False
        ).filter(PasswordReset.expires_at > datetime.now(timezone.utc).replace(tzinfo=None)).first()
        if not reset:
            flash('Invalid or expired OTP.', 'error')
            return render_template('force_logout_verify.html')
        reset.used = True
        user.session_token = None
        user.api_token = None
        db.session.commit()
        tok = secrets.token_hex(32)
        user.session_token = tok
        db.session.commit()
        session.pop('force_logout_user_id', None)
        session['uid']           = user.id
        session['username']      = user.username
        session['role']          = user.role
        session['session_token'] = tok
        flash('Old session logged out. You are now signed in.', 'success')
        if user.role == 'admin':
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('home'))
    return render_template('force_logout_verify.html')


# ═══════════════════════════════════════════════════════════
# ADMIN – Dashboard
# ═══════════════════════════════════════════════════════════
@app.route('/admin')
@admin_required
def admin_dashboard():
    total_tools = Tool.query.count()
    total_users = User.query.filter_by(role='user').count()
    total_logs  = UsageLog.query.count()
    recent_logs = (UsageLog.query
                   .order_by(UsageLog.opened_at.desc())
                   .limit(10).all())
    return render_template('admin_dashboard.html',
                           total_tools=total_tools,
                           total_users=total_users,
                           total_logs=total_logs,
                           recent_logs=recent_logs)


# ── Stats ─────────────────────────────────────────────────────────────
from sqlalchemy import func

@app.route('/admin/stats')
@admin_required
def admin_stats():
    rows = (db.session.query(
                UsageLog.user_id, User.username,
                UsageLog.tool_id, Tool.name,
                func.count(UsageLog.id).label('count')
            )
            .join(User, UsageLog.user_id == User.id)
            .join(Tool, UsageLog.tool_id == Tool.id)
            .group_by(UsageLog.user_id, UsageLog.tool_id, User.username, Tool.name)
            .order_by(func.count(UsageLog.id).desc())
            .all())
    return render_template('admin_stats.html', rows=rows)


# ── Tools CRUD ────────────────────────────────────────────────────────
@app.route('/admin/tools')
@admin_required
def admin_tools():
    tools = Tool.query.order_by(Tool.created_at.desc()).all()
    return render_template('admin_tools.html', tools=tools)


@app.route('/admin/tools/data/<int:tid>')
@admin_required
def admin_tool_data(tid):
    tool = Tool.query.get_or_404(tid)
    return jsonify({
        'id': tool.id, 'name': tool.name, 'category': tool.category,
        'url': tool.url, 'description': tool.description,
        'cookies': tool.cookies, 'kvm_url': tool.kvm_url or '',
        'is_active': tool.is_active,
        'image': tool.image or '',
    })


@app.route('/admin/tools/edit/<int:tid>', methods=['POST'])
@admin_required
def admin_edit_tool(tid):
    tool = Tool.query.get_or_404(tid)
    tool.name        = request.form.get('name', tool.name).strip()
    tool.category    = request.form.get('category', tool.category).strip()
    tool.url         = request.form.get('url', tool.url).strip()
    tool.description = request.form.get('description', tool.description).strip()
    tool.kvm_url     = request.form.get('kvm_url', '').strip() or None
    cookies_raw      = request.form.get('cookies', '').strip()
    if cookies_raw:
        try:
            parsed = json.loads(cookies_raw)
            if not isinstance(parsed, list):
                raise ValueError('Must be a JSON array')
            tool.cookies = json.dumps(parsed)
        except Exception as e:
            flash(f'Invalid cookies JSON: {e}', 'error')
            return redirect(url_for('admin_tools'))
    img = request.files.get('image')
    if img and img.filename:
        tool.image = base64.b64encode(img.read()).decode()
    db.session.commit()
    flash(f'Tool "{tool.name}" updated.', 'success')
    return redirect(url_for('admin_tools'))


@app.route('/admin/tools/delete/<int:tid>', methods=['POST'])
@admin_required
def admin_delete_tool(tid):
    tool = Tool.query.get_or_404(tid)
    UsageLog.query.filter_by(tool_id=tid).delete()
    UserTool.query.filter_by(tool_id=tid).delete()
    db.session.delete(tool)
    db.session.commit()
    flash(f'Tool "{tool.name}" deleted.', 'success')
    return redirect(url_for('admin_tools'))


@app.route('/admin/tools/toggle/<int:tid>', methods=['POST'])
@admin_required
def admin_toggle_tool(tid):
    tool = Tool.query.get_or_404(tid)
    tool.is_active = not tool.is_active
    db.session.commit()
    return jsonify({'ok': True, 'active': tool.is_active})


# ── Users CRUD ────────────────────────────────────────────────────────
@app.route('/admin/users')
@admin_required
def admin_users():
    users = User.query.filter_by(role='user').order_by(User.created_at.desc()).all()
    return render_template('admin_users.html', users=users)


@app.route('/admin/users/add', methods=['POST'])
@admin_required
def admin_add_user():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    email    = request.form.get('email', '').strip()
    subscription_days = request.form.get('subscription_days', '30').strip()

    if not username or not password:
        flash('Username and password required.', 'error')
        return redirect(url_for('admin_users'))

    if User.query.filter_by(username=username).first():
        flash('Username already exists.', 'error')
        return redirect(url_for('admin_users'))

    try:
        subscription_days = int(subscription_days)
        if subscription_days < 1 or subscription_days > 365:
            raise ValueError
    except ValueError:
        flash('Invalid subscription days.', 'error')
        return redirect(url_for('admin_users'))

    from datetime import datetime, timezone, timedelta
    
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user = User(
        username=username,
        password=hashed,
        role='user',
    )
    
    # Set subscription expiration
    if subscription_days > 0:
        user.subscription_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=subscription_days)
    # If subscription_days is 0, leave subscription_expires_at as NULL (unlimited)
    
    db.session.add(user)
    db.session.commit()
    flash(f'User "{username}" created.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/delete/<int:uid>', methods=['POST'])
@admin_required
def admin_delete_user(uid):
    user = User.query.get_or_404(uid)
    PasswordReset.query.filter_by(user_id=uid).delete()
    UsageLog.query.filter_by(user_id=uid).delete()
    UserTool.query.filter_by(user_id=uid).delete()
    db.session.delete(user)
    db.session.commit()
    flash(f'User "{user.username}" deleted.', 'success')
    referer = request.referrer or url_for('admin_users')
    return redirect(referer)


@app.route('/admin/users/logout/<int:uid>', methods=['POST'])
@admin_required
def admin_logout_user(uid):
    user = User.query.get_or_404(uid)
    user.session_token = None
    user.api_token = None
    db.session.commit()
    flash(f'Logged out session for "{user.username}".', 'success')
    referer = request.referrer or url_for('admin_users')
    return redirect(referer)


@app.route('/admin/users/toggle/<int:uid>', methods=['POST'])
@admin_required
def admin_toggle_user(uid):
    user = User.query.get_or_404(uid)
    user.is_active = not user.is_active
    db.session.commit()
    return jsonify({'ok': True, 'active': user.is_active})


@app.route('/admin/user/info/<int:uid>')
@admin_required
def admin_user_info(uid):
    user = User.query.get_or_404(uid)
    # Calculate remaining subscription days if expires_at is set
    subscription_days = None
    if user.subscription_expires_at:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if user.subscription_expires_at > now:
            delta = user.subscription_expires_at - now
            subscription_days = max(0, delta.days)
        else:
            subscription_days = 0  # Expired
    else:
        subscription_days = -1  # -1 means unlimited/no expiration
    
    return jsonify({
        'id': user.id,
        'username': user.username,
        'is_active': user.is_active
    })


@app.route('/admin/users/reset-password/<int:uid>', methods=['POST'])
@admin_required
def admin_reset_password(uid):
    user     = User.query.get_or_404(uid)
    new_pass = request.form.get('new_password', '').strip()
    if not new_pass:
        flash('Password cannot be empty.', 'error')
        referer = request.referrer or url_for('admin_users')
        return redirect(referer)
    user.password = bcrypt.hashpw(new_pass.encode(), bcrypt.gensalt()).decode()
    db.session.commit()
    flash(f'Password reset for "{user.username}".', 'success')
    referer = request.referrer or url_for('admin_users')
    return redirect(referer)


@app.route('/admin/users/edit/<int:uid>', methods=['POST'])
@admin_required
def admin_edit_user(uid):
    user = User.query.get_or_404(uid)
    subscription_days = request.form.get('subscription_days', '').strip()

    if subscription_days:
        try:
            subscription_days = int(subscription_days)
            if subscription_days < 0:
                raise ValueError
            flash(f'Subscription duration updated for "{user.username}".', 'info')
        except ValueError:
            flash('Invalid subscription days.', 'error')
            return redirect(url_for('admin_users'))

    db.session.commit()
    flash(f'User "{user.username}" updated.', 'success')
    return redirect(url_for('admin_users'))


# ── Assign tools to users ─────────────────────────────────────────────
@app.route('/admin/assign')
@admin_required
def admin_assign():
    users = User.query.filter_by(role='user').order_by(User.created_at.desc()).all()
    tools = Tool.query.filter_by(is_active=True).all()
    assignments = {}
    tool_expiry = {}
    assignment_data = {}
    for ut in UserTool.query.all():
        assignments.setdefault(ut.user_id, set()).add(ut.tool_id)
        assignment_data.setdefault(ut.user_id, {})[ut.tool_id] = ut
        if ut.expires_at:
            tool_expiry.setdefault(ut.user_id, {})[ut.tool_id] = ut.expires_at
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return render_template('admin_assign.html',
                           users=users, tools=tools,
                           assignments=assignments,
                           tool_expiry=tool_expiry,
                           assignment_data=assignment_data)


@app.route('/admin/assign/update', methods=['POST'])
@admin_required
def admin_assign_update():
    user_id  = int(request.form.get('user_id'))
    tool_ids = set(int(x) for x in request.form.getlist('tool_ids'))

    UserTool.query.filter_by(user_id=user_id).delete()
    for tid in tool_ids:
        dur = request.form.get(f'dur_{tid}', '')
        expires_at = None
        if dur == 'week':
            expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(weeks=1)
        elif dur == 'month':
            expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)
        elif dur == 'year':
            expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=365)
        elif dur == 'custom':
            custom_date = request.form.get(f'custom_date_{tid}', '').strip()
            if custom_date:
                try:
                    expires_at = datetime.fromisoformat(custom_date)
                    if expires_at.tzinfo:
                        expires_at = expires_at.replace(tzinfo=None)
                except ValueError:
                    pass
        db.session.add(UserTool(user_id=user_id, tool_id=tid, expires_at=expires_at))
    db.session.commit()

    user = db.session.get(User, user_id)
    if request.args.get('ajax') == '1':
        return jsonify({'ok': True, 'msg': f'Tools updated for "{user.username}".'})
    flash(f'Tools updated for "{user.username}".', 'success')
    return redirect(url_for('admin_assign'))


# ── Profile (change own username/password) ────────────────
@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    uid = session['uid']
    user = User.query.get_or_404(uid)
    if request.method == 'POST':
        new_username = request.form.get('username', '').strip()
        curr_password = request.form.get('current_password', '').encode()
        new_password = request.form.get('new_password', '').strip()

        if not bcrypt.checkpw(curr_password, user.password.encode()):
            flash('Current password is incorrect.', 'error')
            return redirect(url_for('profile'))

        if new_username and new_username != user.username:
            if User.query.filter_by(username=new_username).first():
                flash('Username already taken.', 'error')
                return redirect(url_for('profile'))
            user.username = new_username
            session['username'] = new_username

        if new_password:
            user.password = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()

        db.session.commit()
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('profile'))

    return render_template('admin_profile.html', user=user)


# ── Admin Management (create, list, delete admins) ────────────
@app.route('/admin/admins')
@admin_required
def admin_admins():
    admins = User.query.filter_by(role='admin').order_by(User.created_at.desc()).all()
    return render_template('admin_admins.html', admins=admins)


@app.route('/admin/admins/add', methods=['POST'])
@admin_required
def admin_add_admin():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    if not username or not password:
        flash('Username and password required.', 'error')
        return redirect(url_for('admin_admins'))
    if User.query.filter_by(username=username).first():
        flash('Username already exists.', 'error')
        return redirect(url_for('admin_admins'))
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user = User(username=username, password=hashed, role='admin')
    db.session.add(user)
    db.session.commit()
    flash(f'Admin "{username}" created.', 'success')
    return redirect(url_for('admin_admins'))


@app.route('/admin/admins/delete/<int:uid>', methods=['POST'])
@admin_required
def admin_delete_admin(uid):
    if uid == session['uid']:
        flash('You cannot delete yourself.', 'error')
        return redirect(url_for('admin_admins'))
    user = User.query.get_or_404(uid)
    if user.role != 'admin':
        flash('Not an admin account.', 'error')
        return redirect(url_for('admin_admins'))
    db.session.delete(user)
    db.session.commit()
    flash(f'Admin "{user.username}" deleted.', 'success')
    return redirect(url_for('admin_admins'))


@app.route('/admin/admins/reset-password/<int:uid>', methods=['POST'])
@admin_required
def admin_reset_admin_password(uid):
    if uid == session['uid']:
        return redirect(url_for('admin_profile'))
    user = User.query.get_or_404(uid)
    new_pass = request.form.get('new_password', '').strip()
    if not new_pass:
        flash('Password cannot be empty.', 'error')
        return redirect(url_for('admin_admins'))
    user.password = bcrypt.hashpw(new_pass.encode(), bcrypt.gensalt()).decode()
    db.session.commit()
    flash(f'Password reset for "{user.username}".', 'success')
    return redirect(url_for('admin_admins'))


# ═══════════════════════════════════════════════════════════
# RETAILER – Dashboard, Users, Assign
# ═══════════════════════════════════════════════════════════
@app.route('/retailer')
@retailer_required
def retailer_dashboard():
    uid = session['uid']
    total_sub_users = User.query.filter_by(created_by=uid, role='user').count()
    total_assigned = UserTool.query.join(User, UserTool.user_id == User.id).filter(User.created_by == uid).count()
    total_logs = UsageLog.query.join(User, UsageLog.user_id == User.id).filter(User.created_by == uid).count()
    recent_logs = (UsageLog.query
                   .join(User, UsageLog.user_id == User.id)
                   .filter(User.created_by == uid)
                   .order_by(UsageLog.opened_at.desc())
                   .limit(10).all())
    return render_template('retailer_dashboard.html',
                           total_sub_users=total_sub_users,
                           total_assigned=total_assigned,
                           total_logs=total_logs,
                           recent_logs=recent_logs)


@app.route('/retailer/users')
@retailer_required
def retailer_users():
    users = User.query.filter_by(created_by=session['uid'], role='user').order_by(User.created_at.desc()).all()
    return render_template('retailer_users.html', users=users)


@app.route('/retailer/users/add', methods=['POST'])
@retailer_required
def retailer_add_user():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    if not username or not password:
        flash('Username and password required.', 'error')
        return redirect(url_for('retailer_users'))
    if User.query.filter_by(username=username).first():
        flash('Username already exists.', 'error')
        return redirect(url_for('retailer_users'))
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user = User(username=username, password=hashed, role='user', created_by=session['uid'])
    db.session.add(user)
    db.session.commit()
    flash(f'User "{username}" created.', 'success')
    return redirect(url_for('retailer_users'))


@app.route('/retailer/users/delete/<int:uid>', methods=['POST'])
@retailer_required
def retailer_delete_user(uid):
    user = User.query.get_or_404(uid)
    if user.created_by != session['uid']:
        flash('Access denied.', 'error')
        return redirect(url_for('retailer_users'))
    PasswordReset.query.filter_by(user_id=uid).delete()
    UsageLog.query.filter_by(user_id=uid).delete()
    UserTool.query.filter_by(user_id=uid).delete()
    db.session.delete(user)
    db.session.commit()
    flash(f'User "{user.username}" deleted.', 'success')
    return redirect(url_for('retailer_users'))


@app.route('/retailer/users/toggle/<int:uid>', methods=['POST'])
@retailer_required
def retailer_toggle_user(uid):
    user = User.query.get_or_404(uid)
    if user.created_by != session['uid']:
        return jsonify({'ok': False, 'msg': 'Access denied.'})
    user.is_active = not user.is_active
    db.session.commit()
    return jsonify({'ok': True, 'active': user.is_active})


@app.route('/retailer/users/reset-password/<int:uid>', methods=['POST'])
@retailer_required
def retailer_reset_password(uid):
    user = User.query.get_or_404(uid)
    if user.created_by != session['uid']:
        flash('Access denied.', 'error')
        return redirect(url_for('retailer_users'))
    new_pass = request.form.get('new_password', '').strip()
    if not new_pass:
        flash('Password cannot be empty.', 'error')
        return redirect(url_for('retailer_users'))
    user.password = bcrypt.hashpw(new_pass.encode(), bcrypt.gensalt()).decode()
    db.session.commit()
    flash(f'Password reset for "{user.username}".', 'success')
    return redirect(url_for('retailer_users'))


@app.route('/retailer/assign')
@retailer_required
def retailer_assign():
    users = User.query.filter_by(created_by=session['uid'], role='user').all()
    tools = Tool.query.filter_by(is_active=True).all()
    assignments = {}
    tool_expiry = {}
    assignment_data = {}
    for ut in UserTool.query.join(User, UserTool.user_id == User.id).filter(User.created_by == session['uid']).all():
        assignments.setdefault(ut.user_id, set()).add(ut.tool_id)
        assignment_data.setdefault(ut.user_id, {})[ut.tool_id] = ut
        if ut.expires_at:
            tool_expiry.setdefault(ut.user_id, {})[ut.tool_id] = ut.expires_at
    return render_template('retailer_assign.html',
                           users=users, tools=tools,
                           assignments=assignments,
                           tool_expiry=tool_expiry,
                           assignment_data=assignment_data)


@app.route('/retailer/assign/update', methods=['POST'])
@retailer_required
def retailer_assign_update():
    user_id  = int(request.form.get('user_id'))
    user = User.query.get_or_404(user_id)
    if user.created_by != session['uid']:
        return jsonify({'ok': False, 'msg': 'Access denied.'})

    tool_ids = set(int(x) for x in request.form.getlist('tool_ids'))
    UserTool.query.filter_by(user_id=user_id).delete()
    for tid in tool_ids:
        dur = request.form.get(f'dur_{tid}', '')
        expires_at = None
        if dur == 'week':
            expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(weeks=1)
        elif dur == 'month':
            expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)
        elif dur == 'year':
            expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=365)
        elif dur == 'custom':
            custom_date = request.form.get(f'custom_date_{tid}', '').strip()
            if custom_date:
                try:
                    expires_at = datetime.fromisoformat(custom_date)
                    if expires_at.tzinfo:
                        expires_at = expires_at.replace(tzinfo=None)
                except ValueError:
                    pass
        db.session.add(UserTool(user_id=user_id, tool_id=tid, expires_at=expires_at))
    db.session.commit()

    if request.args.get('ajax') == '1':
        return jsonify({'ok': True, 'msg': f'Tools updated for "{user.username}".'})
    flash(f'Tools updated for "{user.username}".', 'success')
    return redirect(url_for('retailer_assign'))


@app.route('/retailer/stats')
@retailer_required
def retailer_stats():
    rows = (db.session.query(
                UsageLog.user_id, User.username,
                UsageLog.tool_id, Tool.name,
                func.count(UsageLog.id).label('count')
            )
            .join(User, UsageLog.user_id == User.id)
            .join(Tool, UsageLog.tool_id == Tool.id)
            .filter(User.created_by == session['uid'])
            .group_by(UsageLog.user_id, UsageLog.tool_id, User.username, Tool.name)
            .order_by(func.count(UsageLog.id).desc())
            .all())
    return render_template('retailer_stats.html', rows=rows)


# ═══════════════════════════════════════════════════════════
# ADMIN – Manage Retailers
# ═══════════════════════════════════════════════════════════
@app.route('/admin/retailers')
@admin_required
def admin_retailers():
    retailers = User.query.filter_by(role='retailer').order_by(User.created_at.desc()).all()
    return render_template('admin_retailers.html', retailers=retailers)


@app.route('/admin/retailers/add', methods=['POST'])
@admin_required
def admin_add_retailer():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    if not username or not password:
        flash('Username and password required.', 'error')
        return redirect(url_for('admin_retailers'))
    if User.query.filter_by(username=username).first():
        flash('Username already exists.', 'error')
        return redirect(url_for('admin_retailers'))
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user = User(username=username, password=hashed, role='retailer')
    db.session.add(user)
    db.session.commit()
    flash(f'Retailer "{username}" created.', 'success')
    return redirect(url_for('admin_retailers'))


@app.route('/admin/retailers/delete/<int:uid>', methods=['POST'])
@admin_required
def admin_delete_retailer(uid):
    user = User.query.get_or_404(uid)
    # Delete all sub-users created by this retailer
    for sub in User.query.filter_by(created_by=uid).all():
        UsageLog.query.filter_by(user_id=sub.id).delete()
        UserTool.query.filter_by(user_id=sub.id).delete()
        db.session.delete(sub)
    db.session.delete(user)
    db.session.commit()
    flash(f'Retailer "{user.username}" and their users deleted.', 'success')
    return redirect(url_for('admin_retailers'))


@app.route('/admin/retailers/toggle/<int:uid>', methods=['POST'])
@admin_required
def admin_toggle_retailer(uid):
    user = User.query.get_or_404(uid)
    user.is_active = not user.is_active
    db.session.commit()
    return jsonify({'ok': True, 'active': user.is_active})


@app.route('/admin/retailers/reset-password/<int:uid>', methods=['POST'])
@admin_required
def admin_reset_retailer_password(uid):
    user = User.query.get_or_404(uid)
    new_pass = request.form.get('new_password', '').strip()
    if not new_pass:
        flash('Password cannot be empty.', 'error')
        return redirect(url_for('admin_retailers'))
    user.password = bcrypt.hashpw(new_pass.encode(), bcrypt.gensalt()).decode()
    db.session.commit()
    flash(f'Password reset for "{user.username}".', 'success')
    return redirect(url_for('admin_retailers'))


# ═══════════════════════════════════════════════════════════
# EMAIL – Utilities & Admin routes
# ═══════════════════════════════════════════════════════════
SMTP_SERVER   = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT     = int(os.getenv('SMTP_PORT', 587))
SMTP_USERNAME = os.getenv('SMTP_USERNAME', '')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
SMTP_FROM     = os.getenv('SMTP_FROM', SMTP_USERNAME)

def send_email_sync(subject, html_body, recipient):
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = SMTP_FROM
        msg['To']      = recipient
        msg.attach(MIMEText(html_body, 'html'))
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(SMTP_USERNAME, SMTP_PASSWORD)
            s.send_message(msg)
        log = EmailLog(subject=subject, recipient=recipient, status='sent')
        db.session.add(log)
        db.session.commit()
        print(f'[email] Sent to {recipient}: {subject}')
    except Exception as e:
        print(f'[email] FAIL to {recipient}: {e}')
        try:
            log = EmailLog(subject=subject, recipient=recipient, status=f'fail: {e}')
            db.session.add(log)
            db.session.commit()
        except Exception:
            pass

send_email_async = send_email_sync


def _render_email(template_name, **kw):
    return render_template(f'emails/{template_name}', **kw)


@app.route('/admin/emails')
@admin_required
def admin_emails():
    users = User.query.filter_by(role='user').order_by(User.username).all()
    logs  = EmailLog.query.order_by(EmailLog.sent_at.desc()).limit(100).all()
    return render_template('admin_emails.html', users=users, logs=logs,
                           smtp_configured=bool(SMTP_USERNAME and SMTP_PASSWORD))


@app.route('/admin/emails/send', methods=['POST'])
@admin_required
def admin_send_email():
    subject  = request.form.get('subject', '').strip()
    message  = request.form.get('message', '').strip()
    to_type  = request.form.get('to_type', 'all')
    user_ids = request.form.getlist('user_ids')

    if not subject or not message:
        flash('Subject and message are required.', 'error')
        return redirect(url_for('admin_emails'))

    if to_type == 'all':
        recipients = [u for u in User.query.filter_by(role='user').all() if '@' in (u.username or '')]
    elif to_type == 'selected':
        recipients = User.query.filter(User.id.in_(user_ids), User.role == 'user').all()
    else:
        recipients = []

    if not recipients:
        flash('No recipients found (users must have an email address).', 'error')
        return redirect(url_for('admin_emails'))

    body_html = render_template('emails/admin_broadcast.html',
                                subject=subject, body=message)

    count = 0
    for u in recipients:
        send_email_async(subject, body_html, u.username)
        count += 1
    flash(f'Email queued to {count} recipients.', 'success')
    return redirect(url_for('admin_emails'))


# ── Tool add notification ──────────────────────────────────────────────
@app.route('/admin/tools/add', methods=['POST'])
@admin_required
def admin_add_tool():
    name        = request.form.get('name', '').strip()
    category    = request.form.get('category', 'General').strip()
    url         = request.form.get('url', '').strip()
    description = request.form.get('description', '').strip()
    cookies_raw = request.form.get('cookies', '').strip()
    kvm_url     = request.form.get('kvm_url', '').strip() or None
    notify      = request.form.get('notify_users') == 'on'

    if not name or not url or not cookies_raw:
        flash('Name, URL and Cookies are required.', 'error')
        return redirect(url_for('admin_tools'))

    try:
        parsed = json.loads(cookies_raw)
        if not isinstance(parsed, list):
            raise ValueError('Must be a JSON array')
        cookies_str = json.dumps(parsed)
    except Exception as e:
        flash(f'Invalid cookies JSON: {e}', 'error')
        return redirect(url_for('admin_tools'))

    tool = Tool(name=name, category=category, url=url,
                description=description, cookies=cookies_str,
                kvm_url=kvm_url)
    img = request.files.get('image')
    if img and img.filename:
        tool.image = base64.b64encode(img.read()).decode()
    db.session.add(tool)
    db.session.commit()
    flash(f'Tool "{name}" added successfully!', 'success')

    if notify:
        recipients = [u for u in User.query.filter_by(role='user').all() if '@' in (u.username or '')]
        if recipients:
            body_html = render_template('emails/new_tool.html', tool=tool)
            for u in recipients:
                send_email_async(
                    f'New Tool Available: {tool.name}',
                    body_html, u.username
                )
            flash(f'Notification sent to {len(recipients)} users.', 'success')

    return redirect(url_for('admin_tools'))


# ── Subscription expiry check ──────────────────────────────────────────
@app.route('/admin/emails/check-expiry')
@admin_required
def admin_check_expiry():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    soon = now + timedelta(days=3)
    users = [u for u in User.query.filter_by(role='user').all() if '@' in (u.username or '')]
    sent = 0
    for u in users:
        if u.subscription_expires_at:
            days_left = (u.subscription_expires_at - now).days
            if 0 <= days_left <= 3:
                body = render_template('emails/expiry_soon.html', user=u, days_left=days_left)
                send_email_async(
                    'Subscription Expiring Soon – Saqib SEO Tools Agency',
                    body, u.username
                )
                sent += 1
            elif days_left < 0:
                body = render_template('emails/expired.html', user=u)
                send_email_async(
                    'Subscription Expired – Saqib SEO Tools Agency',
                    body, u.username
                )
                sent += 1
    flash(f'Expiry notifications sent to {sent} users.', 'success')
    return redirect(url_for('admin_emails'))


@app.route('/admin/emails/manual-notify/<int:uid>', methods=['POST'])
@admin_required
def admin_manual_notify(uid):
    user = User.query.get_or_404(uid)
    if '@' not in (user.username or ''):
        flash('Username is not a valid email address.', 'error')
        return redirect(url_for('admin_users'))
    body = render_template('emails/expiry_soon.html', user=user,
                           days_left=0)
    send_email_async('Your Subscription is Expiring – Saqib SEO Tools Agency', body, user.username)
    flash(f'Expiry reminder sent to {user.username}.', 'success')
    return redirect(url_for('admin_users'))


# ═══════════════════════════════════════════════════════════
# ADMIN – Tool Bundles
# ═══════════════════════════════════════════════════════════
@app.route('/admin/bundles')
@admin_required
def admin_bundles():
    bundles = Bundle.query.order_by(Bundle.created_at.desc()).all()
    tools   = Tool.query.filter_by(is_active=True).all()
    users   = User.query.filter_by(role='user').order_by(User.username).all()
    return render_template('admin_bundles.html', bundles=bundles, tools=tools, users=users)


@app.route('/admin/bundles/create', methods=['POST'])
@admin_required
def admin_bundle_create():
    name = request.form.get('name', '').strip()
    tool_ids = [int(x) for x in request.form.getlist('tool_ids') if x.strip()]
    if not name:
        flash('Bundle name is required.', 'error')
        return redirect(url_for('admin_bundles'))
    if not tool_ids:
        flash('Select at least one tool.', 'error')
        return redirect(url_for('admin_bundles'))
    bundle = Bundle(name=name)
    db.session.add(bundle)
    db.session.flush()
    for tid in tool_ids:
        db.session.add(BundleTool(bundle_id=bundle.id, tool_id=tid))
    db.session.commit()
    flash(f'Bundle "{name}" created with {len(tool_ids)} tools.', 'success')
    return redirect(url_for('admin_bundles'))


@app.route('/admin/bundles/edit/<int:bid>', methods=['POST'])
@admin_required
def admin_bundle_edit(bid):
    bundle = db.session.get(Bundle, bid)
    if not bundle:
        flash('Bundle not found.', 'error')
        return redirect(url_for('admin_bundles'))
    name = request.form.get('name', '').strip()
    tool_ids = [int(x) for x in request.form.getlist('tool_ids') if x.strip()]
    if not name:
        flash('Bundle name is required.', 'error')
        return redirect(url_for('admin_bundles'))
    if not tool_ids:
        flash('Select at least one tool.', 'error')
        return redirect(url_for('admin_bundles'))
    bundle.name = name
    BundleTool.query.filter_by(bundle_id=bid).delete()
    for tid in tool_ids:
        db.session.add(BundleTool(bundle_id=bid, tool_id=tid))
    db.session.commit()
    flash(f'Bundle "{name}" updated.', 'success')
    return redirect(url_for('admin_bundles'))


@app.route('/admin/bundles/delete/<int:bid>', methods=['POST'])
@admin_required
def admin_bundle_delete(bid):
    bundle = db.session.get(Bundle, bid)
    if not bundle:
        flash('Bundle not found.', 'error')
        return redirect(url_for('admin_bundles'))
    name = bundle.name
    db.session.delete(bundle)
    db.session.commit()
    flash(f'Bundle "{name}" deleted.', 'success')
    return redirect(url_for('admin_bundles'))


@app.route('/admin/bundles/assign', methods=['POST'])
@admin_required
def admin_bundle_assign():
    bundle_id = int(request.form.get('bundle_id'))
    user_id   = int(request.form.get('user_id'))
    bundle    = db.session.get(Bundle, bundle_id)
    user      = db.session.get(User, user_id)
    if not bundle or not user:
        flash('Bundle or user not found.', 'error')
        return redirect(url_for('admin_bundles'))
    added = 0
    for bt in bundle.tools:
        existing = UserTool.query.filter_by(user_id=user_id, tool_id=bt.tool_id).first()
        if not existing:
            db.session.add(UserTool(user_id=user_id, tool_id=bt.tool_id))
            added += 1
    db.session.commit()
    flash(f'Bundle "{bundle.name}" → {user.username}: {added} tools assigned.', 'success')
    return redirect(url_for('admin_bundles'))


# ═══════════════════════════════════════════════════════════
# USER – Dashboard & Tool launch
# ═══════════════════════════════════════════════════════════
@app.route('/dashboard')
@login_required
def user_dashboard():
    uid  = session['uid']
    rows = (UserTool.query
            .join(Tool, UserTool.tool_id == Tool.id)
            .filter(UserTool.user_id == uid, Tool.is_active == True)
            .all())
    tools = [r.tool for r in rows]
    tool_assignments = {r.tool_id: r for r in rows}
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    tool_expiry = {}
    for r in rows:
        tool_expiry[r.tool_id] = r.expires_at
    return render_template('user_dashboard.html', tools=tools,
                           tool_expiry=tool_expiry,
                           tool_assignments=tool_assignments,
                           now=now)


def _is_mobile():
    ua = request.headers.get('User-Agent', '').lower()
    keywords = ['mobile', 'android', 'iphone', 'ipad', 'ipod', 'phone', 'tablet',
                'blackberry', 'opera mini', 'iemobile', 'webos', 'touch']
    return any(k in ua for k in keywords)


@app.route('/api/mobile/login', methods=['POST'])
def mobile_login():
    data = request.get_json(silent=True) or request.form
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').encode()
    user = User.query.filter_by(username=username).first()
    if user and user.is_active and bcrypt.checkpw(password, user.password.encode()):
        if user.api_token or user.session_token:
            user.api_token = None
            user.session_token = None
            db.session.commit()
        tok = secrets.token_hex(32)
        user.api_token = tok
        db.session.commit()
        return jsonify({'ok': True, 'api_token': tok,
                        'user': {'id': user.id, 'username': user.username}})
    return jsonify({'ok': False, 'msg': 'Invalid credentials.'})


@app.route('/api/mobile/logout')
def mobile_logout():
    token = request.headers.get('X-Session-Token') or request.args.get('token', '')
    user = User.query.filter_by(api_token=token).first()
    if user:
        user.api_token = None
        user.session_token = None
        db.session.commit()
        return jsonify({'ok': True})
    return jsonify({'ok': False})


@app.route('/api/mobile/tools')
def mobile_tools():
    token = request.headers.get('X-Session-Token') or request.args.get('token', '')
    user = User.query.filter_by(api_token=token).first()
    if not user:
        return jsonify({'ok': False, 'msg': 'Invalid or expired session.'})
    rows = (UserTool.query
            .join(Tool, UserTool.tool_id == Tool.id)
            .filter(UserTool.user_id == user.id, Tool.is_active == True)
            .all())
    tools = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for r in rows:
        is_expired = r.expires_at and r.expires_at < now
        tools.append({
            'id': r.tool.id,
            'name': r.tool.name or '',
            'category': r.tool.category or '',
            'description': r.tool.description or '',
            'url': r.tool.url or '',
            'kvm_url': r.tool.kvm_url or '',
            'is_expired': bool(is_expired),
            'expires_at': r.expires_at.isoformat() if r.expires_at else None,
            'image': ('/api/mobile/tool/image/' + str(r.tool.id)) if r.tool.image else '',
        })
    return jsonify({'ok': True, 'tools': tools, 'user_id': user.id, 'total': len(tools)})


@app.route('/api/mobile/tool/image/<int:tid>')
def mobile_tool_image(tid):
    tool = db.session.get(Tool, tid)
    if not tool or not tool.image:
        return '', 404
    img_bytes = base64.b64decode(tool.image)
    return Response(img_bytes, mimetype='image/png')


@app.route('/api/mobile/launch/<int:tid>')
def mobile_launch(tid):
    token = request.headers.get('X-Session-Token') or request.args.get('token', '')
    user = User.query.filter_by(api_token=token).first()
    if not user:
        return jsonify({'ok': False, 'msg': 'Invalid session.'})
    assignment = UserTool.query.filter_by(user_id=user.id, tool_id=tid).first()
    if not assignment:
        return jsonify({'ok': False, 'msg': 'Access denied.'})
    tool = Tool.query.get_or_404(tid)
    if not tool.is_active:
        return jsonify({'ok': False, 'msg': 'Tool is disabled.'})
    if assignment.expires_at and datetime.now(timezone.utc).replace(tzinfo=None) > assignment.expires_at:
        return jsonify({'ok': False, 'msg': 'Subscription expired.'})
    launch_token = secrets.token_urlsafe(16)
    db.session.add(LaunchToken(
        token=launch_token, url=tool.url, cookies=tool.cookies,
        username=user.username, tool_name=tool.name,
    ))
    db.session.add(UsageLog(user_id=user.id, tool_id=tid))
    db.session.commit()
    return jsonify({
        'ok': True, 'launch_token': launch_token,
        'url': tool.url, 'tool_name': tool.name,
        'kvm_url': tool.kvm_url or '',
        'msg': 'Launch token created.',
    })


@app.route('/use/<int:tid>')
@login_required
def use_tool(tid):
    uid        = session['uid']
    assignment = UserTool.query.filter_by(user_id=uid, tool_id=tid).first()
    if not assignment:
        return jsonify({'ok': False, 'msg': 'Access denied.'})

    tool = Tool.query.get_or_404(tid)
    if not tool.is_active:
        return jsonify({'ok': False, 'msg': 'Tool is disabled.'})

    if assignment.expires_at and datetime.now(timezone.utc).replace(tzinfo=None) > assignment.expires_at:
        return jsonify({'ok': False, 'msg': 'Your subscription has expired.'})

    user = db.session.get(User, uid)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    on_render = os.getenv('RENDER', '').lower() in ('1', 'true', 'yes')

    # If tool has a KVM URL, return it for direct opening instead of Playwright launch
    if tool.kvm_url:
        db.session.add(UsageLog(user_id=uid, tool_id=tid))
        db.session.commit()
        return jsonify({
            'ok': True, 'mode': 'kvm', 'kvm_url': tool.kvm_url,
            'msg': 'Opening KVM browser link...',
        })

    if not on_render:
        if user.subscription_expires_at and now > user.subscription_expires_at:
            return jsonify({'ok': False, 'msg': 'Subscription has expired. Please renew to continue using tools.'})

    # Mobile mode: provide cookies for manual import
    if _is_mobile():
        token = secrets.token_urlsafe(16)
        db.session.add(LaunchToken(
            token=token, url=tool.url, cookies=tool.cookies,
            username=session['username'], tool_name=tool.name,
        ))
        db.session.add(UsageLog(user_id=uid, tool_id=tid))
        db.session.commit()
        return jsonify({
            'ok': True, 'mode': 'mobile', 'token': token,
            'msg': 'Opening on your device...',
        })

    if on_render:
        token = secrets.token_urlsafe(16)
        db.session.add(LaunchToken(
            token=token, url=tool.url, cookies=tool.cookies,
            username=session['username'], tool_name=tool.name,
        ))
        db.session.add(UsageLog(user_id=uid, tool_id=tid))
        db.session.commit()
        return jsonify({
            'ok': True, 'mode': 'remote', 'token': token,
            'msg': 'Opening Chromium on your PC...',
        })

    # Local mode: auto-installs Playwright+Chromium if missing, then opens browser
    result = open_tool(tool.url, tool.cookies, session['username'])
    if result.get('ok'):
        db.session.add(UsageLog(user_id=uid, tool_id=tid))
        db.session.commit()
        return jsonify({'ok': True, 'mode': 'local', 'msg': 'Browser opened on your desktop.'})

    return jsonify({'ok': False, 'msg': result.get('error', 'Launch failed.')})


@app.route('/api/claim-launch/<token>')
def claim_launch(token):
    lt = LaunchToken.query.filter_by(token=token).first()
    if not lt:
        return jsonify({'ok': False, 'error': 'Invalid or expired token.'})
    db.session.delete(lt)
    db.session.commit()
    return jsonify({'ok': True, 'url': lt.url, 'cookies': lt.cookies,
                    'username': lt.username, 'tool_name': lt.tool_name})


@app.route('/launch/<token>')
def launch_page(token):
    lt = LaunchToken.query.filter_by(token=token).first()
    if not lt:
        return 'Invalid or expired launch token.', 404
    return render_template('launch.html',
                           tool_name=lt.tool_name,
                           token=token,
                           server_url=request.host_url.rstrip('/'))


@app.route('/launch/mobile/<token>')
def launch_mobile(token):
    lt = LaunchToken.query.filter_by(token=token).first()
    if not lt:
        return 'Invalid or expired launch token.', 404
    return render_template('launch_mobile.html',
                           tool_name=lt.tool_name,
                           tool_url=lt.url)


@app.route('/tool/view/<token>')
def tool_viewer(token):
    lt = LaunchToken.query.filter_by(token=token).first()
    if not lt:
        return 'Invalid or expired launch token.', 404
    return render_template('tool_viewer.html',
                           tool_name=lt.tool_name,
                           tool_url=lt.url,
                           tool_id=lt.tool_id,
                           username=lt.username,
                           token=token)


@app.route('/tool/proxy/<token>')
def tool_proxy(token):
    lt = LaunchToken.query.filter_by(token=token).first()
    if not lt:
        return 'Invalid or expired launch token.', 404

    target = lt.url
    cookies_dict = {}
    if lt.cookies:
        try:
            clist = json.loads(lt.cookies)
            for c in clist:
                cookies_dict[c['name']] = c['value']
        except Exception:
            pass

    ua = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
          'AppleWebKit/537.36 (KHTML, like Gecko) '
          'Chrome/126.0.0.0 Safari/537.36')

    hdrs = {
        'User-Agent': ua,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
    }

    try:
        resp = http_requests.get(target, headers=hdrs, cookies=cookies_dict,
                                 timeout=30, allow_redirects=True)
    except Exception as e:
        return f'Proxy error fetching {target}: {e}', 502

    ct = resp.headers.get('Content-Type', '').lower()
    if 'text/html' not in ct:
        return Response(resp.content,
                        content_type=ct or 'application/octet-stream')

    html = resp.text

    # Strip CSP meta tags that would block our inline scripts
    html = re.sub(r'<meta[^>]*http-equiv=["\']Content-Security-Policy["\'][^>]*>', '', html, flags=re.IGNORECASE)
    html = re.sub(r'<meta[^>]*http-equiv=["\']Content-Security-Policy-Report-Only["\'][^>]*>', '', html, flags=re.IGNORECASE)

    # Inject <base> so relative URLs resolve to the original server
    base_tag = f'<base href="{target.rstrip("/")}/">'
    html = html.replace('<head>', f'<head>{base_tag}')

    # Inject CSS + JS to hide UI elements (same pattern as Semrush: CSS + polling + MutationObserver)
    is_chatgpt = 'chatgpt.com' in target or 'chat.openai.com' in target
    is_grammarly = 'grammarly.com' in target
    is_primevideo = 'primevideo.com' in target or '/video' in target

    if is_chatgpt:
        sels = json.dumps([
            '#stage-slideover-sidebar > div > div > div > nav',
            '#stage-slideover-sidebar',
            '#page-header div[class*="shrink-0"] button',
        ])
        inject = (
            '<script>'
            '(function(){'
            'var sels=' + sels + ';'
            'var s=document.createElement("style");'
            's.textContent=sels.join(",")+"{display:none!important}";'
            'if(document.head)document.head.appendChild(s);'
            'function h(){for(var si=0;si<sels.length;si++){var e=document.querySelectorAll(sels[si]);for(var i=0;i<e.length;i++){e[i].style.setProperty("display","none","important")}}}'
            'h();setInterval(h,200);'
            'if(document.body){var mo=new MutationObserver(function(){h()});mo.observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:["style","class"]})}'
            '})();'
            '</script>'
        )
        html = html.replace('</head>', '<style>#stage-slideover-sidebar{display:none!important}#stage-slideover-sidebar>div>div>div>nav{display:none!important}</style></head>')
        html = html.replace('</body>', inject + '\n</body>')
        # Also try to intercept XMLHttpRequest/fetch to keep re-hiding sidebar
        inject_xhr = (
            '<script>'
            '(function(){'
            'var origOpen=XMLHttpRequest.prototype.open;'
            'XMLHttpRequest.prototype.open=function(){this.addEventListener("load",function(){setTimeout(function(){'
            'var sels=["#stage-slideover-sidebar > div > div > div > nav","#stage-slideover-sidebar"];'
            'for(var si=0;si<sels.length;si++){'
            'var e=document.querySelectorAll(sels[si]);'
            'for(var i=0;i<e.length;i++){e[i].style.setProperty("display","none","important")}'
            '}'
            '},100)});'
            'return origOpen.apply(this,arguments)};'
            '})();'
            '</script>'
        )
        html = html.replace('</body>', inject_xhr + '\n</body>')

    if is_grammarly:
        sels = json.dumps([
            'header', '[class*="header"]', '[class*="nav"]',
            'a[href*="logout"]', '[data-testid="header"]',
            '.app-header', '.nav-bar', '.nav-container'
        ])
        inject = (
            '<script>'
            '(function(){'
            'var sels=' + sels + ';'
            'var s=document.createElement("style");'
            's.textContent=sels.join(",")+"{display:none!important}";'
            'if(document.head)document.head.appendChild(s);'
            'function h(){for(var si=0;si<sels.length;si++){var e=document.querySelectorAll(sels[si]);for(var i=0;i<e.length;i++){e[i].style.setProperty("display","none","important")}}}'
            'h();setInterval(h,300);'
            'if(document.body){var mo=new MutationObserver(function(){h()});mo.observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:["style","class"]})}'
            '})();'
            '</script>'
        )
        html = html.replace('</body>', inject + '\n</body>')

    if is_primevideo:
        sels = json.dumps([
            '#navbar', '#dv-web-nav-header', '#av-breadcrumb',
            'footer', '[class*="nav-"]', '.nav-links', '.nav-banner',
            '[data-testid="navbar"]', '[data-testid="footer"]'
        ])
        inject = (
            '<script>'
            '(function(){'
            'var sels=' + sels + ';'
            'var s=document.createElement("style");'
            's.textContent=sels.join(",")+"{display:none!important}";'
            'if(document.head)document.head.appendChild(s);'
            'function h(){for(var si=0;si<sels.length;si++){var e=document.querySelectorAll(sels[si]);for(var i=0;i<e.length;i++){e[i].style.setProperty("display","none","important")}}}'
            'h();setInterval(h,300);'
            'if(document.body){var mo=new MutationObserver(function(){h()});mo.observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:["style","class"]})}'
            '})();'
            '</script>'
        )
        html = html.replace('</body>', inject + '\n</body>')

    return Response(html, content_type='text/html; charset=utf-8')


@app.route('/launch-download/<token>')
def launch_download(token):
    lt = LaunchToken.query.filter_by(token=token).first()
    if not lt:
        return 'Invalid or expired launch token.', 404

    server_url = request.host_url.rstrip('/')
    tool_name = lt.tool_name
    safe_name = tool_name.replace(' ', '_').replace('/', '_').replace('\\', '_')

    # Read and base64-encode the Python launcher
    launcher_path = os.path.join(os.path.dirname(__file__), 'static', 'launcher.py')
    b64 = ''
    if os.path.isfile(launcher_path):
        with open(launcher_path, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode()

    # Split base64 into ~4000-char chunks (under Windows cmd's 8191-char line limit)
    chunk_size = 4000
    b64_chunks = [b64[i:i+chunk_size] for i in range(0, len(b64), chunk_size)] if b64 else []

    bat_parts = []
    bat_parts.append(f'@echo off')
    bat_parts.append(f'title Saqib Tools - {tool_name}')
    bat_parts.append(f'cd /d "%~dp0"')
    bat_parts.append(f'')
    if b64_chunks:
        bat_parts.append(f'if not exist "launcher.py" (')
        bat_parts.append(f'    echo Extracting launcher script...')
        # Write base64 in small chunks to separate part files
        for i, chunk in enumerate(b64_chunks):
            bat_parts.append(f'    echo {chunk}>launcher.{i}')
        # Concatenate parts and decode (extra whitespace between chunks is ignored by certutil)
        parts = '+'.join([f'launcher.{i}' for i in range(len(b64_chunks))])
        bat_parts.append(f'    copy /b {parts} launcher.b64 >nul')
        part_files = ' '.join([f'launcher.{i}' for i in range(len(b64_chunks))])
        bat_parts.append(f'    del {part_files} >nul 2>&1')
        bat_parts.append(f'    certutil -decode launcher.b64 launcher.py >nul 2>&1')
        bat_parts.append(f'    del launcher.b64 >nul 2>&1')
        bat_parts.append(f'    if not exist "launcher.py" (')
        bat_parts.append(f'        echo Failed to create launcher.py')
        bat_parts.append(f'        pause')
        bat_parts.append(f'        exit /b 1')
        bat_parts.append(f'    )')
        bat_parts.append(f')')
    bat_parts.append(f'')
    bat_parts.append(f'REM 1) Portable bundled Python')
    bat_parts.append(f'if exist "python\\pythonw.exe" (')
    bat_parts.append(f'    start "" "python\\pythonw.exe" "launcher.py" --token {token} --server {server_url}')
    bat_parts.append(f'    exit /b 0')
    bat_parts.append(f')')
    bat_parts.append(f'')
    bat_parts.append(f'REM 2) System Python')
    bat_parts.append(f'if exist "launcher.py" (')
    bat_parts.append(f'    python launcher.py --token {token} --server {server_url}')
    bat_parts.append(f'    echo.')
    bat_parts.append(f'    pause')
    bat_parts.append(f'    exit /b 0')
    bat_parts.append(f')')
    bat_parts.append(f'')
    bat_parts.append(f'echo Could not extract or find launcher.py.')
    bat_parts.append(f'pause')
    bat_content = '\r\n'.join(bat_parts)

    response = Response(bat_content, mimetype='application/octet-stream')
    response.headers['Content-Disposition'] = f'attachment; filename="launch_{safe_name}.bat"'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Length'] = str(len(bat_content.encode('utf-8')))
    return response


# ── Context processors ────────────────────────────────────────────────
@app.context_processor
def inject_now():
    return {'now_utc': lambda: datetime.now(timezone.utc).replace(tzinfo=None)}


@app.context_processor
def inject_current_user():
    if 'uid' in session:
        user = db.session.get(User, session['uid'])
        return {'current_user': user}
    return {'current_user': None}


@app.context_processor
def inject_active_sessions():
    if 'uid' in session:
        role = session.get('role')
        if role == 'admin':
            logs = (UsageLog.query
                    .order_by(UsageLog.opened_at.desc())
                    .limit(8).all())
        elif role == 'retailer':
            logs = (UsageLog.query
                    .join(User, UsageLog.user_id == User.id)
                    .filter(User.created_by == session['uid'])
                    .order_by(UsageLog.opened_at.desc())
                    .limit(8).all())
        else:
            logs = (UsageLog.query
                    .filter_by(user_id=session['uid'])
                    .order_by(UsageLog.opened_at.desc())
                    .limit(8).all())
        return {'active_sessions': logs}
    return {'active_sessions': []}


# ── Error handlers ────────────────────────────────────────────────────
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404


@app.errorhandler(500)
def internal_server_error(e):
    import traceback
    tb = traceback.format_exc()
    print(f'[500] {e}\n{tb}')
    return render_template('500.html', error=str(e), traceback=tb), 500


# ── Bootstrap DB & seed admin ─────────────────────────────────────────
def seed():
    db.create_all()
    import sqlalchemy as sa
    insp = sa.inspect(db.engine)

    # Migrate: add expires_at column if missing
    cols_ut = [c['name'] for c in insp.get_columns('user_tools')]
    if 'expires_at' not in cols_ut:
        with db.engine.connect() as conn:
            conn.execute(sa.text('ALTER TABLE user_tools ADD COLUMN expires_at TIMESTAMP;'))
            conn.commit()
        print('[MIGRATION] Added expires_at to user_tools')

    # Migrate: add created_by column if missing
    cols_u = [c['name'] for c in insp.get_columns('users')]
    if 'created_by' not in cols_u:
        with db.engine.connect() as conn:
            conn.execute(sa.text('ALTER TABLE users ADD COLUMN created_by INTEGER REFERENCES users(id);'))
            conn.commit()
        print('[MIGRATION] Added created_by to users')

    if 'subscription_expires_at' not in cols_u:
        with db.engine.connect() as conn:
            conn.execute(sa.text('ALTER TABLE users ADD COLUMN subscription_expires_at TIMESTAMP;'))
            conn.commit()
        print('[MIGRATION] Added subscription_expires_at to users')

    if 'session_token' not in cols_u:
        with db.engine.connect() as conn:
            conn.execute(sa.text('ALTER TABLE users ADD COLUMN session_token VARCHAR(64);'))
            conn.commit()
        print('[MIGRATION] Added session_token to users')

    if 'api_token' not in cols_u:
        with db.engine.connect() as conn:
            conn.execute(sa.text('ALTER TABLE users ADD COLUMN api_token VARCHAR(64);'))
            conn.commit()
        print('[MIGRATION] Added api_token to users')

    # Migrate: add kvm_url column if missing
    cols_t = [c['name'] for c in insp.get_columns('tools')]
    if 'kvm_url' not in cols_t:
        with db.engine.connect() as conn:
            conn.execute(sa.text('ALTER TABLE tools ADD COLUMN kvm_url VARCHAR(500);'))
            conn.commit()
        print('[MIGRATION] Added kvm_url to tools')

    # Create email_logs table if not exists
    if 'email_logs' not in [t for t in insp.get_table_names()]:
        EmailLog.__table__.create(db.engine)
        print('[MIGRATION] Created email_logs table')

    if not User.query.filter_by(username='admin').first():
        hashed = bcrypt.hashpw(b'admin123', bcrypt.gensalt()).decode()
        db.session.add(User(username='admin', password=hashed, role='admin'))
        db.session.commit()
        print('[OK] Admin created  ->  admin / admin123')
    else:
        print('[OK] Admin user already exists')


with app.app_context():
    seed()

if __name__ == '__main__':
    print(f'[OK] Running at  http://localhost:{PORT}')
    app.run(debug=False, host='0.0.0.0', port=PORT)
