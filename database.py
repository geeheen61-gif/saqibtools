from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone
_utcnow = lambda: datetime.now(timezone.utc).replace(tzinfo=None)

db = SQLAlchemy()

class User(db.Model):
    __tablename__ = 'users'
    id          = db.Column(db.Integer, primary_key=True)
    username    = db.Column(db.String(80), unique=True, nullable=False)
    password    = db.Column(db.String(200), nullable=False)
    role        = db.Column(db.String(20), default='user')
    is_active   = db.Column(db.Boolean, default=True)
    created_by  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at  = db.Column(db.DateTime, default=_utcnow)
    # Subscription and credit limit system
    subscription_expires_at = db.Column(db.DateTime, nullable=True)  # NULL = no expiration
    monthly_credit_limit = db.Column(db.Integer, default=100)  # Default 100 credits per month
    credits_used_current_month = db.Column(db.Integer, default=0)
    last_credit_reset = db.Column(db.DateTime, default=_utcnow)
    # Single-session tracking
    session_token = db.Column(db.String(64), nullable=True)

    assigned_tools = db.relationship('UserTool', back_populates='user',
                                     cascade='all, delete-orphan')
    sub_users = db.relationship('User', backref=db.backref('creator', remote_side='User.id'),
                                lazy='dynamic', foreign_keys=[created_by])

class Tool(db.Model):
    __tablename__ = 'tools'
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(100), nullable=False)
    category    = db.Column(db.String(50), default='General')
    url         = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text, default='')
    cookies     = db.Column(db.Text, nullable=False)
    is_active   = db.Column(db.Boolean, default=True)
    created_at  = db.Column(db.DateTime, default=_utcnow)

    assigned_users = db.relationship('UserTool', back_populates='tool',
                                     cascade='all, delete-orphan')

class UserTool(db.Model):
    __tablename__ = 'user_tools'
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    tool_id     = db.Column(db.Integer, db.ForeignKey('tools.id'), nullable=False)
    assigned_at = db.Column(db.DateTime, default=_utcnow)
    expires_at  = db.Column(db.DateTime, nullable=True)
    credit_limit = db.Column(db.Integer, nullable=True)

    user = db.relationship('User',  back_populates='assigned_tools')
    tool = db.relationship('Tool',  back_populates='assigned_users')

class UsageLog(db.Model):
    __tablename__ = 'usage_logs'
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id'))
    tool_id     = db.Column(db.Integer, db.ForeignKey('tools.id'))
    opened_at   = db.Column(db.DateTime, default=_utcnow)

    user = db.relationship('User')
    tool = db.relationship('Tool')


class LaunchToken(db.Model):
    __tablename__ = 'launch_tokens'
    token       = db.Column(db.String(64), primary_key=True)
    url         = db.Column(db.Text, nullable=False)
    cookies     = db.Column(db.Text, nullable=False)
    username    = db.Column(db.String(80), nullable=False)
    tool_name   = db.Column(db.String(100), nullable=False)
    created_at  = db.Column(db.DateTime, default=_utcnow)
