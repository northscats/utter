# -*- coding: utf-8 -*-
"""CLI: python cli.py <файлы или папка с ОСВ> [-o dashboard.html]"""
import glob, json, os, sys
from osvparser import parse_many
from builder import build_dataset

def main():
    args = [a for a in sys.argv[1:] if a != '-o' and not a.endswith('.html')]
    out = 'dashboard.html'
    if '-o' in sys.argv:
        out = sys.argv[sys.argv.index('-o') + 1]
    paths = []
    for a in args:
        if os.path.isdir(a):
            paths += glob.glob(os.path.join(a, '*.xls*'))
        else:
            paths += glob.glob(a)
    if not paths:
        print('Укажите .xls/.xlsx файлы или папку с ними'); sys.exit(1)
    data, audit = parse_many(paths)
    for a in audit:
        print(('[OK] ' if a['ok'] else '[!!] ') + a['file'] + ' -> ' + a['status'])
    ds = build_dataset(data)
    tpl = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
               'templates', 'dashboard_template.html'), encoding='utf-8').read()
    open(out, 'w', encoding='utf-8').write(
        tpl.replace('/*__DATA__*/', json.dumps(ds, ensure_ascii=False), 1))
    print('Дашборд:', out, '| период:', ds['period_label'])

if __name__ == '__main__':
    main()
