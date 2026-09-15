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


class DadataDiagnosticEvent(db.Model):
    """Ограниченный технический журнал без содержимого запросов и ответов."""
    __tablename__ = 'dadata_diagnostic_events'

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                           nullable=False, index=True)
    attempt_id = db.Column(db.String(32), nullable=False, index=True)
    stage = db.Column(db.String(32), nullable=False)
    duration_ms = db.Column(db.Integer, nullable=False, default=0)
    retry_no = db.Column(db.Integer, nullable=False, default=0)
    http_status = db.Column(db.Integer)
    category = db.Column(db.String(32), default='')
    outcome = db.Column(db.String(24), default='')


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
    role = db.Column(db.String(20), nullable=False, default='user')  # 'admin' | 'user'
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    # Additive password lifecycle fields.  Existing accounts remain usable
    # until an administrator explicitly requires a change.
    password_changed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    password_change_required = db.Column(db.Boolean, nullable=False, default=False)
    password_version = db.Column(db.Integer, nullable=False, default=1)

    @property
    def is_admin(self):
        return self.role == 'admin'

    uploads = db.relationship('Upload', backref='uploader', lazy=True,
                              foreign_keys='Upload.uploaded_by')
    companies = db.relationship('Company', backref='creator', lazy=True,
                                foreign_keys='Company.created_by')
    module_grants = db.relationship(
        'UserModuleAccess', backref='user', lazy=True,
        cascade='all, delete-orphan')
    company_memberships = db.relationship(
        'CompanyMembership', backref='user', lazy=True,
        cascade='all, delete-orphan')
    metric_layouts = db.relationship(
        'MetricLayout', backref='user', lazy=True,
        cascade='all, delete-orphan')

    def has_module_access(self, module_key):
        """Администратор видит весь живой реестр; пользователь — назначения."""
        if self.is_admin:
            return True
        return any(grant.module_key == module_key for grant in self.module_grants)

    @property
    def assigned_module_keys(self):
        return {grant.module_key for grant in self.module_grants}


class UserModuleAccess(db.Model):
    """Сохранённое назначение модуля обычному пользователю."""
    __tablename__ = 'user_module_access'
    __table_args__ = (
        db.UniqueConstraint('user_id', 'module_key',
                            name='uq_user_module_access_user_key'),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False, index=True)
    module_key = db.Column(db.String(80), nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)


class Company(db.Model):
    __tablename__ = 'companies'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    # Реквизиты предприятия (справочник РСО)
    inn              = db.Column(db.String(20),  default='')
    full_name        = db.Column(db.Text,        default='')
    address          = db.Column(db.Text,        default='')
    director_name    = db.Column(db.String(255), default='')
    director_phone   = db.Column(db.String(100), default='')
    accountant_name  = db.Column(db.String(255), default='')
    accountant_phone = db.Column(db.String(100), default='')
    note             = db.Column(db.Text,        default='')

    # Каноническая сущность предприятия для справочников и всех доменных модулей.
    internal_code       = db.Column(db.String(80), default='')
    identity_key        = db.Column(db.String(80), nullable=True, unique=True)
    status              = db.Column(db.String(20), nullable=False, default='active')
    is_rso              = db.Column(db.Boolean, nullable=False, default=True)
    curator_user_id     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    ogrn                = db.Column(db.String(20), default='')
    kpp                 = db.Column(db.String(20), default='')
    opf_short           = db.Column(db.String(120), default='')
    legal_status        = db.Column(db.String(40), default='')
    registration_date   = db.Column(db.Date, nullable=True)
    ogrn_date           = db.Column(db.Date, nullable=True)
    actuality_date      = db.Column(db.Date, nullable=True)
    liquidation_date    = db.Column(db.Date, nullable=True)
    legal_address       = db.Column(db.Text, default='')
    address_quality     = db.Column(db.String(40), default='')
    address_ids_json    = db.Column(db.Text, default='{}')
    director_position   = db.Column(db.String(255), default='')
    primary_okved       = db.Column(db.String(32), default='')
    classifications_json = db.Column(db.Text, default='{}')
    labels_json         = db.Column(db.Text, default='[]')
    dadata_source       = db.Column(db.String(40), default='')
    dadata_received_at  = db.Column(db.DateTime(timezone=True), nullable=True)
    dadata_actuality_date = db.Column(db.Date, nullable=True)
    dadata_sync_status  = db.Column(db.String(24), default='never')
    dadata_last_error   = db.Column(db.Text, default='')
    profile_version     = db.Column(db.Integer, nullable=False, default=1)
    updated_at          = db.Column(db.DateTime(timezone=True), default=utcnow,
                                    onupdate=utcnow)

    uploads = db.relationship('Upload', backref='company', lazy=True,
                              order_by='Upload.uploaded_at.desc()')
    memberships = db.relationship('CompanyMembership', backref='company',
                                  lazy=True, cascade='all, delete-orphan')
    contacts = db.relationship('CompanyContact', backref='company', lazy=True,
                               cascade='all, delete-orphan')
    dadata_profile = db.relationship('CompanyDadataProfile', backref='company',
                                     uselist=False, lazy=True,
                                     cascade='all, delete-orphan')
    dadata_snapshots = db.relationship('CompanyDadataSnapshot', backref='company',
                                       lazy=True, order_by='CompanyDadataSnapshot.received_at.desc()',
                                       cascade='all, delete-orphan')
    okved_entries = db.relationship('CompanyOkved', backref='company', lazy=True,
                                    cascade='all, delete-orphan')
    licenses = db.relationship('CompanyLicense', backref='company', lazy=True,
                               cascade='all, delete-orphan')
    finance_rows = db.relationship('CompanyFinance', backref='company', lazy=True,
                                   cascade='all, delete-orphan')
    historical_imports = db.relationship(
        'HistoricalImport', backref='company', lazy=True,
        cascade='all, delete-orphan')
    historical_mappings = db.relationship(
        'HistoricalMapping', backref='company', lazy=True,
        cascade='all, delete-orphan')
    curator = db.relationship('User', foreign_keys=[curator_user_id], lazy=True)
    one_c_channel = db.relationship(
        'OneCChannel', backref='company', uselist=False, lazy=True,
        cascade='all, delete-orphan', passive_deletes=True)
    revenue_analytic_mappings = db.relationship(
        'RevenueAnalyticMapping', backref='company', lazy=True,
        cascade='all, delete-orphan', passive_deletes=True)
    cost_analytic_mappings = db.relationship(
        'CostAnalyticMapping', backref='company', lazy=True,
        cascade='all, delete-orphan', passive_deletes=True)
    metric_layouts = db.relationship(
        'MetricLayout', backref='company', lazy=True,
        cascade='all, delete-orphan', passive_deletes=True)
    debtor_analytic_mappings = db.relationship(
        'DebtorAnalyticMapping', backref='company', lazy=True,
        cascade='all, delete-orphan', passive_deletes=True)

    @property
    def is_archived(self):
        return self.status == 'archived'

    @property
    def classifications(self):
        try:
            return json.loads(self.classifications_json or '{}')
        except (TypeError, ValueError):
            return {}

    @property
    def labels(self):
        try:
            return json.loads(self.labels_json or '[]')
        except (TypeError, ValueError):
            return []
class RevenueAnalyticMapping(db.Model):
    """Точное сопоставление аналитики продаж с категорией предприятия."""
    __tablename__ = 'revenue_analytic_mappings'
    __table_args__ = (
        db.UniqueConstraint(
            'company_id', 'account', 'analytic_key',
            name='uq_revenue_mapping_company_account_key'),
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(
        db.Integer, db.ForeignKey('companies.id', ondelete='CASCADE'),
        nullable=False, index=True)
    account = db.Column(db.String(5), nullable=False)
    analytic_key = db.Column(db.String(64), nullable=False)
    analytic_label = db.Column(db.String(1000), nullable=False)
    category = db.Column(db.String(32), nullable=False)
    updated_by = db.Column(
        db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow,
                           nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow,
                           onupdate=utcnow, nullable=False)

class DebtorAnalyticMapping(db.Model):
    """Периодическое точное сопоставление аналитики дебиторской задолженности."""
    __tablename__ = 'debtor_analytic_mappings'
    __table_args__ = (
        db.UniqueConstraint(
            'company_id', 'account', 'analytic_key',
            'effective_from', 'effective_to',
            name='uq_debtor_mapping_company_account_key_period'),
        db.CheckConstraint(
            "(account = '62.01' OR account LIKE '76.%')",
            name='ck_debtor_mapping_account'),
        db.CheckConstraint(
            'effective_from <= effective_to',
            name='ck_debtor_mapping_period'),
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(
        db.Integer, db.ForeignKey('companies.id', ondelete='CASCADE'),
        nullable=False, index=True)
    account = db.Column(db.String(32), nullable=False)
    analytic_key = db.Column(db.String(64), nullable=False)
    analytic_label = db.Column(db.String(1000), nullable=False)
    category = db.Column(db.String(32), nullable=False)
    effective_from = db.Column(db.Date, nullable=False, index=True)
    effective_to = db.Column(db.Date, nullable=False, index=True)
    updated_by = db.Column(
        db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow,
                           nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow,
                           onupdate=utcnow, nullable=False)
class CostAnalyticMapping(db.Model):
    """Периодное сопоставление формирующей затраты с экономической статьёй."""
    __tablename__ = 'cost_analytic_mappings'
    __table_args__ = (
        db.UniqueConstraint(
            'company_id', 'account', 'analytic_key', 'valid_from', 'valid_to',
            name='uq_cost_mapping_company_account_key_period'),
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(
        db.Integer, db.ForeignKey('companies.id', ondelete='CASCADE'),
        nullable=False, index=True)
    account = db.Column(db.String(2), nullable=False)
    analytic_key = db.Column(db.String(64), nullable=False)
    analytic_label = db.Column(db.String(1000), nullable=False)
    category = db.Column(db.String(32), nullable=False)
    target_account = db.Column(db.String(5), nullable=False)
    valid_from = db.Column(db.Date, nullable=False)
    valid_to = db.Column(db.Date, nullable=False)
    updated_by = db.Column(
        db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow,
                           nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow,
                           onupdate=utcnow, nullable=False)


class CompanyMembership(db.Model):
    """Явное назначение пользователя предприятию, независимо от модулей."""
    __tablename__ = 'company_memberships'
    __table_args__ = (
        db.UniqueConstraint('user_id', 'company_id',
                            name='uq_company_membership_user_company'),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id', ondelete='CASCADE'),
                           nullable=False, index=True)
    participation_type = db.Column(db.String(40), nullable=False, default='employee')
    is_primary = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CompanyContact(db.Model):
    """Повторяющиеся внутренние и внешние контакты предприятия."""
    __tablename__ = 'company_contacts'

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id', ondelete='CASCADE'),
                           nullable=False, index=True)
    contact_type = db.Column(db.String(40), nullable=False, default='other')
    label = db.Column(db.String(120), default='')
    name = db.Column(db.String(255), default='')
    value = db.Column(db.String(500), nullable=False, default='')
    is_internal = db.Column(db.Boolean, nullable=False, default=True)
    source = db.Column(db.String(40), default='manual')
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CompanyDadataProfile(db.Model):
    """Последний принятый расширенный профиль DaData."""
    __tablename__ = 'company_dadata_profiles'

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id', ondelete='CASCADE'),
                           nullable=False, unique=True)
    normalized_json = db.Column(db.Text, default='{}')
    extended_json = db.Column(db.Text, default='{}')
    source = db.Column(db.String(40), default='DaData')
    received_at = db.Column(db.DateTime(timezone=True), nullable=True)
    actuality_date = db.Column(db.Date, nullable=True)
    accepted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    accepted_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    version = db.Column(db.Integer, nullable=False, default=1)

    accepter = db.relationship('User', lazy=True)


class CompanyDadataSnapshot(db.Model):
    """Неизменяемый снимок успешного или неуспешного обращения к DaData."""
    __tablename__ = 'company_dadata_snapshots'

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id', ondelete='CASCADE'),
                           nullable=False, index=True)
    query_inn = db.Column(db.String(20), default='')
    query_ogrn = db.Column(db.String(20), default='')
    received_at = db.Column(db.DateTime(timezone=True), default=utcnow, index=True)
    actuality_date = db.Column(db.Date, nullable=True)
    response_hash = db.Column(db.String(64), default='')
    status = db.Column(db.String(24), nullable=False, default='success')
    normalized_json = db.Column(db.Text, default='{}')
    response_json = db.Column(db.Text, default='{}')
    error_text = db.Column(db.Text, default='')
    requested_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    requester = db.relationship('User', lazy=True)


class CompanyOkved(db.Model):
    """Нормализованный основной и дополнительные ОКВЭД."""
    __tablename__ = 'company_okveds'
    __table_args__ = (
        db.UniqueConstraint('company_id', 'code', name='uq_company_okved_code'),
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id', ondelete='CASCADE'),
                           nullable=False, index=True)
    code = db.Column(db.String(32), nullable=False)
    name = db.Column(db.String(500), default='')
    is_main = db.Column(db.Boolean, nullable=False, default=False)
    source = db.Column(db.String(40), default='DaData')


class CompanyLicense(db.Model):
    """Лицензии из принятого профиля DaData."""
    __tablename__ = 'company_licenses'

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id', ondelete='CASCADE'),
                           nullable=False, index=True)
    number = db.Column(db.String(255), default='')
    activity = db.Column(db.Text, default='')
    status = db.Column(db.String(80), default='')
    details_json = db.Column(db.Text, default='{}')
    source = db.Column(db.String(40), default='DaData')


class CompanyFinance(db.Model):
    """Финансовые показатели по годам для отчётов и фильтров."""
    __tablename__ = 'company_finances'
    __table_args__ = (
        db.UniqueConstraint('company_id', 'year', name='uq_company_finance_year'),
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id', ondelete='CASCADE'),
                           nullable=False, index=True)
    year = db.Column(db.Integer, nullable=False)
    revenue = db.Column(db.Float, nullable=True)
    expense = db.Column(db.Float, nullable=True)
    profit = db.Column(db.Float, nullable=True)
    details_json = db.Column(db.Text, default='{}')
    source = db.Column(db.String(40), default='DaData')


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
    # Неизменяемый снимок серверных KPI на момент загрузки. Общий рейтинг
    # никогда не читает изменяемый кэш текущего дашборда.
    metrics_json = db.Column(db.Text, default='{}')
    # JSON-массив только принятых месячных исходных файлов.  Старые записи
    # остаются NULL: связь с историческими каталогами задним числом не
    # угадывается и не создаётся миграцией.
    # New ORM-created uploads explicitly describe an empty accepted-source set
    # as [] so it cannot be confused with historical NULL rows.  The additive
    # migration intentionally adds this nullable column without a SQL default,
    # leaving existing rows untouched.
    source_manifest_json = db.Column(db.Text, nullable=True, default='[]')

    @property
    def accounts_list(self):
        try:
            return json.loads(self.accounts_loaded)
        except Exception:
            return []

    @property
    def metrics(self):
        try:
            value = json.loads(self.metrics_json or '{}')
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError):
            return {}

    @property
    def source_manifest(self):
        """Return the accepted-file manifest, preserving NULL for old rows."""
        if self.source_manifest_json in (None, ''):
            return None
        try:
            value = json.loads(self.source_manifest_json)
            return value if isinstance(value, list) else None
        except (TypeError, ValueError):
            return None

class RatingProfile(db.Model):
    """Именованный пользовательский профиль общего рейтинга."""
    __tablename__ = 'rating_profiles'
    __table_args__ = (
        db.UniqueConstraint('owner_id', 'name', name='uq_rating_profile_owner_name'),
    )

    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'),
                         nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    config_json = db.Column(db.Text, nullable=False, default='{}')
    is_default = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow,
                           onupdate=utcnow)

    @property
    def config(self):
        try:
            return json.loads(self.config_json)
        except (TypeError, ValueError):
            return {}


class MetricLayout(db.Model):
    """Пользовательское размещение частей модального показателя.

    This is deliberately separate from calculated data and from company-wide
    settings: a layout belongs to one authenticated user, one company, one
    screen mode, and one metric. ``revision`` is the optimistic-lock counter
    used by the layout API.
    """
    __tablename__ = 'metric_layouts'
    __table_args__ = (
        db.UniqueConstraint(
            'user_id', 'company_id', 'mode', 'metric_id',
            name='uq_metric_layout_user_company_mode_metric'),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False, index=True)
    company_id = db.Column(
        db.Integer, db.ForeignKey('companies.id', ondelete='CASCADE'),
        nullable=False, index=True)
    mode = db.Column(db.String(16), nullable=False, index=True)
    metric_id = db.Column(db.String(128), nullable=False, index=True)
    layout_json = db.Column(db.Text, nullable=False, default='{}')
    revision = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow,
                           nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow,
                           onupdate=utcnow, nullable=False)


# Descriptive alias for callers that refer to the record as a preference.
# Both names identify the same mapped table; no second schema object exists.
MetricLayoutPreference = MetricLayout


# ---------------------------------------------------------------------------
# Годовая историческая аналитика (независимый от ОСВ контур)
# ---------------------------------------------------------------------------
class HistoricalImport(db.Model):
    """Неизменяемая попытка загрузки книги и результат её проверки."""
    __tablename__ = 'historical_imports'

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(
        db.Integer, db.ForeignKey('companies.id', ondelete='CASCADE'),
        nullable=False, index=True)
    uploaded_by = db.Column(
        db.Integer, db.ForeignKey('users.id', ondelete='RESTRICT'),
        nullable=False)
    original_filename = db.Column(db.String(500), nullable=False)
    stored_path = db.Column(db.String(1000), nullable=False)
    file_hash = db.Column(db.String(64), nullable=False, index=True)
    status = db.Column(db.String(24), nullable=False, default='preview')
    is_partial = db.Column(db.Boolean, nullable=False, default=False)
    partial_confirmed = db.Column(db.Boolean, nullable=False, default=False)
    preview_json = db.Column(db.Text, nullable=False, default='{}')
    validation_json = db.Column(db.Text, nullable=False, default='{}')
    error_text = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    confirmed_at = db.Column(db.DateTime(timezone=True))

    author = db.relationship('User', lazy=True)
    versions = db.relationship(
        'HistoricalYearVersion', backref='historical_import', lazy=True,
        cascade='all, delete-orphan')

    @property
    def preview(self):
        try:
            return json.loads(self.preview_json or '{}')
        except (TypeError, ValueError):
            return {}


class HistoricalYearVersion(db.Model):
    """Версия годового среза; только одна версия года может быть активной."""
    __tablename__ = 'historical_year_versions'
    __table_args__ = (
        db.UniqueConstraint(
            'company_id', 'year', 'version_no',
            name='uq_historical_year_version'),
        db.Index(
            'uq_historical_active_year', 'company_id', 'year', unique=True,
            postgresql_where=db.text('is_active'),
            sqlite_where=db.text('is_active')),
    )

    id = db.Column(db.Integer, primary_key=True)
    import_id = db.Column(
        db.Integer, db.ForeignKey('historical_imports.id', ondelete='CASCADE'),
        nullable=False, index=True)
    company_id = db.Column(
        db.Integer, db.ForeignKey('companies.id', ondelete='CASCADE'),
        nullable=False, index=True)
    year = db.Column(db.Integer, nullable=False, index=True)
    version_no = db.Column(db.Integer, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=False, index=True)
    activated_at = db.Column(db.DateTime(timezone=True))
    activated_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    activator = db.relationship('User', lazy=True)
    metrics = db.relationship(
        'HistoricalMetricValue', backref='year_version', lazy=True,
        cascade='all, delete-orphan')


class HistoricalMetricValue(db.Model):
    """Нормализованное значение одного показателя за календарный год."""
    __tablename__ = 'historical_metric_values'
    __table_args__ = (
        db.UniqueConstraint(
            'year_version_id', 'section_key', 'metric_key', 'series_key',
            name='uq_historical_metric_version_key'),
    )

    id = db.Column(db.Integer, primary_key=True)
    year_version_id = db.Column(
        db.Integer,
        db.ForeignKey('historical_year_versions.id', ondelete='CASCADE'),
        nullable=False, index=True)
    section_key = db.Column(db.String(100), nullable=False, index=True)
    section_label = db.Column(db.String(255), nullable=False)
    metric_key = db.Column(db.String(240), nullable=False, index=True)
    metric_label = db.Column(db.Text, nullable=False)
    series_key = db.Column(db.String(120), nullable=False, default='value')
    period_label = db.Column(db.String(255), nullable=False, default='')
    value_numeric = db.Column(db.Float)
    value_text = db.Column(db.Text)
    unit = db.Column(db.String(40), nullable=False, default='ratio')
    is_missing = db.Column(db.Boolean, nullable=False, default=False)
    is_upper_bound = db.Column(db.Boolean, nullable=False, default=False)

    raw_value = db.relationship(
        'HistoricalRawValue', backref='metric', uselist=False, lazy=True,
        cascade='all, delete-orphan')


class HistoricalRawValue(db.Model):
    """Исходная строка ячейки для аудита нормализации."""
    __tablename__ = 'historical_raw_values'

    id = db.Column(db.Integer, primary_key=True)
    metric_id = db.Column(
        db.Integer,
        db.ForeignKey('historical_metric_values.id', ondelete='CASCADE'),
        nullable=False, unique=True)
    raw_text = db.Column(db.Text, nullable=False, default='')
    sheet_name = db.Column(db.String(255), nullable=False, default='')
    cell_ref = db.Column(db.String(32), nullable=False, default='')


class HistoricalMapping(db.Model):
    """Подтверждённый пользователем вариант названия раздела или показателя."""
    __tablename__ = 'historical_mappings'
    __table_args__ = (
        db.UniqueConstraint(
            'company_id', 'kind', 'source_normalized',
            name='uq_historical_mapping_company_source'),
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(
        db.Integer, db.ForeignKey('companies.id', ondelete='CASCADE'),
        nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False)  # section | metric
    source_label = db.Column(db.Text, nullable=False)
    source_normalized = db.Column(db.String(500), nullable=False)
    canonical_key = db.Column(db.String(240), nullable=False)
    canonical_label = db.Column(db.Text, nullable=False)
    confirmed_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    confirmed_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    confirmer = db.relationship('User', lazy=True)


class AuditLog(db.Model):
    """Журнал событий безопасности (входы, изменения пользователей, загрузки файлов и т.д.)."""
    __tablename__ = 'audit_log'

    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'),
                           nullable=True)
    action     = db.Column(db.String(64), nullable=False, index=True)
    details    = db.Column(db.Text, default=None)       # JSON-строка с деталями
    ip         = db.Column(db.String(45), default=None) # IPv4 или IPv6
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, index=True)


class OneCChannel(db.Model):
    """Изолированный входящий канал 1С для одного предприятия."""
    __tablename__ = 'one_c_channels'

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(
        db.Integer, db.ForeignKey('companies.id', ondelete='CASCADE'),
        nullable=False, unique=True, index=True)
    login = db.Column(db.String(120), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.Text, nullable=False)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    allow_http = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    last_received_at = db.Column(db.DateTime(timezone=True))
    last_receipt_id = db.Column(db.String(64), nullable=True, unique=True)
    last_raw_json = db.Column(db.Text)
    last_row_count = db.Column(db.Integer, nullable=False, default=0)

    attempts = db.relationship(
        'OneCAttempt', backref='channel', lazy=True,
        cascade='all, delete-orphan')
    idempotency_keys = db.relationship(
        'OneCIdempotency', backref='channel', lazy=True,
        cascade='all, delete-orphan')


class OneCAttempt(db.Model):
    """Ограниченный безопасный журнал попыток без тела и реквизитов."""
    __tablename__ = 'one_c_attempts'

    id = db.Column(db.Integer, primary_key=True)
    channel_id = db.Column(
        db.Integer, db.ForeignKey('one_c_channels.id', ondelete='CASCADE'),
        nullable=True, index=True)
    received_at = db.Column(db.DateTime(timezone=True), default=utcnow, index=True)
    result = db.Column(db.String(24), nullable=False)
    receipt_id = db.Column(db.String(64))
    byte_size = db.Column(db.Integer, nullable=False, default=0)
    row_count = db.Column(db.Integer)
    transport = db.Column(db.String(8), nullable=False)
    error = db.Column(db.String(300), default='')


class OneCIdempotency(db.Model):
    """Ключ идемпотентности входящего запроса с семидневным сроком."""
    __tablename__ = 'one_c_idempotency'
    __table_args__ = (
        db.UniqueConstraint('channel_id', 'key',
                            name='uq_one_c_idempotency_channel_key'),
    )

    id = db.Column(db.Integer, primary_key=True)
    channel_id = db.Column(
        db.Integer, db.ForeignKey('one_c_channels.id', ondelete='CASCADE'),
        nullable=False, index=True)
    key = db.Column(db.String(200), nullable=False)
    body_hash = db.Column(db.String(64), nullable=False)
    receipt_id = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, index=True)


class AiProvider(db.Model):
    """Настройки LLM-провайдера (OpenAI-совместимый API)."""
    __tablename__ = 'ai_providers'

    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(100), nullable=False)
    base_url   = db.Column(db.String(500), default='')
    api_key    = db.Column(db.Text, default='')   # хранится зашифрованным (enc:…)
    model      = db.Column(db.String(200), default='')
    is_active  = db.Column(db.Boolean, nullable=False, default=True)
    is_default = db.Column(db.Boolean, nullable=False, default=False)
    # ИБ: локальный (внутренний) провайдер. Документы пакетов проверки
    # разрешено отправлять ТОЛЬКО локальным провайдерам.
    is_local   = db.Column(db.Boolean, nullable=False, default=False)
    # Явная capability: модель принимает изображения и возвращает координаты.
    # Никогда не выводится автоматически из имени модели.
    supports_vision = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)


class AiSummary(db.Model):
    """Кэш последнего ИИ-заключения по предприятию (один на предприятие, перезаписывается)."""
    __tablename__ = 'ai_summaries'

    id                = db.Column(db.Integer, primary_key=True)
    company_id        = db.Column(db.Integer, db.ForeignKey('companies.id'),
                                  nullable=False, unique=True)
    content           = db.Column(db.Text, default='')
    provider_name     = db.Column(db.String(100), default='')
    model_name        = db.Column(db.String(200), default='')
    prompt_tokens     = db.Column(db.Integer, default=0)
    completion_tokens = db.Column(db.Integer, default=0)
    generated_at      = db.Column(db.DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------------------
# Универсальный парсер документов: каталоги, правила, пакеты, файлы, страницы
# ---------------------------------------------------------------------------
class DocCatalog(db.Model):
    """Каталог документов — файлы раскладываются по каталогам по правилам."""
    __tablename__ = 'doc_catalogs'

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, default='')
    sort_order  = db.Column(db.Integer, default=0)
    is_system   = db.Column(db.Boolean, nullable=False, default=False)  # «Не распределено»
    created_at  = db.Column(db.DateTime(timezone=True), default=utcnow)

    routing_rules = db.relationship('DocRoutingRule', backref='catalog', lazy=True,
                                    cascade='all, delete-orphan')
    check_rules   = db.relationship('DocCheckRule', backref='catalog', lazy=True,
                                    cascade='all, delete-orphan')


class DocRoutingRule(db.Model):
    """Правило маршрутизации файла в каталог."""
    __tablename__ = 'doc_routing_rules'

    id         = db.Column(db.Integer, primary_key=True)
    catalog_id = db.Column(db.Integer, db.ForeignKey('doc_catalogs.id'), nullable=False)
    rule_type  = db.Column(db.String(20), nullable=False)  # 'filename' | 'keyword' | 'extension'
    pattern    = db.Column(db.String(500), nullable=False) # маска имени / ключевое слово / расширение
    priority   = db.Column(db.Integer, default=0)          # меньше = проверяется раньше
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)


class DocCheckRule(db.Model):
    """Правило проверки документов каталога (используется ИИ-анализом)."""
    __tablename__ = 'doc_check_rules'

    id          = db.Column(db.Integer, primary_key=True)
    catalog_id  = db.Column(db.Integer, db.ForeignKey('doc_catalogs.id'), nullable=False)
    name        = db.Column(db.String(255), nullable=False)   # «Наличие печати»
    prompt_text = db.Column(db.Text, nullable=False)          # что именно проверять
    is_active   = db.Column(db.Boolean, nullable=False, default=True)
    created_at  = db.Column(db.DateTime(timezone=True), default=utcnow)


class DocPackage(db.Model):
    """Загруженный пакет документов (один ZIP = один пакет).
    Статус хранится в БД — переживает перезапуск сервера."""
    __tablename__ = 'doc_packages'

    id          = db.Column(db.Integer, primary_key=True)
    title       = db.Column(db.String(255), default='')
    uploaded_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at  = db.Column(db.DateTime(timezone=True), default=utcnow)

    # pending | processing | done | error
    status       = db.Column(db.String(20), nullable=False, default='pending')
    error_text   = db.Column(db.Text, default='')
    files_total  = db.Column(db.Integer, default=0)
    files_done   = db.Column(db.Integer, default=0)
    zip_filename = db.Column(db.String(500), default='')

    # ИИ-проверка: none | running | done | error
    analysis_status = db.Column(db.String(20), nullable=False, default='none')
    analysis_error  = db.Column(db.Text, default='')
    analysis_total  = db.Column(db.Integer, default=0)   # файлов к проверке
    analysis_done   = db.Column(db.Integer, default=0)

    files = db.relationship('DocFile', backref='package', lazy=True,
                            cascade='all, delete-orphan')


class DocFile(db.Model):
    """Файл внутри пакета."""
    __tablename__ = 'doc_files'

    id          = db.Column(db.Integer, primary_key=True)
    package_id  = db.Column(db.Integer, db.ForeignKey('doc_packages.id'), nullable=False)
    catalog_id  = db.Column(db.Integer, db.ForeignKey('doc_catalogs.id'), nullable=True)
    orig_name   = db.Column(db.String(500), nullable=False)   # имя из архива
    stored_path = db.Column(db.String(1000), default='')      # путь на диске (санитизированный)
    pdf_path    = db.Column(db.String(1000), default='')      # сконвертированный PDF
    # pending | routed | converting | ready | error
    status      = db.Column(db.String(20), nullable=False, default='pending')
    error_text  = db.Column(db.Text, default='')
    pages_count = db.Column(db.Integer, default=0)
    created_at  = db.Column(db.DateTime(timezone=True), default=utcnow)
    # Согласование файла пользователем
    approved    = db.Column(db.Boolean, nullable=False, default=False)
    approved_at = db.Column(db.DateTime(timezone=True), nullable=True)

    # ИИ-анализ файла: none | processing | done | error
    analysis_status = db.Column(db.String(20), nullable=False, default='none')

    catalog = db.relationship('DocCatalog', backref='files', lazy=True)
    pages   = db.relationship('DocFilePage', backref='file', lazy=True,
                              cascade='all, delete-orphan')
    findings = db.relationship('DocFinding', backref='file', lazy=True,
                               cascade='all, delete-orphan')
    marks    = db.relationship('DocFileMark', backref='file', lazy=True,
                               cascade='all, delete-orphan')


class DocFilePage(db.Model):
    """Страница файла: извлечённый текст + путь к изображению."""
    __tablename__ = 'doc_file_pages'
    __table_args__ = (
        db.UniqueConstraint('file_id', 'page_num', name='uq_docpage_file_num'),
    )

    id         = db.Column(db.Integer, primary_key=True)
    file_id    = db.Column(db.Integer, db.ForeignKey('doc_files.id'), nullable=False)
    page_num   = db.Column(db.Integer, nullable=False)        # 1-based
    text       = db.Column(db.Text, default='')
    image_path = db.Column(db.String(1000), default='')
    ocr_used   = db.Column(db.Boolean, default=False)
    image_width  = db.Column(db.Integer, default=0)
    image_height = db.Column(db.Integer, default=0)
    # JSON-массив word-level токенов: text, bbox [x,y,w,h], confidence.
    words_json = db.Column(db.Text, default='[]')
    # pdf_text_v1 | ocr_v1 | photo_rectify_v1
    coordinate_version = db.Column(db.String(40), default='')


class DocCheckResult(db.Model):
    """Результат проверки файла по правилу каталога (заполняется ИИ-анализом)."""
    __tablename__ = 'doc_check_results'

    id            = db.Column(db.Integer, primary_key=True)
    file_id       = db.Column(db.Integer, db.ForeignKey('doc_files.id'), nullable=False)
    check_rule_id = db.Column(db.Integer, db.ForeignKey('doc_check_rules.id'), nullable=True)
    # ok | violation | not_found | error
    status     = db.Column(db.String(20), nullable=False, default='not_found')
    details    = db.Column(db.Text, default='')     # JSON с деталями от ИИ
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    file       = db.relationship(
        'DocFile',
        backref=db.backref('check_results', cascade='all, delete-orphan', lazy=True),
        lazy=True)
    check_rule = db.relationship('DocCheckRule', lazy=True)


class DocFinding(db.Model):
    """Находка ИИ на странице файла: дата / подпись / печать."""
    __tablename__ = 'doc_findings'

    id          = db.Column(db.Integer, primary_key=True)
    file_id     = db.Column(db.Integer, db.ForeignKey('doc_files.id'), nullable=False)
    page_num    = db.Column(db.Integer, nullable=False, default=1)
    ftype       = db.Column(db.String(20), nullable=False)  # 'date'|'signature'|'seal'
    snippet     = db.Column(db.Text, default='')            # цитата из текста
    description = db.Column(db.Text, default='')            # пояснение ИИ
    source      = db.Column(db.String(20), default='')       # pdf_text|ocr_text|vision
    # JSON-массив нормализованных прямоугольников [x,y,w,h], значения 0..1.
    boxes_json  = db.Column(db.Text, default='[]')
    confidence  = db.Column(db.Float, nullable=True)
    algorithm_version = db.Column(db.String(40), default='')
    created_at  = db.Column(db.DateTime(timezone=True), default=utcnow)


class DocMarkButton(db.Model):
    """Настраиваемая кнопка ручной отметки (например «Нет подписи»).

    catalog_id = NULL — глобальная кнопка, иначе действует для файлов каталога."""
    __tablename__ = 'doc_mark_buttons'

    id         = db.Column(db.Integer, primary_key=True)
    key        = db.Column(db.String(100), nullable=False, unique=True)
    label      = db.Column(db.String(255), nullable=False)
    catalog_id = db.Column(db.Integer, db.ForeignKey('doc_catalogs.id'), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    catalog = db.relationship('DocCatalog', lazy=True)


class DocFileMark(db.Model):
    """Ручная отметка пользователя на странице файла."""
    __tablename__ = 'doc_file_marks'
    __table_args__ = (
        db.UniqueConstraint('file_id', 'page_num', 'button_key',
                            name='uq_docmark_file_page_key'),
    )

    id         = db.Column(db.Integer, primary_key=True)
    file_id    = db.Column(db.Integer, db.ForeignKey('doc_files.id'), nullable=False)
    page_num   = db.Column(db.Integer, nullable=False, default=1)
    button_key = db.Column(db.String(100), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)


class AnalysisTemplate(db.Model):
    """Шаблон параметров ИИ-анализа (системный промпт + типы находок)."""
    __tablename__ = 'doc_analysis_templates'

    id              = db.Column(db.Integer, primary_key=True)
    name            = db.Column(db.String(255), nullable=False)
    system_prompt   = db.Column(db.Text, default='')
    find_dates      = db.Column(db.Boolean, nullable=False, default=True)
    find_signatures = db.Column(db.Boolean, nullable=False, default=True)
    find_seals      = db.Column(db.Boolean, nullable=False, default=True)
    created_at      = db.Column(db.DateTime(timezone=True), default=utcnow)


class DocCatalogAnalysisRun(db.Model):
    """Версия анализа фиксированного набора документов одного каталога."""
    __tablename__ = 'doc_catalog_analysis_runs'

    id              = db.Column(db.Integer, primary_key=True)
    package_id      = db.Column(db.Integer, db.ForeignKey('doc_packages.id'),
                                nullable=False)
    catalog_id      = db.Column(
        db.Integer, db.ForeignKey('doc_catalogs.id', ondelete='SET NULL'),
        nullable=True)
    catalog_name_snapshot = db.Column(db.String(255), default='')
    created_by      = db.Column(db.Integer, db.ForeignKey('users.id'),
                                nullable=False)
    mode            = db.Column(db.String(20), nullable=False)
    user_prompt     = db.Column(db.Text, default='')
    template_id     = db.Column(db.Integer,
                                db.ForeignKey('doc_analysis_templates.id'))
    template_name   = db.Column(db.String(255), default='')
    template_prompt = db.Column(db.Text, default='')
    file_ids_json   = db.Column(db.Text, default='[]')
    skipped_json    = db.Column(db.Text, default='[]')
    options_json    = db.Column(db.Text, default='{}')
    summary_json    = db.Column(db.Text, default='{}')
    errors_json     = db.Column(db.Text, default='[]')
    status          = db.Column(db.String(20), nullable=False, default='queued')
    files_total     = db.Column(db.Integer, nullable=False, default=0)
    files_done      = db.Column(db.Integer, nullable=False, default=0)
    files_cancelled = db.Column(db.Integer, nullable=False, default=0)
    error_text      = db.Column(db.Text, default='')
    cancel_requested_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    cancel_requested_at = db.Column(db.DateTime(timezone=True))
    cancelled_at    = db.Column(db.DateTime(timezone=True))
    created_at      = db.Column(db.DateTime(timezone=True), default=utcnow)
    started_at      = db.Column(db.DateTime(timezone=True))
    completed_at    = db.Column(db.DateTime(timezone=True))

    package = db.relationship('DocPackage', lazy=True)
    catalog = db.relationship('DocCatalog', lazy=True)
    author = db.relationship('User', foreign_keys=[created_by], lazy=True)
    cancel_requester = db.relationship(
        'User', foreign_keys=[cancel_requested_by], lazy=True)
    template = db.relationship('AnalysisTemplate', lazy=True)


class DocAnalysisRun(db.Model):
    """Воспроизводимый запуск ИИ-анализа одного документа.

    Отдельная сущность не смешивает историю запусков со сводным статусом файла
    и сохраняет снимок промпта/шаблона на момент запуска.
    """
    __tablename__ = 'doc_analysis_runs'

    id                = db.Column(db.Integer, primary_key=True)
    package_id        = db.Column(db.Integer, db.ForeignKey('doc_packages.id'),
                                  nullable=False)
    catalog_run_id    = db.Column(
        db.Integer, db.ForeignKey('doc_catalog_analysis_runs.id'),
        nullable=True)
    file_id           = db.Column(db.Integer, db.ForeignKey('doc_files.id'),
                                  nullable=False)
    created_by        = db.Column(db.Integer, db.ForeignKey('users.id'),
                                  nullable=False)
    # supplement | free
    mode              = db.Column(db.String(20), nullable=False)
    # current_file (следующие задачи расширят список областей)
    scope             = db.Column(db.String(30), nullable=False,
                                  default='current_file')
    user_prompt       = db.Column(db.Text, default='')
    template_id       = db.Column(db.Integer,
                                  db.ForeignKey('doc_analysis_templates.id'),
                                  nullable=True)
    template_name     = db.Column(db.String(255), default='')
    template_prompt   = db.Column(db.Text, default='')
    options_json      = db.Column(db.Text, default='{}')
    result_json       = db.Column(db.Text, default='{}')
    # queued | running | done | error
    status            = db.Column(db.String(20), nullable=False, default='queued')
    error_text        = db.Column(db.Text, default='')
    created_at        = db.Column(db.DateTime(timezone=True), default=utcnow)
    started_at        = db.Column(db.DateTime(timezone=True), nullable=True)
    completed_at      = db.Column(db.DateTime(timezone=True), nullable=True)

    package = db.relationship('DocPackage', lazy=True)
    file = db.relationship(
        'DocFile',
        backref=db.backref('analysis_runs', cascade='all, delete-orphan',
                           lazy=True),
        lazy=True)
    author = db.relationship('User', lazy=True)
    template = db.relationship('AnalysisTemplate', lazy=True)
    catalog_run = db.relationship(
        'DocCatalogAnalysisRun',
        backref=db.backref('file_runs', cascade='all, delete-orphan',
                           lazy=True),
        lazy=True)


class DocFreeAnalysisResult(db.Model):
    """Текстовый результат свободного вопроса к документу."""
    __tablename__ = 'doc_free_analysis_results'

    id         = db.Column(db.Integer, primary_key=True)
    run_id     = db.Column(db.Integer, db.ForeignKey('doc_analysis_runs.id'),
                           nullable=False, unique=True)
    file_id    = db.Column(db.Integer, db.ForeignKey('doc_files.id'),
                           nullable=False)
    content    = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    run = db.relationship(
        'DocAnalysisRun',
        backref=db.backref('free_result', uselist=False,
                           cascade='all, delete-orphan'),
        lazy=True)
    file = db.relationship('DocFile', lazy=True)


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

    # Provenance is explicit metadata, never inferred from a zero balance.
    # Existing rows predate provenance tracking and remain honestly unknown.
    source_type = db.Column(db.String(40), nullable=False, default='unknown')
    uploaded_at = db.Column(db.DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------------------
# База объектов-аналогов теплоснабжения
# ---------------------------------------------------------------------------
class AnalogPassport(db.Model):
    """Версионный паспорт объекта, собранный из заключения и ССР.

    Денежные значения намеренно имеют Numeric, а не Float: стоимость и
    коэффициенты участвуют в юридически значимых сверках.
    """
    __tablename__ = 'analog_passports'
    __table_args__ = (
        db.Index('ix_analog_passport_search', 'object_type', 'climate_zone',
                 'work_type', 'capacity_kw'),
        db.Index('ix_analog_passport_active', 'archived', 'updated_at'),
    )

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(1000), nullable=False)
    address = db.Column(db.Text, default='')
    object_type = db.Column(db.String(32), nullable=False, default='котельная')
    climate_zone = db.Column(db.String(32), default='')
    climate_subzone = db.Column(db.String(32), default='')
    climate_source = db.Column(db.Text, default='')
    climate_reference_version = db.Column(db.String(80), default='')
    climate_reference_date = db.Column(db.Date, nullable=True)
    cadastral_number = db.Column(db.String(64), default='')
    natural_conditions = db.Column(db.Text, default='')
    construction_object = db.Column(db.Text, default='')
    capacity_kw = db.Column(db.Numeric(18, 6), nullable=True)
    work_type = db.Column(db.String(32), default='')
    archived = db.Column(db.Boolean, nullable=False, default=False, index=True)
    current_version_id = db.Column(db.Integer, nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow,
                           nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow,
                           onupdate=utcnow, nullable=False)

    versions = db.relationship('AnalogPassportVersion', backref='passport',
                               lazy=True, cascade='all, delete-orphan',
                               foreign_keys='AnalogPassportVersion.passport_id')
    organizations = db.relationship('AnalogOrganizationRole', backref='passport',
                                    lazy=True, cascade='all, delete-orphan')
    teps = db.relationship('AnalogTep', backref='passport', lazy=True,
                           cascade='all, delete-orphan')
    documents = db.relationship('AnalogDocument', backref='passport', lazy=True,
                                cascade='all, delete-orphan')
    costs = db.relationship('AnalogCostRow', backref='passport', lazy=True,
                            cascade='all, delete-orphan')
    network_attributes = db.relationship(
        'AnalogNetworkAttribute', backref='passport', lazy=True,
        cascade='all, delete-orphan')
    extracted_json = db.Column(db.Text, nullable=False, default='{}')
    overrides_json = db.Column(db.Text, nullable=False, default='{}')
    provenance_json = db.Column(db.Text, nullable=False, default='{}')
    legacy_json = db.Column(db.Text, nullable=False, default='{}')


class AnalogPassportVersion(db.Model):
    __tablename__ = 'analog_passport_versions'
    __table_args__ = (
        db.UniqueConstraint('passport_id', 'version_no',
                            name='uq_analog_passport_version'),
        db.Index('ix_analog_version_conclusion', 'conclusion_number'),
    )
    id = db.Column(db.Integer, primary_key=True)
    passport_id = db.Column(db.Integer, db.ForeignKey(
        'analog_passports.id', ondelete='CASCADE'), nullable=False)
    version_no = db.Column(db.Integer, nullable=False)
    conclusion_number = db.Column(db.String(255), nullable=False)
    approval_date = db.Column(db.Date, nullable=True)
    examination_result = db.Column(db.String(24), nullable=False)
    expert_object_name = db.Column(db.Text, default='')
    cadastral_number = db.Column(db.String(64), default='')
    natural_conditions = db.Column(db.Text, default='')
    construction_object = db.Column(db.Text, default='')
    source_hash = db.Column(db.String(64), nullable=False, index=True)
    ssr_hash = db.Column(db.String(64), nullable=False, index=True)
    schema_version = db.Column(db.String(40), default='')
    price_year = db.Column(db.Integer, nullable=True)
    price_quarter = db.Column(db.Integer, nullable=True)
    total_cost = db.Column(db.Numeric(20, 6), nullable=True)
    adjusted_cost = db.Column(db.Numeric(20, 6), nullable=True)
    adjustment_factor = db.Column(db.Numeric(18, 8), nullable=True)
    adjustment_provenance = db.Column(db.Text, default='')
    adjustment_reference_version = db.Column(db.String(80), default='')
    adjustment_reference_date = db.Column(db.Date, nullable=True)
    adjustment_source = db.Column(db.Text, default='')
    adjustment_price_level = db.Column(db.String(120), default='')
    payload_json = db.Column(db.Text, default='{}')
    imported_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow,
                           nullable=False)
    source = db.relationship('AnalogSource', backref='version', uselist=False,
                             cascade='all, delete-orphan', lazy=True)
    chapters = db.relationship('AnalogSSRChapter', backref='version', lazy=True,
                               cascade='all, delete-orphan')
    items = db.relationship('AnalogSSRItem', backref='version', lazy=True,
                            cascade='all, delete-orphan')
    diagnostics = db.relationship('AnalogDiagnostic', backref='version', lazy=True,
                                  cascade='all, delete-orphan')
    signatures = db.relationship('AnalogSSRSignature', backref='version', lazy=True,
                                 cascade='all, delete-orphan')


class AnalogClimateReference(db.Model):
    """Версионное адресное правило согласованного климатического справочника."""
    __tablename__ = 'analog_climate_references'
    __table_args__ = (
        db.Index('ix_analog_climate_lookup', 'active', 'address_pattern'),
    )
    id = db.Column(db.Integer, primary_key=True)
    address_pattern = db.Column(db.String(500), nullable=False)
    climate_zone = db.Column(db.String(32), nullable=False)
    climate_subzone = db.Column(db.String(32), default='')
    source = db.Column(db.Text, nullable=False)
    reference_version = db.Column(db.String(80), nullable=False)
    reference_date = db.Column(db.Date, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


class AnalogCostIndexReference(db.Model):
    """Версионный коэффициент для даты расчёта и исходного уровня цен."""
    __tablename__ = 'analog_cost_index_references'
    __table_args__ = (
        db.UniqueConstraint('effective_date', 'price_level', 'reference_version',
                            name='uq_analog_cost_index_version'),
        db.Index('ix_analog_cost_index_lookup', 'active', 'price_level',
                 'effective_date'),
    )
    id = db.Column(db.Integer, primary_key=True)
    effective_date = db.Column(db.Date, nullable=False)
    price_level = db.Column(db.String(120), nullable=False)
    factor = db.Column(db.Numeric(18, 8), nullable=False)
    source = db.Column(db.Text, nullable=False)
    reference_version = db.Column(db.String(80), nullable=False)
    reference_date = db.Column(db.Date, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


class AnalogSource(db.Model):
    __tablename__ = 'analog_sources'
    __table_args__ = (
        db.UniqueConstraint('source_hash', 'ssr_hash',
                            name='uq_analog_source_pair'),
    )
    id = db.Column(db.Integer, primary_key=True)
    version_id = db.Column(db.Integer, db.ForeignKey(
        'analog_passport_versions.id', ondelete='CASCADE'), nullable=False,
        unique=True)
    source_hash = db.Column(db.String(64), nullable=False, index=True)
    ssr_hash = db.Column(db.String(64), nullable=False, index=True)
    source_filename = db.Column(db.String(500), default='')
    ssr_filename = db.Column(db.String(500), default='')
    source_size = db.Column(db.BigInteger, nullable=False, default=0)
    ssr_size = db.Column(db.BigInteger, nullable=False, default=0)
    raw_conclusion_path = db.Column(db.String(1000), nullable=False, default='')
    raw_ssr_path = db.Column(db.String(1000), nullable=False, default='')
    parser_version = db.Column(db.String(40), default='1')
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow,
                           nullable=False)


class AnalogOrganizationRole(db.Model):
    __tablename__ = 'analog_organization_roles'
    __table_args__ = (
        db.UniqueConstraint('passport_id', 'version_id', 'role',
                            name='uq_analog_organization_role'),
    )
    id = db.Column(db.Integer, primary_key=True)
    passport_id = db.Column(db.Integer, db.ForeignKey(
        'analog_passports.id', ondelete='CASCADE'), nullable=False)
    role = db.Column(db.String(32), nullable=False)  # applicant/developer/customer
    name = db.Column(db.String(1000), nullable=False, default='')
    ogrn = db.Column(db.String(32), default='')
    inn = db.Column(db.String(32), default='')
    kpp = db.Column(db.String(32), default='')
    email = db.Column(db.String(320), default='')
    address = db.Column(db.Text, default='')
    version_id = db.Column(db.Integer, db.ForeignKey(
        'analog_passport_versions.id', ondelete='CASCADE'), nullable=False,
        index=True)
    extracted_json = db.Column(db.Text, nullable=False, default='{}')
    override_json = db.Column(db.Text, nullable=False, default='{}')
    effective_json = db.Column(db.Text, nullable=False, default='{}')
    provenance = db.Column(db.Text, default='conclusionRaw')


class AnalogTep(db.Model):
    __tablename__ = 'analog_teps'
    __table_args__ = (db.Index('ix_analog_tep_name_value', 'name', 'value'),)
    id = db.Column(db.Integer, primary_key=True)
    passport_id = db.Column(db.Integer, db.ForeignKey(
        'analog_passports.id', ondelete='CASCADE'), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    unit = db.Column(db.String(80), default='')
    value = db.Column(db.Numeric(24, 8), nullable=True)
    normalized_value = db.Column(db.Numeric(24, 8), nullable=True)
    normalized_unit = db.Column(db.String(80), default='')
    value_text = db.Column(db.Text, default='')
    provenance = db.Column(db.String(500), default='')
    version_id = db.Column(db.Integer, db.ForeignKey(
        'analog_passport_versions.id', ondelete='CASCADE'), nullable=False,
        index=True)
    extracted_json = db.Column(db.Text, nullable=False, default='{}')
    override_json = db.Column(db.Text, nullable=False, default='{}')
    effective_json = db.Column(db.Text, nullable=False, default='{}')


class AnalogDocument(db.Model):
    __tablename__ = 'analog_documents'
    id = db.Column(db.Integer, primary_key=True)
    passport_id = db.Column(db.Integer, db.ForeignKey(
        'analog_passports.id', ondelete='CASCADE'), nullable=False, index=True)
    parent_id = db.Column(db.Integer, db.ForeignKey('analog_documents.id'))
    ordinal = db.Column(db.String(40), default='')
    parent_ordinal = db.Column(db.String(40), default='')
    node_kind = db.Column(db.String(16), nullable=False, default='document')
    name = db.Column(db.String(1000), nullable=False, default='')
    file_format = db.Column(db.String(80), default='')
    checksum = db.Column(db.String(255), default='')
    note = db.Column(db.Text, default='')
    signature_name = db.Column(db.String(1000), default='')
    signature_checksum = db.Column(db.String(255), default='')
    version_id = db.Column(db.Integer, db.ForeignKey(
        'analog_passport_versions.id', ondelete='CASCADE'), nullable=False,
        index=True)
    extracted_json = db.Column(db.Text, nullable=False, default='{}')
    override_json = db.Column(db.Text, nullable=False, default='{}')
    effective_json = db.Column(db.Text, nullable=False, default='{}')
    provenance = db.Column(db.Text, default='conclusionRaw/Documents')
    parent = db.relationship('AnalogDocument', remote_side=[id],
                             back_populates='children', lazy=True)
    children = db.relationship('AnalogDocument', back_populates='parent',
                                lazy=True, cascade='all, delete-orphan')


class AnalogCostRow(db.Model):
    __tablename__ = 'analog_cost_rows'
    id = db.Column(db.Integer, primary_key=True)
    passport_id = db.Column(db.Integer, db.ForeignKey(
        'analog_passports.id', ondelete='CASCADE'), nullable=False, index=True)
    ordinal = db.Column(db.String(40), default='')
    name = db.Column(db.String(1000), nullable=False, default='')
    submitted_cost = db.Column(db.Numeric(20, 6), nullable=True)
    approved_cost = db.Column(db.Numeric(20, 6), nullable=True)
    change_cost = db.Column(db.Numeric(20, 6), nullable=True)
    price_level = db.Column(db.String(120), default='')
    vat = db.Column(db.String(80), default='')
    footnote = db.Column(db.Text, default='')
    change_reason = db.Column(db.Text, default='')
    provenance = db.Column(db.Text, default='')
    version_id = db.Column(db.Integer, db.ForeignKey(
        'analog_passport_versions.id', ondelete='CASCADE'), nullable=False,
        index=True)
    extracted_json = db.Column(db.Text, nullable=False, default='{}')
    override_json = db.Column(db.Text, nullable=False, default='{}')
    effective_json = db.Column(db.Text, nullable=False, default='{}')


class AnalogSSRChapter(db.Model):
    __tablename__ = 'analog_ssr_chapters'
    id = db.Column(db.Integer, primary_key=True)
    version_id = db.Column(db.Integer, db.ForeignKey(
        'analog_passport_versions.id', ondelete='CASCADE'), nullable=False,
        index=True)
    number = db.Column(db.Integer, nullable=False)
    name = db.Column(db.Text, default='')
    building = db.Column(db.Numeric(20, 6), default=0)
    mounting = db.Column(db.Numeric(20, 6), default=0)
    equipment = db.Column(db.Numeric(20, 6), default=0)
    other = db.Column(db.Numeric(20, 6), default=0)
    total = db.Column(db.Numeric(20, 6), default=0)
    provenance = db.Column(db.Text, default='')
    extracted_json = db.Column(db.Text, nullable=False, default='{}')
    override_json = db.Column(db.Text, nullable=False, default='{}')
    effective_json = db.Column(db.Text, nullable=False, default='{}')


class AnalogSSRItem(db.Model):
    __tablename__ = 'analog_ssr_items'
    id = db.Column(db.Integer, primary_key=True)
    version_id = db.Column(db.Integer, db.ForeignKey(
        'analog_passport_versions.id', ondelete='CASCADE'), nullable=False,
        index=True)
    chapter_number = db.Column(db.Integer, nullable=False)
    item_type = db.Column(db.String(32), default='')
    ordinal = db.Column(db.String(40), default='')
    name = db.Column(db.Text, default='')
    reason = db.Column(db.Text, default='')
    total = db.Column(db.Numeric(20, 6), default=0)
    details_json = db.Column(db.Text, default='{}')
    signature_name = db.Column(db.String(1000), default='')
    extracted_json = db.Column(db.Text, nullable=False, default='{}')
    override_json = db.Column(db.Text, nullable=False, default='{}')
    effective_json = db.Column(db.Text, nullable=False, default='{}')
    provenance = db.Column(db.Text, default='ССР 3.01')


class AnalogSSRSignature(db.Model):
    __tablename__ = 'analog_ssr_signatures'
    id = db.Column(db.Integer, primary_key=True)
    version_id = db.Column(db.Integer, db.ForeignKey(
        'analog_passport_versions.id', ondelete='CASCADE'), nullable=False,
        index=True)
    role = db.Column(db.String(120), default='')
    name = db.Column(db.String(1000), default='')
    position = db.Column(db.String(500), default='')
    department = db.Column(db.String(500), default='')
    provenance = db.Column(db.Text, default='ССР 3.01/Signatures')


class AnalogDiagnostic(db.Model):
    __tablename__ = 'analog_diagnostics'
    id = db.Column(db.Integer, primary_key=True)
    version_id = db.Column(db.Integer, db.ForeignKey(
        'analog_passport_versions.id', ondelete='CASCADE'), nullable=True,
        index=True)
    import_id = db.Column(db.Integer, nullable=True, index=True)
    severity = db.Column(db.String(16), nullable=False, default='warning')
    code = db.Column(db.String(80), nullable=False)
    message = db.Column(db.Text, nullable=False)
    path = db.Column(db.String(500), default='')
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)


class AnalogNetworkAttribute(db.Model):
    __tablename__ = 'analog_network_attributes'
    __table_args__ = (
        db.UniqueConstraint('passport_id', 'version_id', 'key',
                            name='uq_analog_network_attribute_version'),
    )
    id = db.Column(db.Integer, primary_key=True)
    passport_id = db.Column(db.Integer, db.ForeignKey(
        'analog_passports.id', ondelete='CASCADE'), nullable=False, index=True)
    version_id = db.Column(db.Integer, db.ForeignKey(
        'analog_passport_versions.id', ondelete='CASCADE'), nullable=False,
        index=True)
    key = db.Column(db.String(120), nullable=False)
    value = db.Column(db.Text, default='')
    value_number = db.Column(db.Numeric(24, 8), nullable=True)
    unit = db.Column(db.String(80), default='')
    provenance = db.Column(db.String(500), default='JSON v4')


class AnalogImport(db.Model):
    __tablename__ = 'analog_imports'
    __table_args__ = (
        db.Index('ix_analog_import_status_created', 'status', 'created_at'),
    )
    id = db.Column(db.Integer, primary_key=True)
    source_hash = db.Column(db.String(64), nullable=False, index=True)
    ssr_hash = db.Column(db.String(64), nullable=False, index=True)
    source_filename = db.Column(db.String(500), default='')
    ssr_filename = db.Column(db.String(500), default='')
    raw_conclusion_path = db.Column(db.String(1000), default='')
    raw_ssr_path = db.Column(db.String(1000), default='')
    target_passport_id = db.Column(db.Integer, db.ForeignKey(
        'analog_passports.id', ondelete='SET NULL'), nullable=True, index=True)
    status = db.Column(db.String(24), nullable=False, default='preview')
    preview_json = db.Column(db.Text, nullable=False, default='{}')
    diagnostics_json = db.Column(db.Text, nullable=False, default='[]')
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    confirmed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    @property
    def preview(self):
        try:
            return json.loads(self.preview_json or '{}')
        except (TypeError, ValueError):
            return {}

    @property
    def diagnostics(self):
        try:
            return json.loads(self.diagnostics_json or '[]')
        except (TypeError, ValueError):
            return []
