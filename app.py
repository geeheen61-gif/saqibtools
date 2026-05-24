import os
import json
import bcrypt
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import (Flask, render_template, request,
                   redirect, url_for, session, jsonify, flash)

from database import db, User, Tool, UserTool, UsageLog
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
        db.session.add(UserTool(user_id=user_id, tool_id=tid, expires_at=expires_at))
    db.session.commit()

    user = User.query.get(user_id)
    if request.args.get('ajax') == '1':
        return jsonify({'ok': True, 'msg': f'Tools updated for "{user.username}".'})
    flash(f'Tools updated for "{user.username}".', 'success')
    return redirect(url_for('admin_assign'))


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

    return jsonify({
        'ok': True,
        'url': tool.url,
        'cookies': tool.cookies,
        'username': session['username']
    })


@app.route('/local-launch', methods=['POST', 'OPTIONS'])
def local_launch():
    if request.method == 'OPTIONS':
        response = app.response_class(status=204)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        return response

    data = request.json or {}
    url = data.get('url')
    cookies = data.get('cookies')
    username = data.get('username')

    if not url or not cookies or not username:
        response = jsonify({'ok': False, 'error': 'Missing parameters'})
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response, 400

    result = open_tool(url, cookies, username)
    response = jsonify(result)
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response



# ── Context processors ────────────────────────────────────────────────
@app.context_processor
def inject_now():
    return {'now_utc': lambda: datetime.now(timezone.utc).replace(tzinfo=None)}


@app.context_processor
def inject_active_sessions():
    if 'uid' in session:
        if session.get('role') == 'admin':
            logs = (UsageLog.query
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
    # Migrate: add expires_at column if missing
    import sqlalchemy as sa
    insp = sa.inspect(db.engine)
    cols = [c['name'] for c in insp.get_columns('user_tools')]
    if 'expires_at' not in cols:
        with db.engine.connect() as conn:
            conn.execute(sa.text('ALTER TABLE user_tools ADD COLUMN expires_at TIMESTAMP;'))
            conn.commit()
        print('[MIGRATION] Added expires_at to user_tools')

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
