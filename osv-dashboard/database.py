# -*- coding: utf-8 -*-
"""Модели базы данных (PostgreSQL через Flask-SQLAlchemy)."""
import json
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

db = SQLAlchemy()


class Setting(db.Model):
    """Глобальные настройки приложения (ключ-значение)."""
    __tablename__ = 'settings'

    key   = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.Text, default='')

    @staticmethod
    def get(key, default=''):
        row = db.session.get(Setting, key)
        return row.value if row else default

    @staticmethod
    def set(key, value):
        row = db.session.get(Setting, key)
        if row is None:
            row = Setting(key=key, value=value)
            db.session.add(row)
        else:
            row.value = value


class EmailRule(db.Model):
    """Правило маршрутизации входящих писем → предприятие."""
    __tablename__ = 'email_rules'

    id              = db.Column(db.Integer, primary_key=True)
    company_id      = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False)
    sender_pattern  = db.Column(db.String(255), default='')   # например: *@mup-kgh.ru
    subject_pattern = db.Column(db.String(255), default='')   # например: ОСВ КГХ
    note            = db.Column(db.String(255), default='')   # комментарий
    created_at      = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    company = db.relationship('Company', backref='email_rules', lazy=True)


def utcnow():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    uploads = db.relationship('Upload', backref='uploader', lazy=True,
                              foreign_keys='Upload.uploaded_by')
    companies = db.relationship('Company', backref='creator', lazy=True,
                                foreign_keys='Company.created_by')


class Company(db.Model):
    __tablename__ = 'companies'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    uploads = db.relationship('Upload', backref='company', lazy=True,
                              order_by='Upload.uploaded_at.desc()')


class Upload(db.Model):
    __tablename__ = 'uploads'

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False)
    uploaded_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    uploaded_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    period_label = db.Column(db.String(100), default='')        # «янв – июн 2025»
    accounts_loaded = db.Column(db.Text, default='[]')          # JSON-список счетов
    closure_status = db.Column(db.Boolean, default=None)        # True = закрыт
    profit_sign = db.Column(db.String(20), default='neutral')   # profit/loss/neutral
    profit_value = db.Column(db.Float, default=0.0)             # числовое значение
    dashboard_file = db.Column(db.String(255), default='')      # имя HTML-файла
    files_count = db.Column(db.Integer, default=0)              # кол-во файлов в пакете
    source = db.Column(db.String(20), default='manual')         # 'manual' | 'auto_imap'

    @property
    def accounts_list(self):
        try:
            return json.loads(self.accounts_loaded)
        except Exception:
            return []


class DailyRow(db.Model):
    """Суточные данные из ZIP-архивов: один счёт, один день."""
    __tablename__ = 'daily_rows'
    __table_args__ = (
        db.UniqueConstraint('company_id', 'date', 'acc', name='uq_daily_company_date_acc'),
    )

    id         = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False)
    date       = db.Column(db.Date, nullable=False)          # дата дня
    acc        = db.Column(db.String(10), nullable=False)    # номер счёта ('51', '62', …)

    # СН Дт / Кт (сальдо начало дня)
    snd = db.Column(db.Float, default=0.0)
    snk = db.Column(db.Float, default=0.0)
    # Обороты Дт / Кт за день
    obd = db.Column(db.Float, default=0.0)
    obk = db.Column(db.Float, default=0.0)
    # СК Дт / Кт (сальдо конец дня)
    skd = db.Column(db.Float, default=0.0)
    skk = db.Column(db.Float, default=0.0)

    uploaded_at = db.Column(db.DateTime(timezone=True), default=utcnow)
