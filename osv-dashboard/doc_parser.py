# -*- coding: utf-8 -*-
"""Парсер документов для универсальной проверки: PDF / DOCX / XLSX / изображения.

Адаптировано из MBT-сервиса (python_server/document_parser.py) под стек ГИС УЗТЭР.

ИБ-требования:
  * Все subprocess-вызовы — только списком аргументов (shell=False).
  * Имена файлов санитизируются перед любым использованием в путях.
  * Таймауты на все внешние процессы (LibreOffice, pdftoppm, tesseract).
"""
import logging
import os
import re
import subprocess
import tempfile
import unicodedata

logger = logging.getLogger(__name__)

try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

try:
    import pytesseract
    from PIL import Image
    HAS_OCR = True
except ImportError:
    HAS_OCR = False

# Расширения, которые умеем обрабатывать
CONVERTIBLE_EXTS = {'.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.odt', '.ods', '.rtf', '.txt'}
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp'}
SUPPORTED_EXTS = {'.pdf'} | CONVERTIBLE_EXTS | IMAGE_EXTS


def sanitize_filename(name: str) -> str:
    """Безопасное имя файла: без путей, спецсимволов и управляющих символов."""
    name = os.path.basename(name.replace('\\', '/'))
    name = unicodedata.normalize('NFC', name)
    # Оставляем буквы (включая кириллицу), цифры, точку, дефис, подчёркивание, пробел
    name = re.sub(r'[^\w.\- ()]', '_', name, flags=re.UNICODE)
    name = name.strip(' .')
    return name[:200] or 'file'


def convert_to_pdf(src_path: str, out_dir: str, timeout: int = 120) -> str | None:
    """Конвертирует офисный документ в PDF через LibreOffice (shell=False).

    Возвращает путь к PDF или None при ошибке.
    """
    os.makedirs(out_dir, exist_ok=True)
    # Отдельный профиль LibreOffice, чтобы параллельные вызовы не конфликтовали
    profile_dir = tempfile.mkdtemp(prefix='lo_profile_')
    try:
        cmd = [
            'soffice', '--headless', '--norestore',
            f'-env:UserInstallation=file://{profile_dir}',
            '--convert-to', 'pdf', '--outdir', out_dir, src_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            logger.warning('LibreOffice convert failed: %s', result.stderr[:300])
            return None
        base = os.path.splitext(os.path.basename(src_path))[0]
        pdf_path = os.path.join(out_dir, base + '.pdf')
        return pdf_path if os.path.isfile(pdf_path) else None
    except subprocess.TimeoutExpired:
        logger.warning('LibreOffice convert timeout: %s', src_path)
        return None
    finally:
        import shutil
        shutil.rmtree(profile_dir, ignore_errors=True)


def image_to_pdf(src_path: str, out_dir: str) -> str | None:
    """Оборачивает изображение в одностраничный PDF (Pillow)."""
    try:
        from PIL import Image as _Img
        os.makedirs(out_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(src_path))[0]
        pdf_path = os.path.join(out_dir, base + '.pdf')
        with _Img.open(src_path) as im:
            if im.mode in ('RGBA', 'P', 'LA'):
                im = im.convert('RGB')
            im.save(pdf_path, 'PDF')
        return pdf_path
    except Exception as exc:
        logger.warning('image_to_pdf failed for %s: %s', src_path, exc)
        return None


def get_pdf_page_count(pdf_path: str) -> int:
    """Количество страниц PDF через pdfinfo (poppler)."""
    try:
        result = subprocess.run(['pdfinfo', pdf_path], capture_output=True,
                                text=True, timeout=15)
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith('Pages:'):
                    return int(line.split(':', 1)[1].strip())
    except Exception as exc:
        logger.warning('pdfinfo failed: %s', exc)
    return 0


def render_pdf_page(pdf_path: str, page_num: int, out_path: str, dpi: int = 150) -> bool:
    """Рендерит страницу PDF в PNG через pdftoppm. page_num — 1-based."""
    try:
        prefix = out_path[:-4] if out_path.endswith('.png') else out_path
        cmd = ['pdftoppm', '-png', '-r', str(dpi),
               '-f', str(page_num), '-l', str(page_num),
               '-singlefile', pdf_path, prefix]
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        return result.returncode == 0 and os.path.isfile(prefix + '.png')
    except Exception as exc:
        logger.warning('pdftoppm failed: %s', exc)
        return False


def extract_pdf_pages_text(pdf_path: str) -> list[str]:
    """Текст каждой страницы PDF через pdfplumber. Возвращает список строк."""
    if not HAS_PDFPLUMBER:
        return []
    texts = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                texts.append((page.extract_text() or '').strip())
    except Exception as exc:
        logger.warning('pdfplumber failed for %s: %s', pdf_path, exc)
    return texts


def ocr_image_file(image_path: str, lang: str = 'rus+eng') -> str:
    """OCR изображения. Сначала pytesseract, затем subprocess-fallback."""
    if HAS_OCR:
        try:
            with Image.open(image_path) as im:
                return pytesseract.image_to_string(im, lang=lang).strip()
        except Exception as exc:
            logger.warning('pytesseract failed: %s', exc)
    # Fallback: tesseract CLI (shell=False)
    try:
        result = subprocess.run(
            ['tesseract', image_path, 'stdout', '-l', lang],
            capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as exc:
        logger.warning('tesseract CLI failed: %s', exc)
    return ''


def prepare_pdf(src_path: str, out_dir: str) -> str | None:
    """Приводит любой поддерживаемый файл к PDF. Возвращает путь к PDF или None."""
    ext = os.path.splitext(src_path)[1].lower()
    if ext == '.pdf':
        return src_path
    if ext in IMAGE_EXTS:
        return image_to_pdf(src_path, out_dir)
    if ext in CONVERTIBLE_EXTS:
        return convert_to_pdf(src_path, out_dir)
    return None
