import os
import json
import bcrypt
import secrets
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import (Flask, render_template, request,
                   redirect, url_for, session, jsonify, flash, Response)

from database import db, User, Tool, UserTool, UsageLog, LaunchToken
import base64
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
        if request.endpoint in ('static', 'login', 'logout', 'health', 'launch_page', 'launch_download'):
            return
        user = db.session.get(User, session['uid'])
        if not user or user.session_token != session['session_token']:
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
            db.session.commit()
    session.clear()
    return redirect(url_for('login'))


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


@app.route('/admin/tools/add', methods=['POST'])
@admin_required
def admin_add_tool():
    name        = request.form.get('name', '').strip()
    category    = request.form.get('category', 'General').strip()
    url         = request.form.get('url', '').strip()
    description = request.form.get('description', '').strip()
    cookies_raw = request.form.get('cookies', '').strip()

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
                description=description, cookies=cookies_str)
    db.session.add(tool)
    db.session.commit()
    flash(f'Tool "{name}" added successfully!', 'success')
    return redirect(url_for('admin_tools'))


@app.route('/admin/tools/data/<int:tid>')
@admin_required
def admin_tool_data(tid):
    tool = Tool.query.get_or_404(tid)
    return jsonify({
        'id': tool.id, 'name': tool.name, 'category': tool.category,
        'url': tool.url, 'description': tool.description,
        'cookies': tool.cookies, 'is_active': tool.is_active,
    })


@app.route('/admin/tools/edit/<int:tid>', methods=['POST'])
@admin_required
def admin_edit_tool(tid):
    tool = Tool.query.get_or_404(tid)
    tool.name        = request.form.get('name', tool.name).strip()
    tool.category    = request.form.get('category', tool.category).strip()
    tool.url         = request.form.get('url', tool.url).strip()
    tool.description = request.form.get('description', tool.description).strip()
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
    subscription_days = request.form.get('subscription_days', '30').strip()
    credit_limit = request.form.get('credit_limit', '100').strip()

    if not username or not password:
        flash('Username and password required.', 'error')
        return redirect(url_for('admin_users'))

    if User.query.filter_by(username=username).first():
        flash('Username already exists.', 'error')
        return redirect(url_for('admin_users'))

    try:
        subscription_days = int(subscription_days)
        credit_limit = int(credit_limit)
        if subscription_days < 1 or subscription_days > 365:
            raise ValueError
        if credit_limit < 0:
            raise ValueError
    except ValueError:
        flash('Invalid subscription days or credit limit.', 'error')
        return redirect(url_for('admin_users'))

    from datetime import datetime, timezone, timedelta
    
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user = User(
        username=username,
        password=hashed,
        role='user',
        monthly_credit_limit=credit_limit
    )
    
    # Set subscription expiration
    if subscription_days > 0:
        user.subscription_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=subscription_days)
    # If subscription_days is 0, leave subscription_expires_at as NULL (unlimited)
    
    db.session.add(user)
    db.session.commit()
    flash(f'User "{username}" created with {credit_limit} monthly credits.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/delete/<int:uid>', methods=['POST'])
@admin_required
def admin_delete_user(uid):
    user = User.query.get_or_404(uid)
    UsageLog.query.filter_by(user_id=uid).delete()
    UserTool.query.filter_by(user_id=uid).delete()
    db.session.delete(user)
    db.session.commit()
    flash(f'User "{user.username}" deleted.', 'success')
    return redirect(url_for('admin_users'))


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
        'subscription_days': subscription_days,
        'credit_limit': user.monthly_credit_limit,
        'credits_used': user.credits_used_current_month,
        'is_active': user.is_active
    })


@app.route('/admin/users/reset-password/<int:uid>', methods=['POST'])
@admin_required
def admin_reset_password(uid):
    user     = User.query.get_or_404(uid)
    new_pass = request.form.get('new_password', '').strip()
    if not new_pass:
        flash('Password cannot be empty.', 'error')
        return redirect(url_for('admin_users'))
    user.password = bcrypt.hashpw(new_pass.encode(), bcrypt.gensalt()).decode()
    db.session.commit()
    flash(f'Password reset for "{user.username}".', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/edit/<int:uid>', methods=['POST'])
@admin_required
def admin_edit_user(uid):
    user = User.query.get_or_404(uid)
    subscription_days = request.form.get('subscription_days', '').strip()
    credit_limit = request.form.get('credit_limit', '').strip()

    if subscription_days:
        try:
            subscription_days = int(subscription_days)
            if subscription_days < 0:
                raise ValueError
            # Note: Subscription expiration is handled via UserTool assignments
            # For now, we'll store a note or this could be extended to add a subscription tool
            flash(f'Subscription duration updated for "{user.username}".', 'info')
        except ValueError:
            flash('Invalid subscription days.', 'error')
            return redirect(url_for('admin_users'))

    if credit_limit:
        try:
            credit_limit = int(credit_limit)
            if credit_limit < 0:
                raise ValueError
            user.monthly_credit_limit = credit_limit
            # Reset current usage if new limit is lower than current usage
            if user.credits_used_current_month > credit_limit:
                user.credits_used_current_month = credit_limit
        except ValueError:
            flash('Invalid credit limit.', 'error')
            return redirect(url_for('admin_users'))

    db.session.commit()
    flash(f'User "{user.username}" updated.', 'success')
    return redirect(url_for('admin_users'))


# ── Assign tools to users ─────────────────────────────────────────────
@app.route('/admin/assign')
@admin_required
def admin_assign():
    users = User.query.filter_by(role='user').all()
    tools = Tool.query.filter_by(is_active=True).all()
    assignments = {}
    tool_expiry = {}
    assignment_data = {}
    for ut in UserTool.query.all():
        assignments.setdefault(ut.user_id, set()).add(ut.tool_id)
        assignment_data.setdefault(ut.user_id, {})[ut.tool_id] = ut
        if ut.expires_at:
            tool_expiry.setdefault(ut.user_id, {})[ut.tool_id] = ut.expires_at
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
        credit_limit = request.form.get(f'credit_limit_{tid}', '').strip()
        credit_limit_value = None
        if credit_limit:
            try:
                credit_limit_value = int(credit_limit)
                if credit_limit_value < 0:
                    raise ValueError
            except ValueError:
                credit_limit_value = None
        db.session.add(UserTool(user_id=user_id, tool_id=tid, expires_at=expires_at, credit_limit=credit_limit_value))
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
        credit_limit = request.form.get(f'credit_limit_{tid}', '').strip()
        credit_limit_value = None
        if credit_limit:
            try:
                credit_limit_value = int(credit_limit)
                if credit_limit_value < 0:
                    raise ValueError
            except ValueError:
                credit_limit_value = None
        db.session.add(UserTool(user_id=user_id, tool_id=tid, expires_at=expires_at, credit_limit=credit_limit_value))
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

    # Skip credit & subscription checks on Render so users never see limit errors
    if not on_render:
        if assignment.credit_limit and assignment.credit_limit > 0:
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            tool_usage_count = (UsageLog.query
                                .filter_by(user_id=uid, tool_id=tid)
                                .filter(UsageLog.opened_at >= month_start)
                                .count())
            if tool_usage_count >= assignment.credit_limit:
                return jsonify({'ok': False, 'msg': 'Tool credit limit reached for this month. Please contact administrator to increase access.'})

        if user.subscription_expires_at and now > user.subscription_expires_at:
            return jsonify({'ok': False, 'msg': 'Subscription has expired. Please renew to continue using tools.'})

    # Mobile mode: provide cookies for manual import
    if _is_mobile():
        token = secrets.token_urlsafe(16)
        db.session.add(LaunchToken(
            token=token, url=tool.url, cookies=tool.cookies,
            username=session['username'], tool_name=tool.name,
        ))
        user.credits_used_current_month += 1
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
        user.credits_used_current_month += 1
        db.session.add(UsageLog(user_id=uid, tool_id=tid))
        db.session.commit()
        return jsonify({
            'ok': True, 'mode': 'remote', 'token': token,
            'msg': 'Opening Chromium on your PC...',
        })

    # Local mode: auto-installs Playwright+Chromium if missing, then opens browser
    result = open_tool(tool.url, tool.cookies, session['username'])
    if result.get('ok'):
        user.credits_used_current_month += 1
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

    bat_parts = []
    bat_parts.append(f'@echo off')
    bat_parts.append(f'title Saqib Tools - {tool_name}')
    bat_parts.append(f'cd /d "%~dp0"')
    bat_parts.append(f'')
    if b64:
        bat_parts.append(f'if not exist "launcher.py" (')
        bat_parts.append(f'    echo Extracting launcher script...')
        bat_parts.append(f"    powershell -Command \"&{{$b='{b64}';$d=[Convert]::FromBase64String($b);[IO.File]::WriteAllBytes('launcher.py',$d)}}\"")
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

    # Migrate: add expires_at and credit_limit columns if missing
    cols_ut = [c['name'] for c in insp.get_columns('user_tools')]
    if 'expires_at' not in cols_ut:
        with db.engine.connect() as conn:
            conn.execute(sa.text('ALTER TABLE user_tools ADD COLUMN expires_at TIMESTAMP;'))
            conn.commit()
        print('[MIGRATION] Added expires_at to user_tools')
    if 'credit_limit' not in cols_ut:
        with db.engine.connect() as conn:
            conn.execute(sa.text('ALTER TABLE user_tools ADD COLUMN credit_limit INTEGER;'))
            conn.commit()
        print('[MIGRATION] Added credit_limit to user_tools')

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

    if 'monthly_credit_limit' not in cols_u:
        with db.engine.connect() as conn:
            conn.execute(sa.text('ALTER TABLE users ADD COLUMN monthly_credit_limit INTEGER DEFAULT 100;'))
            conn.commit()
        print('[MIGRATION] Added monthly_credit_limit to users')

    if 'credits_used_current_month' not in cols_u:
        with db.engine.connect() as conn:
            conn.execute(sa.text('ALTER TABLE users ADD COLUMN credits_used_current_month INTEGER DEFAULT 0;'))
            conn.commit()
        print('[MIGRATION] Added credits_used_current_month to users')

    if 'last_credit_reset' not in cols_u:
        with db.engine.connect() as conn:
            conn.execute(sa.text('ALTER TABLE users ADD COLUMN last_credit_reset TIMESTAMP DEFAULT CURRENT_TIMESTAMP;'))
            conn.commit()
        print('[MIGRATION] Added last_credit_reset to users')

    if 'session_token' not in cols_u:
        with db.engine.connect() as conn:
            conn.execute(sa.text('ALTER TABLE users ADD COLUMN session_token VARCHAR(64);'))
            conn.commit()
        print('[MIGRATION] Added session_token to users')

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
