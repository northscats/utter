# -*- coding: utf-8 -*-
"""Детерминированные проверки гибридной координатной подсветки."""
import json
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix='docs_bbox_test_')
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(_tmp, 'test.db')
os.environ.setdefault('ENCRYPTION_KEY', 'test-only-key-for-docs-bbox')
os.environ.setdefault('SESSION_SECRET', 'test-session')

from PIL import Image, ImageDraw, ImageFont

import doc_analysis
from app import _hash_password, app
from database import (AiProvider, DocCatalog, DocFile, DocFilePage, DocFinding,
                      DocPackage, User, db)
from doc_analysis import (ProviderNotLocalError, _analyze_page_vision, analyze_file,
                          _parse_vision_findings, match_quote_boxes)
from doc_parser import (convert_to_pdf, extract_pdf_pages_data, ocr_image_data,
                        prepare_ocr_page, render_pdf_page)


def check(label, condition, detail=''):
    if not condition:
        raise AssertionError(f'{label}: {detail}')
    print('PASS', label)


app.config.update(TESTING=True, WTF_CSRF_ENABLED=False,
                  DOCS_STORAGE=os.path.join(_tmp, 'docs'))
os.makedirs(app.config['DOCS_STORAGE'], exist_ok=True)

# Цифровой PDF: word-level координаты pdfplumber.
txt_path = os.path.join(_tmp, 'digital.txt')
with open(txt_path, 'w', encoding='utf-8') as fh:
    fh.write('Contract date 28.08.2026')
pdf_path = convert_to_pdf(txt_path, _tmp)
pdf_pages = extract_pdf_pages_data(pdf_path) if pdf_path else []
check('цифровой PDF содержит word-level bbox',
      bool(pdf_pages and pdf_pages[0]['words'])
      and all(len(w['bbox']) == 4 for w in pdf_pages[0]['words']))

# OCR/фото: preprocessing меняет именно финальный PNG, bbox снимаются после него.
photo_path = os.path.join(app.config['DOCS_STORAGE'], 'photo.png')
photo = Image.new('RGB', (1200, 800), 'white')
draw = ImageDraw.Draw(photo)
try:
    font_path = os.popen("fc-match -f '%{file}' 'DejaVu Sans'").read().strip()
    font = ImageFont.truetype(font_path, 64)
except Exception:
    font = ImageFont.load_default()
draw.polygon([(80, 80), (1120, 35), (1160, 730), (45, 760)],
             outline='black', width=8)
draw.text((220, 310), 'DATE 28.08.2026', fill='black', font=font)
photo.save(photo_path)
version = prepare_ocr_page(photo_path, is_photo=True)
ocr = ocr_image_data(photo_path, lang='eng')
check('фото обработано в координатах финального изображения',
      version == 'photo_rectify_v1' and ocr['width'] > 0 and ocr['height'] > 0)
check('OCR возвращает валидные word bbox',
      bool(ocr['words']) and all(
          0 <= w['bbox'][0] <= 1 and 0 <= w['bbox'][1] <= 1
          and w['bbox'][2] > 0 and w['bbox'][3] > 0
          and w['bbox'][0] + w['bbox'][2] <= 1.01
          and w['bbox'][1] + w['bbox'][3] <= 1.01
          for w in ocr['words']))

# Fuzzy quote match: пунктуация/регистр/OCR-подмена не мешают, перенос даёт 2 bbox.
page_stub = type('Page', (), {
    'words_json': json.dumps([
        {'text': 'Дата:', 'bbox': [.10, .10, .08, .03], 'confidence': .96},
        {'text': '28.О8.2026', 'bbox': [.19, .10, .15, .03], 'confidence': .91},
        {'text': 'подписания', 'bbox': [.10, .18, .14, .03], 'confidence': .95},
    ], ensure_ascii=False),
    'ocr_used': True,
})()
boxes, confidence, source = match_quote_boxes(
    page_stub, 'ДАТА 28.08.2026 подписания.')
check('OCR-цитата сопоставлена с несколькими bbox',
      len(boxes) == 2 and confidence >= .68 and source == 'ocr_text',
      (boxes, confidence, source))
wrong_page = type('Page', (), {
    'words_json': json.dumps([
        {'text': 'Дата', 'bbox': [.1, .1, .08, .03], 'confidence': 1},
        {'text': '29.08.2026', 'bbox': [.2, .1, .15, .03], 'confidence': 1},
    ]), 'ocr_used': False,
})()
wrong_boxes, _, _ = match_quote_boxes(wrong_page, 'Дата 28.08.2026')
check('соседняя дата с другой цифрой не получает рамку', wrong_boxes == [])
ambiguous_page = type('Page', (), {
    'words_json': json.dumps([
        {'text': '28.08.2026', 'bbox': [.1, .1, .15, .03], 'confidence': 1},
        {'text': '28.08.2026', 'bbox': [.1, .2, .15, .03], 'confidence': 1},
    ]), 'ocr_used': False,
})()
ambiguous_boxes, _, _ = match_quote_boxes(ambiguous_page, '28.08.2026')
check('неоднозначная цитата остаётся без ложной рамки', ambiguous_boxes == [])
bad_conf_page = type('Page', (), {
    'words_json': json.dumps([
        {'text': '28.08.2026', 'bbox': [.1, .1, .15, .03],
         'confidence': float('nan')},
    ]), 'ocr_used': True,
})()
bad_conf_boxes, bad_confidence, _ = match_quote_boxes(
    bad_conf_page, '28.08.2026')
check('неконечный OCR confidence запрещает точную рамку',
      bad_conf_boxes == [] and bad_confidence is None)

# Vision: высокий confidence рисуется, низкий/выходящий за страницу — карточка без bbox.
parsed = _parse_vision_findings(json.dumps({
    'rules': [], 'findings': [
        {'type': 'seal', 'confidence': .93, 'bbox': [.1, .2, .3, .2],
         'description': 'Чёткий оттиск'},
        {'type': 'signature', 'confidence': .61, 'bbox': [.2, .4, .3, .1]},
        {'type': 'seal', 'confidence': .99, 'bbox': [-.1, 0, 2, 2]},
        {'type': 'seal', 'confidence': float('nan'), 'bbox': [.1, .1, .2, .2]},
        {'type': 'seal', 'confidence': .99, 'bbox': [float('nan'), 0, .2, .2]},
    ]}), 1, ('signature', 'seal'))
check('vision рисует только уверенный валидный bbox',
      len(json.loads(parsed[0]['boxes_json'])) == 1
      and all(x['boxes_json'] == '[]' for x in parsed[1:]))

# Реальная multimodal-форма вызова и локальный барьер.
captured = {}
original_call = doc_analysis.call_ai_provider


def fake_call(**kwargs):
    captured.update(kwargs)
    return {'content': json.dumps({'rules': [], 'findings': [{
        'type': 'seal', 'confidence': .9, 'bbox': [.2, .2, .2, .2],
    }]}), 'usage': {}}


doc_analysis.call_ai_provider = fake_call
provider = type('Provider', (), {
    'is_local': True, 'supports_vision': True,
    'base_url': 'http://127.0.0.1:9/v1', 'model': 'local-vision',
})()
vision_page = type('Page', (), {'page_num': 1, 'image_path': photo_path})()
vision = _analyze_page_vision(vision_page, provider, 'not-a-real-key', ('seal',))
image_url = captured['messages'][1]['content'][1]['image_url']['url']
check('vision получает локальный data URI и structured JSON',
      image_url.startswith('data:image/jpeg;base64,')
      and captured['response_format'] == {'type': 'json_object'}
      and bool(json.loads(vision[0]['boxes_json'])))
external = type('Provider', (), {
    'is_local': False, 'supports_vision': True,
    'base_url': 'https://external.invalid/v1', 'model': 'external',
})()
try:
    _analyze_page_vision(vision_page, external, 'secret', ('seal',))
    blocked = False
except ProviderNotLocalError:
    blocked = True
check('изображение не отправляется внешнему провайдеру', blocked)
doc_analysis.call_ai_provider = original_call

# API: координаты доступны только владельцу; legacy/malformed строки безопасны.
with app.app_context():
    db.drop_all()
    db.create_all()
    owner = User(username='bbox-owner', password_hash=_hash_password('bbox-pass'),
                 role='user')
    stranger = User(username='bbox-stranger', password_hash=_hash_password('bbox-pass'),
                    role='user')
    catalog = DocCatalog(name='BBox')
    db.session.add_all([owner, stranger, catalog])
    db.session.flush()
    package = DocPackage(title='BBox package', uploaded_by=owner.id, status='done')
    db.session.add(package)
    db.session.flush()
    doc_file = DocFile(package_id=package.id, catalog_id=catalog.id,
                       orig_name='sample.pdf', status='ready', pages_count=1)
    db.session.add(doc_file)
    db.session.flush()
    db.session.add(DocFilePage(
        file_id=doc_file.id, page_num=1, text='Date 28.08.2026',
        image_path=photo_path, image_width=ocr['width'], image_height=ocr['height'],
        words_json='[]', coordinate_version='ocr_v1'))
    db.session.add(DocFinding(
        file_id=doc_file.id, page_num=1, ftype='date', snippet='28.08.2026',
        source='ocr_text', boxes_json='[[0.1,0.2,0.3,0.1]]',
        confidence=.9, algorithm_version='hybrid_bbox_v1'))
    db.session.add(DocFinding(
        file_id=doc_file.id, page_num=1, ftype='seal',
        description='Старая находка', boxes_json='broken'))
    db.session.add(DocFinding(
        file_id=doc_file.id, page_num=1, ftype='seal',
        description='Неконечные координаты',
        boxes_json='[[NaN,0.2,0.3,0.1]]'))
    db.session.add(DocFinding(
        file_id=doc_file.id, page_num=1, ftype='signature',
        description='Неконечная уверенность', boxes_json='[]',
        confidence=float('inf')))
    db.session.commit()
    file_id = doc_file.id

client = app.test_client()
client.post('/login', data={'username': 'bbox-owner', 'password': 'bbox-pass'})
detail = client.get(f'/docs/files/{file_id}')
payload = detail.get_json()
check('API владельца возвращает bbox и legacy fallback',
      detail.status_code == 200 and payload['findings'][0]['boxes']
      and payload['findings'][1]['boxes'] == []
      and payload['findings'][2]['boxes'] == []
      and payload['findings'][3]['confidence'] is None)
client.get('/logout')
client.post('/login', data={'username': 'bbox-stranger', 'password': 'bbox-pass'})
check('координаты закрыты от постороннего пользователя',
      client.get(f'/docs/files/{file_id}').status_code == 403)

# Включённый vision не удаляет текстовую карточку при сбое image-вызова.
vision_attempted = False


def fallback_call(**kwargs):
    global vision_attempted
    content = kwargs['messages'][1]['content']
    if isinstance(content, list):
        vision_attempted = True
        raise RuntimeError('vision unavailable')
    return {'content': json.dumps({'rules': [], 'findings': [{
        'type': 'signature', 'page': 1, 'quote': 'Подпись',
        'description': 'Текстовая резервная находка',
    }]}), 'usage': {}}


doc_analysis.call_ai_provider = fallback_call
with app.app_context():
    doc_file = db.session.get(DocFile, file_id)
    result = analyze_file(
        doc_file, provider, 'not-a-real-key',
        find_types=('signature',))
    saved = DocFinding.query.filter_by(
        file_id=file_id, ftype='signature').all()
check('сбой vision сохраняет текстовую карточку без рамки',
      vision_attempted and len(result['findings']) == 1
      and result['findings'][0]['source'] == ''
      and result['findings'][0]['boxes'] == []
      and len(saved) == 1 and saved[0].boxes_json == '[]')
doc_analysis.call_ai_provider = original_call

print('ВСЕ BBOX-ТЕСТЫ ПРОЙДЕНЫ')