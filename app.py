import os
import json
import bcrypt
import secrets
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import (Flask, render_template, request,
                   redirect, url_for, session, jsonify, flash)

from database import db, User, Tool, UserTool, UsageLog, LaunchToken
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
        'pool_recycle': 180,
        'connect_args': {
            'connect_timeout': 10,
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


# ── Decorators ────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def inner(*a, **kw):
        if 'uid' not in session:
            return redirect(url_for('login'))
        return f(*a, **kw)
    return inner


def admin_required(f):
    @wraps(f)
    def inner(*a, **kw):
        if 'uid' not in session:
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            flash('Admin access only.', 'error')
            return redirect(url_for('user_dashboard'))
        return f(*a, **kw)
    return inner


def retailer_required(f):
    @wraps(f)
    def inner(*a, **kw):
        if 'uid' not in session:
            return redirect(url_for('login'))
        if session.get('role') not in ('retailer', 'admin'):
            flash('Retailer access only.', 'error')
            return redirect(url_for('user_dashboard'))
        return f(*a, **kw)
    return inner


# ── Health check ──────────────────────────────────────────────────────
@app.route('/health')
def health():
    return {'status': 'ok'}


# ── Auth routes ───────────────────────────────────────────────────────
@app.route('/')
def home():
    if 'uid' not in session:
        return redirect(url_for('login'))
    if session['role'] == 'admin':
        return redirect(url_for('admin_dashboard'))
    if session['role'] == 'retailer':
        return redirect(url_for('retailer_dashboard'))
    return redirect(url_for('user_dashboard'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').encode()
        user = User.query.filter_by(username=username).first()
        if user and user.is_active and bcrypt.checkpw(password, user.password.encode()):
            session['uid']      = user.id
            session['username'] = user.username
            session['role']     = user.role
            return redirect(url_for('home'))
        flash('Invalid username or password.', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
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

    if not username or not password:
        flash('Username and password required.', 'error')
        return redirect(url_for('admin_users'))

    if User.query.filter_by(username=username).first():
        flash('Username already exists.', 'error')
        return redirect(url_for('admin_users'))

    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user   = User(username=username, password=hashed, role='user')
    db.session.add(user)
    db.session.commit()
    flash(f'User "{username}" created.', 'success')
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


# ── Assign tools to users ─────────────────────────────────────────────
@app.route('/admin/assign')
@admin_required
def admin_assign():
    users = User.query.filter_by(role='user').all()
    tools = Tool.query.filter_by(is_active=True).all()
    assignments = {}
    tool_expiry = {}
    for ut in UserTool.query.all():
        assignments.setdefault(ut.user_id, set()).add(ut.tool_id)
        if ut.expires_at:
            tool_expiry.setdefault(ut.user_id, {})[ut.tool_id] = ut.expires_at
    return render_template('admin_assign.html',
                           users=users, tools=tools,
                           assignments=assignments,
                           tool_expiry=tool_expiry)


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

    user = User.query.get(user_id)
    if request.args.get('ajax') == '1':
        return jsonify({'ok': True, 'msg': f'Tools updated for "{user.username}".'})
    flash(f'Tools updated for "{user.username}".', 'success')
    return redirect(url_for('admin_assign'))


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
    for ut in UserTool.query.join(User, UserTool.user_id == User.id).filter(User.created_by == session['uid']).all():
        assignments.setdefault(ut.user_id, set()).add(ut.tool_id)
        if ut.expires_at:
            tool_expiry.setdefault(ut.user_id, {})[ut.tool_id] = ut.expires_at
    return render_template('retailer_assign.html',
                           users=users, tools=tools,
                           assignments=assignments,
                           tool_expiry=tool_expiry)


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
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    tool_expiry = {}
    for r in rows:
        tool_expiry[r.tool_id] = r.expires_at
    return render_template('user_dashboard.html', tools=tools, tool_expiry=tool_expiry, now=now)


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

    db.session.add(UsageLog(user_id=uid, tool_id=tid))
    db.session.commit()

    on_render = os.getenv('RENDER', '').lower() in ('1', 'true', 'yes')
    if on_render:
        token = secrets.token_urlsafe(16)
        db.session.add(LaunchToken(
            token=token, url=tool.url, cookies=tool.cookies,
            username=session['username'], tool_name=tool.name,
        ))
        db.session.commit()
        return jsonify({
            'ok': True, 'mode': 'remote', 'token': token,
            'msg': 'Opening Chromium on your PC...',
        })
    # Local mode: auto-installs Playwright+Chromium if missing, then opens browser
    result = open_tool(tool.url, tool.cookies, session['username'])
    if result.get('ok'):
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


@app.route('/launch-download/<token>')
def launch_download(token):
    lt = LaunchToken.query.filter_by(token=token).first()
    if not lt:
        return 'Invalid or expired launch token.', 404

    server_url = request.host_url.rstrip('/')
    tool_name = lt.tool_name

    bat_content = f"""@echo off
title AccountHub Launcher - {tool_name}
echo =====================================================================
echo    AccountHub Launcher
echo =====================================================================
echo.
echo  Tool: {tool_name}
echo  This will install Playwright + Chromium (if needed) and open your
echo  session in a separate Chromium browser window.
echo.
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Python not found. Please install Python from:
    echo     https://www.python.org/downloads/
    echo     Make sure to check "Add Python to PATH".
    pause
    exit /b 1
)
echo [*] Downloading launcher script...
curl -sL "{server_url}/static/launcher.py" -o "%TEMP%\\account_hub_launcher.py" 2>nul
if not exist "%TEMP%\\account_hub_launcher.py" (
    echo [!] Failed to download launcher script.
    echo     Check your internet connection.
    pause
    exit /b 1
)
echo [*] Launching...
python "%TEMP%\\account_hub_launcher.py" --token {token} --server {server_url}
if %errorlevel% equ 0 (
    echo.
    echo [OK] Session launched successfully!
) else (
    echo.
    echo [x] Launch failed. Try running setup_and_run.bat first.
)
pause
"""
    return bat_content, 200, {
        'Content-Type': 'application/x-bat',
        'Content-Disposition': f'attachment; filename="launch_{tool_name.replace(" ", "_")}.bat"',
    }


# ── Context processors ────────────────────────────────────────────────
@app.context_processor
def inject_now():
    return {'now_utc': lambda: datetime.now(timezone.utc).replace(tzinfo=None)}


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
