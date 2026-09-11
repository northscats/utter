# -*- coding: utf-8 -*-
"""SQLite route regression for response-only legacy dashboard upgrades."""
import os
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


_TMP = tempfile.TemporaryDirectory(prefix='dashboard-legacy-route-')
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(_TMP.name, 'test.db')
os.environ['ENCRYPTION_KEY'] = (
    'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module
from dashboard_compat import (json_for_script,
                               upgrade_legacy_salary_daily_labels)
from database import Company, User, db


LEGACY_DASHBOARD = r'''<!doctype html>
<a class="hd-back" href="/">☰ Все предприятия</a>
<script>
const D={"company_name":"Начислено (Дт) injected / Выплачено (Кт) injected","zp":{"n":[111],"v":[222]},"daily_charts":{"accounts":{"70":{"obd":[10],"obk":[20]}}},"embedded_marker":"Начислено (Дт) injected / Выплачено (Кт) injected"};

function renderDailyExec(){
  const DC=D.daily_charts;
  const dates=DC.dates||[];
  const accs=DC.accounts||{};
  const el70=document.getElementById('ch-daily-70');
  if(accs['70']){
    dailyBarsPair(el70, dates, accs['70'].obd, accs['70'].obk, 'Начислено', 'Выплачено', '#2e6e8e','#7d97a8');
  }
}

/* ---------- рендер суточных в детальной вкладке ---------- */
function renderDailyDetail(){
  const DC=D.daily_charts;
  const dates=DC.dates||[];
  const accs=DC.accounts||{};
  const grid=document.getElementById('daily-tabs-grid');
  if(!grid) return;
  grid.innerHTML='';
  if(!dates.length) return;
  const rows=[];
  rows.forEach(([acc,title,mode,c1,c2])=>{
    const ad=accs[acc]; if(!ad) return;
    const cw=document.createElement('div');cw.className='chartwrap';
    if(mode==='line'){
      const v=ad.balance;
      if(v)dailyLine(cw,dates,[{n:acc,c:c1,v}]);
      else dailyBarsPair(cw,dates,ad.obd,ad.obk,'Дт','Кт',c1,c2||'#a05a5a');
    }else if(mode==='bars'){
      dailyBarsPair(cw,dates,ad.obd,ad.obk,'Дт','Кт',c1,c2||'#a05a5a');
    }else{
      // bars_single — только Дт
      dailyBarsPair(cw,dates,ad.obd,[].map(()=>0),'Дт','',c1,c1);
    }
  });
}

/* ---------- главный рендер ---------- */
function openDailyModal(acc){
  var DC=D.daily_charts,dates=DC.dates||[],accs=DC.accounts||{};
  var titles={'51':'Расчётный счёт 51 — суточные обороты','70':'Оплата труда 70 — суточные обороты'};
  var legs={
    '51':'<span><i style="background:#4d8f6f"></i>Поступления (Дт)</span><span><i style="background:#a05a5a"></i>Платежи (Кт)</span>',
    '70':'<span><i style="background:#2e6e8e"></i>Начислено (Дт)</span><span><i style="background:#7d97a8"></i>Выплачено (Кт)</span>'
  };
}

function redraw(){
  var s0=0,s1=1,dates=[],accs=D.daily_charts.accounts||{},box=document.body;
  else if(_dmAcc==='70'&&accs['70'])
    dailyBarsPair(box,dates,accs['70'].obd.slice(s0,s1+1),accs['70'].obk.slice(s0,s1+1),
      'Начислено','Выплачено','#2e6e8e','#7d97a8');
}

function drawMonthlySalary(){
  barsLine('ch-zp',
    [{n:'Начислено (Кт 70)',c:'#2e6e8e',v:D.zp.n},
     {n:'Выплачено и удержано (Дт 70)',c:'#7d97a8',v:D.zp.v}],null,{ghost:true});
}

<div class="daily-card" id="daily-70-wrap">
  <h3>Оплата труда 70 — обороты</h3>
  <div class="chartwrap" id="ch-daily-70"></div>
  <div class="daily-legend">
    <span><i style="background:#2e6e8e"></i>Начислено (Дт)</span>
    <span><i style="background:#7d97a8"></i>Выплачено (Кт)</span>
  </div>
</div>
</script>'''


class DashboardLegacyRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_module.app
        cls.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        cls.imap_patch = patch.object(app_module, '_ensure_imap_thread')
        cls.imap_patch.start()
        cls.output_dir = Path(_TMP.name) / 'output'
        cls.output_dir.mkdir()
        cls.previous_output = app_module.OUTPUT
        app_module.OUTPUT = str(cls.output_dir)
        with cls.app.app_context():
            db.create_all()
            admin = User(
                username='dashboard_legacy_admin',
                password_hash='unused',
                role='admin',
            )
            db.session.add(admin)
            db.session.flush()
            company = Company(
                name='Legacy dashboard company',
                created_by=admin.id,
            )
            db.session.add(company)
            db.session.commit()
            cls.admin_id = admin.id
            cls.company_id = company.id
        cls.dashboard_path = (
            cls.output_dir / str(cls.company_id) / 'dashboard_current.html')
        cls.dashboard_path.parent.mkdir()
        cls.dashboard_path.write_text(LEGACY_DASHBOARD, encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        app_module.OUTPUT = cls.previous_output
        with cls.app.app_context():
            db.session.remove()
            db.drop_all()
        cls.imap_patch.stop()
        _TMP.cleanup()

    def test_new_dashboard_has_no_company_back_button(self):
        html = app_module.render_dashboard({})
        self.assertNotIn('☰ Все предприятия', html)
        self.assertNotIn('hd-back', html)

    def _client(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = str(self.admin_id)
            session['_fresh'] = True
        return client

    def test_authenticated_route_upgrades_response_without_writing_file(self):
        path_before = self.dashboard_path.read_bytes()
        client = self.app.test_client()
        self.assertEqual(
            client.get(
                f'/dashboard/{self.company_id}/dashboard_current.html'
            ).status_code,
            302,
        )

        response = self._client().get(
            f'/dashboard/{self.company_id}/dashboard_current.html')
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertNotIn('☰ Все предприятия', body)
        self.assertNotIn('<a class="hd-back"', body)

        self.assertIn(
            '<span><i style="background:#2e6e8e"></i>'
            'ОбД70 — дебетовый оборот</span>',
            body,
        )
        self.assertIn(
            '<span><i style="background:#7d97a8"></i>'
            'ОбК70 — начисления (Кт)</span>',
            body,
        )
        self.assertIn('daily-inference', body)
        self.assertIn(
            'ОбД70 может включать выплаты, удержания и корректировки; '
            'общий оборот не равен денежной выплате.',
            body,
        )
        self.assertIn(
            "accs['70'].obd, accs['70'].obk, 'ОбД70', 'ОбК70', "
            "'#2e6e8e','#7d97a8'",
            body,
        )
        self.assertIn(
            "'ОбД70','ОбК70','#2e6e8e','#7d97a8'",
            body,
        )
        self.assertIn(
            "const nIn=acc==='70'?'ОбД70':'Дт',"
            "nOut=acc==='70'?'ОбК70':'Кт';",
            body,
        )

        old_daily_markup = (
            '<span><i style="background:#2e6e8e"></i>Начислено (Дт)</span>')
        self.assertNotIn(old_daily_markup, body)
        self.assertNotIn(
            "'Начислено','Выплачено','#2e6e8e','#7d97a8'", body)
        self.assertNotIn(
            "Начислено (Дт)</span><span><i "
            'style="background:#7d97a8"></i>Выплачено (Кт)</span>',
            body,
        )

        embedded = (
            '"company_name":"Начислено (Дт) injected / '
            'Выплачено (Кт) injected"')
        self.assertIn(embedded, body)
        self.assertIn(
            '"embedded_marker":"Начислено (Дт) injected / '
            'Выплачено (Кт) injected"',
            body,
        )
        self.assertIn('v:D.zp.n', body)
        self.assertIn('v:D.zp.v', body)
        self.assertIn("Начислено (Кт 70)", body)
        self.assertIn("Выплачено и удержано (Дт 70)", body)

        self.assertEqual(path_before, self.dashboard_path.read_bytes())
        self.assertEqual(upgrade_legacy_salary_daily_labels(body), body)

    def test_authenticated_route_rebinds_known_saved_template_without_rebuilding_data(self):
        current_template = Path(
            app_module.BASE, 'templates', 'dashboard_template.html'
        ).read_text(encoding='utf-8')
        saved = current_template.replace(
            '/*__DATA__*/',
            json.dumps({
                'company_name': 'Saved snapshot',
                'embedded_marker': 'saved-only-marker',
                'daily_charts': {
                    'dates': ['2026-05-01', '2026-05-02'],
                    'accounts': {
                        '51': {'obd': [100, 0], 'obk': [20, None]},
                        '70': {'obd': [30, 40], 'obk': [50, 60]},
                    },
                },
                'months': [], 'month_names': [], 'month_short': [],
                'closing_label': [], 'ghost': [],
            }, ensure_ascii=False),
            1,
        )
        original = self.dashboard_path.read_text(encoding='utf-8')
        self.dashboard_path.write_text(saved, encoding='utf-8')
        try:
            response = self._client().get(
                f'/dashboard/{self.company_id}/dashboard_current.html')
            self.assertEqual(response.status_code, 200)
            body = response.get_data(as_text=True)
            self.assertIn('const DAILY = D.daily_charts', body)
            self.assertIn('"embedded_marker": "saved-only-marker"', body)
            self.assertIn('"legacy_unknown": true', body)
            self.assertIn('"2026-05-01"', body)
            self.assertIn('Суточные данные недоступны', body)
        finally:
            self.dashboard_path.write_text(original, encoding='utf-8')

    def test_inline_dataset_json_escapes_script_breakout_characters(self):
        value = {'marker': '</script><script>alert(1)</script>&\u2028\u2029'}
        encoded = json_for_script(value)
        self.assertNotIn('</script>', encoded)
        self.assertIn(r'\u003C/script\u003E', encoded)
        self.assertIn(r'\u0026', encoded)
        self.assertIn(r'\u2028\u2029', encoded)

        rendered = app_module.render_dashboard(value)
        self.assertNotIn('</script><script>', rendered)
        self.assertIn(r'\u003C/script\u003E', rendered)


if __name__ == '__main__':
    unittest.main()
