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
    created_at  = db.Column(db.DateTime, default=_utcnow)

    assigned_tools = db.relationship('UserTool', back_populates='user',
                                     cascade='all, delete-orphan')

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
