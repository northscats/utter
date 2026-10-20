import "./_group.css";
import type { ReactNode } from "react";

const stats = [
  ["Загрузок", "4", "за выбранный период"], ["Сумма долга", "18,42 млн ₽", "значение из строк источника"],
  ["Строк данных", "1 236", "учтено в выборке"], ["Покрытие", "89,6%", "показатель API"],
];
const records = [
  ["АО «Теплосеть Балашиха»","5001023456 · 500101001","Балашиха","03.2026 · Тепло · лист 1 / №18","2 480 300,00 ₽","Не сопоставлено"],
  ["МУП «Водоканал»","5002034567 · 500201001","Подольск","03.2026 · Вода · лист 2 / №46","1 184 720,50 ₽","12 400,00 ₽ · 02.2026"],
  ["ООО «Энергоресурс»","5003045678 · 500301001","Химки","03.2026 · Электроэнергия · лист 1 / №9","845 000,00 ₽","Не сопоставлено"],
  ["МУП «Коммунальные сети»","5004056789 · 500401001","Коломна","03.2026 · Тепло · лист 3 / №67","692 180,00 ₽","Сравнение недоступно"],
];

export function Updated() {
  return <div className="ministry-mockup updated">
    <header className="topbar"><div className="brand"><span className="brand-mark">М</span><span>ГИС УЗТЭР</span><span className="brand-divider"/><span className="brand-sub">Кабинет министерства</span></div><div className="top-spacer"/><button className="user-toggle"><span className="avatar">М</span><span>operator</span>⌄</button></header>
    <main>
      <div className="page-head"><div><div className="eyebrow">Проверка источников <span> / </span> 2026</div><h1>Долговые отчёты поставщиков</h1><p className="intro">Сводка переданных форм ТЭР и переход к исходным строкам.</p></div><div className="actions"><button className="button">Настроить вид</button><button className="button primary">Загрузить отчёт</button></div></div>
      <div className="notice"><span className="notice-mark">i</span><span><strong>Только подтверждённые данные.</strong> Областной итог не подтверждён: состав источников и пересечения не сверены. Пустые значения не считаются нулём; границы не дорисовываются.</span></div>
      <div className="workspace">
        <section className="left-column" aria-label="Обзор и территориальные данные">
          <div className="dash-title"><span>ОБЗОР ПЕРИОДА</span><b>Март 2026</b></div>
          <div className="summary-grid">{stats.map(([label,value,note])=><article className="metric" key={label}><div className="metric-label">{label}</div><div className="metric-value">{value}</div><div className="metric-note">{note}</div></article>)}</div>
          <Panel title="Динамика по месяцам" caption="Долг из доступных строк источников"><div className="chart">{[["10.2025","12,4 млн",48],["11.2025","14,8 млн",58],["12.2025","11,2 млн",44],["01.2026","16,9 млн",67],["02.2026","15,1 млн",60],["03.2026","18,4 млн",78]].map(([label,val,height],i)=><div className="chart-item" key={String(label)}><span className="chart-value">{val}</span><div className="chart-bar" style={{height:`${height}%`,background:i===5?"#315f9b":undefined}}/><span className="chart-label">{label}</span></div>)}</div><div className="chart-foot">Суммы из доступных отчётов; итог области не подтверждён.</div></Panel>
          <Panel title="Территориальный обзор" caption="Покрытие по данным API"><div className="map-unavailable"><div className="map-message"><div className="map-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M3 6.5 8.5 4l7 2.5L21 4v13.5L15.5 20l-7-2.5L3 20zM8.5 4v13.5M15.5 6.5V20"/></svg></div><strong>Карта пока недоступна</strong><p>Подтверждённые границы муниципальных образований не переданы. Территории на карте не отображаются.</p></div></div><div className="map-note"><span className="map-marker" />Нет проверенной геометрии <span className="map-sep">·</span> данные по территории доступны в таблице</div></Panel>
          <div className="quality-note"><span className="quality-mark">!</span><span><b>Качество источников</b><br/>ИНН отсутствует — 12 · повторяющиеся ИНН — 8 · групповых строк — 17 · предупреждения — 6</span></div>
        </section>
        <section className="right-column" aria-label="Фильтры и журнал отчётов">
          <section className="filters-panel">
            <div className="filters-heading"><div><span className="section-kicker">ВЫБОРКА</span><h2>Фильтры отчётов</h2></div><button className="button">Сбросить</button></div>
            <div className="filters">
              <Field label="Отчётный месяц" value="Март 2026"/><Field label="Ресурс" value="Все ресурсы"/><Field label="Территория" value="Все территории"/><Field label="Поиск по организации, ИНН, файлу" value="Название или ИНН"/>
            </div>
          </section>
          <Panel title="Строки отчётов" caption="Выберите строку, чтобы открыть реквизиты и происхождение"><div className="table-tools"><span>1 236 строк</span><span className="table-tip">Сортировка по долгу: по убыванию</span></div><div className="table-scroll"><table><thead><tr><th>Организация</th><th>ИНН / КПП</th><th>Территория</th><th>Период / источник</th><th>Долг</th><th>Сопоставление</th></tr></thead><tbody>{records.map((r,i)=><tr key={r[0]} tabIndex={0}><td><div className="company-cell">{r[0]}</div><div className="subcell">ООО «Поставщик ТЭР»</div></td><td><div className="mono">{r[1]}</div></td><td>{r[2]}</td><td><div>{r[3].split(" · ").slice(0,2).join(" · ")}</div><div className="subcell">{r[3].split(" · ").slice(2).join(" · ")}</div></td><td><div className="amount">{r[4]}</div><div className="subcell">за период</div></td><td><span className={`badge ${i===1?"good":""}`}>{r[5]}</span></td></tr>)}</tbody></table></div><div className="pagination"><span>Показаны 1–50 из 1 236</span><div className="page-controls"><button disabled>‹</button><span>1 / 25</span><button>›</button></div></div></Panel>
          <Panel title="Последние загрузки" caption="Файлы и версии как зарегистрированы"><div className="imports-list"><div className="import-item"><div><div className="import-file">Отчёт_теплосеть_март_2026.xlsx</div><div className="import-meta">Владелец данных: Теплосеть Балашиха · загрузил: operator</div><div className="import-meta">Март 2026 · Тепло · версия 2 · 18.03.2026, 14:32</div></div><span className="import-count">284 строки</span></div><div className="import-item"><div><div className="import-file">Долги_водоканал_03-2026.xlsx</div><div className="import-meta">Владелец данных: Водоканал · загрузил: analyst</div><div className="import-meta">Март 2026 · Вода · версия 1 · 17.03.2026, 10:06</div></div><span className="import-count">512 строк</span></div></div></Panel>
        </section>
      </div>
    </main>
  </div>;
}

function Field({label,value}:{label:string;value:string}) { return <div className={`field ${label.startsWith("Поиск")?"search-field":""}`}><label>{label}</label><input value={value} readOnly /></div>; }
function Panel({title,caption,children}:{title:string;caption:string;children:ReactNode}) { return <section className="panel"><div className="panel-head"><div><h2 className="panel-title">{title}</h2><div className="panel-caption">{caption}</div></div></div>{children}</section>; }
