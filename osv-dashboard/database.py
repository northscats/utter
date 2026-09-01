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
    role = db.Column(db.String(20), nullable=False, default='user')  # 'admin' | 'user'
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

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

    uploaded_at = db.Column(db.DateTime(timezone=True), default=utcnow)
