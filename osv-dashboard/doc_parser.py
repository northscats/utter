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

try:
    import cv2
    import numpy as np
    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False

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


def extract_pdf_pages_data(pdf_path: str) -> list[dict]:
    """Текст и word-level bbox цифрового PDF в нормализованных координатах."""
    if not HAS_PDFPLUMBER:
        return []
    pages = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                width, height = float(page.width or 0), float(page.height or 0)
                words = []
                if width > 0 and height > 0:
                    for word in page.extract_words(use_text_flow=True, keep_blank_chars=False):
                        text = str(word.get('text') or '').strip()
                        if not text:
                            continue
                        x0 = max(0.0, min(width, float(word.get('x0') or 0)))
                        x1 = max(x0, min(width, float(word.get('x1') or x0)))
                        top = max(0.0, min(height, float(word.get('top') or 0)))
                        bottom = max(top, min(height, float(word.get('bottom') or top)))
                        words.append({
                            'text': text,
                            'bbox': [x0 / width, top / height,
                                     (x1 - x0) / width, (bottom - top) / height],
                            'confidence': 1.0,
                        })
                pages.append({
                    'text': (page.extract_text() or '').strip(),
                    'words': words,
                    'width': width,
                    'height': height,
                })
    except Exception as exc:
        logger.warning('pdfplumber failed for %s: %s', pdf_path, exc)
    return pages


def extract_pdf_pages_text(pdf_path: str) -> list[str]:
    """Обратная совместимость для старых callers."""
    return [p['text'] for p in extract_pdf_pages_data(pdf_path)]


def _order_quad(points):
    result = np.zeros((4, 2), dtype='float32')
    sums = points.sum(axis=1)
    diffs = np.diff(points, axis=1).reshape(-1)
    result[0], result[2] = points[np.argmin(sums)], points[np.argmax(sums)]
    result[1], result[3] = points[np.argmin(diffs)], points[np.argmax(diffs)]
    return result


def _rectify_photo(image):
    """Выравнивает крупнейший похожий на лист четырёхугольник."""
    height, width = image.shape[:2]
    scale = min(1.0, 1400.0 / max(height, width))
    small = cv2.resize(image, None, fx=scale, fy=scale) if scale < 1 else image.copy()
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 60, 180)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    image_area = small.shape[0] * small.shape[1]
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:8]:
        perimeter = cv2.arcLength(contour, True)
        quad = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(quad) != 4 or cv2.contourArea(quad) < image_area * 0.30:
            continue
        points = _order_quad(quad.reshape(4, 2).astype('float32') / scale)
        tl, tr, br, bl = points
        out_w = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
        out_h = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
        if out_w < 200 or out_h < 200:
            break
        target = np.array([[0, 0], [out_w - 1, 0],
                           [out_w - 1, out_h - 1], [0, out_h - 1]],
                          dtype='float32')
        return cv2.warpPerspective(
            image, cv2.getPerspectiveTransform(points, target), (out_w, out_h))
    return image


def prepare_ocr_page(image_path: str, is_photo: bool = False) -> str:
    """Best-effort ориентация/геометрия/контраст с записью финального PNG.

    Координаты OCR затем снимаются уже с этого же изображения, поэтому не
    требуют обратного преобразования в браузере.
    """
    if not HAS_OPENCV:
        return 'ocr_v1'
    try:
        image = cv2.imread(image_path, cv2.IMREAD_COLOR)
        if image is None:
            return 'ocr_v1'
        if HAS_OCR:
            try:
                osd = pytesseract.image_to_osd(
                    cv2.cvtColor(image, cv2.COLOR_BGR2RGB),
                    output_type=pytesseract.Output.DICT)
                rotation = int(osd.get('rotate') or 0) % 360
                if rotation == 90:
                    image = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
                elif rotation == 180:
                    image = cv2.rotate(image, cv2.ROTATE_180)
                elif rotation == 270:
                    image = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
            except Exception:
                pass
        version = 'ocr_v1'
        if is_photo:
            image = _rectify_photo(image)
            version = 'photo_rectify_v1'
        # Выравнивание освещённости без уничтожения цвета печатей.
        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        l_chan, a_chan, b_chan = cv2.split(lab)
        l_chan = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l_chan)
        image = cv2.cvtColor(cv2.merge((l_chan, a_chan, b_chan)), cv2.COLOR_LAB2BGR)
        if not cv2.imwrite(image_path, image):
            return 'ocr_v1'
        return version
    except Exception as exc:
        logger.warning('OCR page preprocessing failed: %s', exc)
        return 'ocr_v1'


def ocr_image_data(image_path: str, lang: str = 'rus+eng') -> dict:
    """OCR с word-level bbox относительно финального изображения."""
    if HAS_OCR:
        try:
            with Image.open(image_path) as im:
                im = im.convert('RGB')
                width, height = im.size
                raw = pytesseract.image_to_data(
                    im, lang=lang, output_type=pytesseract.Output.DICT)
            words, lines = [], {}
            for i, raw_text in enumerate(raw.get('text') or []):
                text = str(raw_text or '').strip()
                try:
                    confidence = float(raw['conf'][i])
                except (TypeError, ValueError, IndexError):
                    confidence = -1
                if not text or confidence < 0 or width <= 0 or height <= 0:
                    continue
                left, top = int(raw['left'][i]), int(raw['top'][i])
                box_w, box_h = int(raw['width'][i]), int(raw['height'][i])
                words.append({
                    'text': text,
                    'bbox': [max(0, left) / width, max(0, top) / height,
                             max(0, box_w) / width, max(0, box_h) / height],
                    'confidence': round(max(0.0, min(100.0, confidence)) / 100.0, 3),
                })
                key = (raw.get('block_num', [0])[i], raw.get('par_num', [0])[i],
                       raw.get('line_num', [0])[i])
                lines.setdefault(key, []).append(text)
            return {
                'text': '\n'.join(' '.join(line) for line in lines.values()).strip(),
                'words': words, 'width': width, 'height': height,
            }
        except Exception as exc:
            logger.warning('pytesseract data failed: %s', exc)
    text = ocr_image_file(image_path, lang=lang)
    try:
        with Image.open(image_path) as im:
            width, height = im.size
    except Exception:
        width = height = 0
    return {'text': text, 'words': [], 'width': width, 'height': height}


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
