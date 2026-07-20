# -*- coding: utf-8 -*-
"""Веб-прототип: загрузка ОСВ (.xls/.xlsx) -> аудит -> HTML-дашборд."""
import json
import os
import time
import traceback

from flask import Flask, request, render_template, send_from_directory, abort

from osvparser import parse_many
from builder import build_dataset

BASE = os.path.dirname(os.path.abspath(__file__))
UPLOADS = os.path.join(BASE, 'uploads')
OUTPUT = os.path.join(BASE, 'output')
os.makedirs(UPLOADS, exist_ok=True)
os.makedirs(OUTPUT, exist_ok=True)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 МБ на запрос


def render_dashboard(dataset):
    tpl_path = os.path.join(BASE, 'templates', 'dashboard_template.html')
    with open(tpl_path, encoding='utf-8') as f:
        tpl = f.read()
    return tpl.replace('/*__DATA__*/',
                       json.dumps(dataset, ensure_ascii=False), 1)


@app.route('/')
def index():
    ready = sorted(
        (f for f in os.listdir(OUTPUT) if f.endswith('.html')), reverse=True)
    return render_template('upload.html', ready=ready, audit=None, error=None)


@app.route('/process', methods=['POST'])
def process():
    files = [f for f in request.files.getlist('files') if f.filename]
    if not files:
        return render_template('upload.html', ready=[], audit=None,
                               error='Файлы не выбраны.')
    ts = time.strftime('%Y%m%d_%H%M%S')
    batch_dir = os.path.join(UPLOADS, ts)
    os.makedirs(batch_dir, exist_ok=True)
    paths = []
    for f in files:
        name = os.path.basename(f.filename)
        if not name.lower().endswith(('.xls', '.xlsx')):
            continue
        p = os.path.join(batch_dir, name)
        f.save(p)
        paths.append(p)
    if not paths:
        return render_template('upload.html', ready=[], audit=None,
                               error='Среди загруженного нет .xls/.xlsx файлов.')
    try:
        data, audit = parse_many(paths)
        if not data:
            return render_template('upload.html', ready=[], audit=audit,
                                   error='Ни один файл не удалось разобрать '
                                         'как ОСВ 1С.')
        dataset = build_dataset(data)
        html = render_dashboard(dataset)
        out_name = f'dashboard_{ts}.html'
        with open(os.path.join(OUTPUT, out_name), 'w', encoding='utf-8') as f:
            f.write(html)
        # какие счета получены / чего не хватает для полного дашборда
        want = ['20', '25', '26', '50', '51', '60', '62', '68', '69',
                '70', '76', '90', '91']
        missing = [a for a in want if a not in data]
        if missing:
            audit.append(dict(file='—', acc=', '.join(missing),
                              status='счета не загружены — соответствующие '
                                     'блоки дашборда будут пустыми',
                              ok=False))
        return render_template('upload.html', ready=[], audit=audit,
                               error=None, dashboard=out_name,
                               period=dataset['period_label'])
    except Exception as e:  # noqa: BLE001 — прототип
        traceback.print_exc()
        return render_template('upload.html', ready=[], audit=None,
                               error=f'Ошибка обработки: {e}')


@app.route('/dashboard/<name>')
def dashboard(name):
    name = os.path.basename(name)
    if not name.endswith('.html'):
        abort(404)
    return send_from_directory(OUTPUT, name)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)),
            debug=False)
