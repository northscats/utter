# -*- coding: utf-8 -*-
"""Пайплайн универсального парсера документов.

ZIP → безопасная распаковка → маршрутизация по каталогам → PDF → страницы.

ИБ-требования:
  * ZipSlip-защита: realpath-проверка каждого распакованного пути.
  * Zip-bomb-защита: лимит на количество файлов и суммарный размер.
  * Кодировки CP1251/CP866 в именах файлов внутри ZIP.
  * Временные файлы удаляются через try/finally.
  * Статус обработки хранится в БД (DocPackage.status) — переживает рестарт.
"""
import fnmatch
import logging
import os
import shutil
import tempfile
import zipfile

from database import (DocCatalog, DocFile, DocFilePage, DocPackage,
                      DocRoutingRule, db)
from doc_parser import (SUPPORTED_EXTS, extract_pdf_pages_text,
                        get_pdf_page_count, ocr_image_file, prepare_pdf,
                        render_pdf_page, sanitize_filename)

logger = logging.getLogger(__name__)

# Лимиты пакета (защита от zip-bomb)
MAX_FILES_PER_PACKAGE = 100
MAX_TOTAL_UNCOMPRESSED = 500 * 1024 * 1024   # 500 МБ
MAX_SINGLE_FILE = 100 * 1024 * 1024          # 100 МБ на один файл

# Лимиты обработки (защита от DoS через «тяжёлые» документы)
MAX_PAGES_PER_FILE = 300        # страницы сверх лимита не обрабатываются
_PROCESS_SEMAPHORE = __import__('threading').Semaphore(1)  # 1 пакет за раз

UNROUTED_CATALOG_NAME = 'Не распределено'


class PackageValidationError(Exception):
    """Пакет отклонён по ИБ-причине (ZipSlip, zip-bomb, лимиты)."""


def recover_stale_packages():
    """При старте сервера помечает зависшие пакеты ошибкой.

    Фоновые daemon-потоки погибают при рестарте; пакеты в состоянии
    pending/processing без работающего потока «зависают» навсегда.
    Вызывается из init_db() внутри app_context.
    """
    stale = DocPackage.query.filter(
        DocPackage.status.in_(('pending', 'processing'))).all()
    for pkg in stale:
        pkg.status = 'error'
        pkg.error_text = ('Обработка прервана перезапуском сервера. '
                          'Загрузите архив повторно.')
    stale_an = DocPackage.query.filter_by(analysis_status='running').all()
    for pkg in stale_an:
        pkg.analysis_status = 'error'
        pkg.analysis_error = ('Проверка прервана перезапуском сервера. '
                              'Запустите проверку повторно.')
    if stale or stale_an:
        db.session.commit()
        logger.warning('recover_stale_packages: обработка=%d, проверка=%d',
                       len(stale), len(stale_an))


# ---------------------------------------------------------------------------
# Безопасная распаковка
# ---------------------------------------------------------------------------
def _decode_zip_name(info: zipfile.ZipInfo) -> str:
    """Имя файла из ZIP с учётом русских кодировок (CP866/CP1251)."""
    if info.flag_bits & 0x800:            # UTF-8 flag установлен
        return info.filename
    raw = info.filename.encode('cp437', errors='replace')
    for enc in ('cp866', 'cp1251', 'utf-8'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return info.filename


def validate_zip(zip_path: str) -> list[zipfile.ZipInfo]:
    """Проверяет архив на лимиты и ZipSlip ДО распаковки.

    Возвращает список файлов к распаковке. Бросает PackageValidationError.
    """
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise PackageValidationError('Файл не является корректным ZIP-архивом.')

    with zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        # Скрытые/служебные файлы macOS и т.п. пропускаем
        members = [m for m in members
                   if not os.path.basename(m.filename).startswith(('.', '__MACOSX'))
                   and '__MACOSX' not in m.filename]

        if len(members) == 0:
            raise PackageValidationError('Архив пуст.')
        if len(members) > MAX_FILES_PER_PACKAGE:
            raise PackageValidationError(
                f'В архиве {len(members)} файлов — превышен лимит '
                f'{MAX_FILES_PER_PACKAGE}.')

        total = sum(m.file_size for m in members)
        if total > MAX_TOTAL_UNCOMPRESSED:
            raise PackageValidationError(
                f'Распакованный размер {total // (1024 * 1024)} МБ — превышен '
                f'лимит {MAX_TOTAL_UNCOMPRESSED // (1024 * 1024)} МБ.')

        for m in members:
            if m.file_size > MAX_SINGLE_FILE:
                raise PackageValidationError(
                    f'Файл «{m.filename}» больше '
                    f'{MAX_SINGLE_FILE // (1024 * 1024)} МБ.')
            # ZipSlip: абсолютные пути и попытки выйти из каталога
            name = m.filename.replace('\\', '/')
            if name.startswith('/') or '..' in name.split('/'):
                raise PackageValidationError(
                    f'Недопустимый путь в архиве: «{m.filename}» (ZipSlip).')
        return members


def safe_extract(zip_path: str, dest_dir: str) -> list[tuple[str, str]]:
    """Безопасно распаковывает архив.

    Возвращает список (оригинальное_имя, путь_на_диске).
    Каждый файл сохраняется под санитизированным плоским именем.
    """
    members = validate_zip(zip_path)
    os.makedirs(dest_dir, exist_ok=True)
    dest_real = os.path.realpath(dest_dir)
    out = []
    with zipfile.ZipFile(zip_path) as zf:
        for idx, m in enumerate(members):
            orig_name = _decode_zip_name(m)
            base = sanitize_filename(orig_name)
            stored_name = f'{idx:03d}_{base}'
            target = os.path.join(dest_dir, stored_name)
            # Контрольная realpath-проверка (защита в глубину)
            if not os.path.realpath(target).startswith(dest_real + os.sep):
                raise PackageValidationError(
                    f'ZipSlip-попытка: «{orig_name}».')
            with zf.open(m) as src, open(target, 'wb') as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
            out.append((orig_name, target))
    return out


# ---------------------------------------------------------------------------
# Маршрутизация
# ---------------------------------------------------------------------------
def get_unrouted_catalog() -> DocCatalog:
    """Системный каталог «Не распределено» (создаётся при первом обращении)."""
    cat = DocCatalog.query.filter_by(is_system=True).first()
    if cat is None:
        cat = DocCatalog(name=UNROUTED_CATALOG_NAME, is_system=True,
                         sort_order=9999,
                         description='Файлы, не подошедшие ни под одно правило')
        db.session.add(cat)
        db.session.commit()
    return cat


def route_file(orig_name: str) -> DocCatalog | None:
    """Подбирает каталог для файла по правилам. None — не распределён."""
    name_lower = orig_name.lower()
    ext = os.path.splitext(name_lower)[1].lstrip('.')
    rules = (DocRoutingRule.query
             .join(DocCatalog)
             .filter(DocCatalog.is_system.is_(False))
             .order_by(DocRoutingRule.priority, DocRoutingRule.id)
             .all())
    for rule in rules:
        pattern = (rule.pattern or '').strip().lower()
        if not pattern:
            continue
        if rule.rule_type == 'filename':
            # fnmatch-маска: «*акт*.pdf»
            if fnmatch.fnmatch(name_lower, pattern):
                return rule.catalog
        elif rule.rule_type == 'keyword':
            if pattern in name_lower:
                return rule.catalog
        elif rule.rule_type == 'extension':
            if ext == pattern.lstrip('.'):
                return rule.catalog
    return None


# ---------------------------------------------------------------------------
# Основной пайплайн (выполняется в фоновом потоке)
# ---------------------------------------------------------------------------
def process_package(app, package_id: int, zip_path: str, storage_root: str):
    """Полный цикл обработки пакета. Вызывается в threading.Thread.

    Статус пишется в БД на каждом шаге — фронтенд поллит /docs/packages/<id>/status.
    Обработка сериализована семафором: не более одного пакета одновременно,
    чтобы LibreOffice/OCR не исчерпали ресурсы сервера.
    """
    with _PROCESS_SEMAPHORE, app.app_context():
        pkg = db.session.get(DocPackage, package_id)
        if pkg is None:
            return
        pkg.status = 'processing'
        db.session.commit()

        pkg_dir = os.path.join(storage_root, str(package_id))
        files_dir = os.path.join(pkg_dir, 'files')
        pdf_dir = os.path.join(pkg_dir, 'pdf')
        pages_dir = os.path.join(pkg_dir, 'pages')
        tmp_extract = tempfile.mkdtemp(prefix=f'docpkg_{package_id}_')

        try:
            # 1. Распаковка (безопасная)
            try:
                extracted = safe_extract(zip_path, tmp_extract)
            except PackageValidationError as ve:
                pkg.status = 'error'
                pkg.error_text = str(ve)
                db.session.commit()
                return

            os.makedirs(files_dir, exist_ok=True)
            unrouted = get_unrouted_catalog()

            # 2. Регистрация файлов + маршрутизация
            doc_files = []
            for orig_name, tmp_path in extracted:
                stored = os.path.join(files_dir, os.path.basename(tmp_path))
                shutil.move(tmp_path, stored)
                ext = os.path.splitext(orig_name)[1].lower()
                catalog = route_file(orig_name) if ext in SUPPORTED_EXTS else None
                df = DocFile(
                    package_id=package_id,
                    catalog_id=(catalog or unrouted).id,
                    orig_name=orig_name[:500],
                    stored_path=stored,
                    status='routed' if ext in SUPPORTED_EXTS else 'error',
                    error_text=('' if ext in SUPPORTED_EXTS
                                else f'Неподдерживаемый формат: {ext or "без расширения"}'),
                )
                db.session.add(df)
                doc_files.append(df)
            pkg.files_total = len(doc_files)
            db.session.commit()

            # 3. Конвертация в PDF + страницы (по одному файлу, статус в БД)
            for df in doc_files:
                if df.status == 'error':
                    pkg.files_done += 1
                    db.session.commit()
                    continue
                try:
                    df.status = 'converting'
                    db.session.commit()

                    pdf_path = prepare_pdf(df.stored_path, pdf_dir)
                    if not pdf_path:
                        raise RuntimeError('Не удалось сконвертировать в PDF')
                    df.pdf_path = pdf_path

                    n_pages = get_pdf_page_count(pdf_path)
                    if n_pages == 0:
                        raise RuntimeError('PDF без страниц или повреждён')
                    if n_pages > MAX_PAGES_PER_FILE:
                        df.error_text = (f'Документ содержит {n_pages} страниц; '
                                         f'обработаны первые {MAX_PAGES_PER_FILE}.')
                        n_pages = MAX_PAGES_PER_FILE

                    texts = extract_pdf_pages_text(pdf_path)
                    file_pages_dir = os.path.join(pages_dir, str(df.id))
                    os.makedirs(file_pages_dir, exist_ok=True)

                    for p in range(1, n_pages + 1):
                        img_path = os.path.join(file_pages_dir, f'p{p}.png')
                        has_img = render_pdf_page(pdf_path, p, img_path)
                        text = texts[p - 1] if p <= len(texts) else ''
                        ocr_used = False
                        # OCR-fallback: текст не извлёкся — вероятно скан
                        if not text and has_img:
                            text = ocr_image_file(img_path)
                            ocr_used = bool(text)
                        db.session.add(DocFilePage(
                            file_id=df.id, page_num=p, text=text,
                            image_path=img_path if has_img else '',
                            ocr_used=ocr_used,
                        ))
                    df.pages_count = n_pages
                    df.status = 'ready'
                except Exception as exc:
                    logger.exception('processing failed for file %s', df.orig_name)
                    df.status = 'error'
                    df.error_text = str(exc)[:1000]
                finally:
                    pkg.files_done += 1
                    db.session.commit()

            pkg.status = 'done'
            db.session.commit()
        except Exception as exc:
            logger.exception('package %s failed', package_id)
            db.session.rollback()
            pkg = db.session.get(DocPackage, package_id)
            if pkg:
                pkg.status = 'error'
                pkg.error_text = str(exc)[:1000]
                db.session.commit()
        finally:
            shutil.rmtree(tmp_extract, ignore_errors=True)
