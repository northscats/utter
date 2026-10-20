import "./_group.css";
import type { ReactNode } from "react";

const metrics = [
  ["Загрузок", "4", "за выбранный период"], ["Строк в источниках", "1 284", "как передано поставщиками"],
  ["Строк данных", "1 236", "учтено в выборке"], ["Совпавших долгов", "1 108", "значение из источника"],
  ["Сумма долга", "18 420 550 ₽", "без пересчёта"], ["ИНН отсутствует", "12", "в строках данных"],
  ["Повторяющиеся ИНН", "8", "требуют проверки"], ["Групповых строк", "17", "помечено в источнике"],
  ["Покрытие", "89,6%", "показатель API"], ["Предупреждения", "6", "по загруженным файлам"],
];
const rows = [
  ["АО «Теплосеть Балашиха»", "ИНН 5001023456 · КПП 500101001", "Балашиха", "03.2026 · Тепло · лист 1 / №18", "2 480 300,00 ₽", "Нет доступного сравнения"],
  ["МУП «Водоканал»", "ИНН 5002034567 · КПП 500201001", "Подольск", "03.2026 · Вода · лист 2 / №46", "1 184 720,50 ₽", "12 400,00 ₽ · предыдущий период"],
  ["ООО «Энергоресурс»", "ИНН 5003045678 · КПП 500301001", "Химки", "03.2026 · Электроэнергия · лист 1 / №9", "845 000,00 ₽", "Нет доступного сравнения"],
];

export function Current() {
  return <div className="ministry-mockup">
    <header className="topbar"><div className="brand"><span className="brand-mark">М</span><span>ГИС УЗТЭР</span><span className="brand-divider" /><span className="brand-sub">Кабинет министерства</span></div><div className="top-spacer" /><button className="user-toggle"><span className="avatar">М</span><span>operator</span>⌄</button></header>
    <main>
      <div className="page-head"><div><div className="eyebrow">Рабочая область · проверка источников</div><h1>Долговые отчёты поставщиков</h1><p className="intro">Сводка по формам поставщиков ТЭР с переходом к исходным строкам и файлам.</p></div><div className="actions"><button className="button">Настроить вид</button><button className="button primary">Загрузить отчёт</button></div></div>
      <div className="notice"><span><strong>Только подтверждённые данные.</strong> Показатели берутся из строк переданных отчётов. Областной итог не подтверждён: состав и пересечения источников не сверены. Пустые значения не считаются нулём, границы не дорисовываются.</span></div>
      <section className="filters"><Field label="Отчётный месяц" value="Март 2026" /><Field label="Ресурс" value="Все ресурсы" /><Field label="Территория" value="Все территории" /><Field label="Поиск по организации, ИНН, файлу" value="Название или ИНН" /><button className="button">Сбросить</button></section>
      <section className="summary-grid">{metrics.map(([label, value, note]) => <article className="metric" key={label}><div className="metric-label">{label}</div><div className="metric-value">{value}</div><div className="metric-note">{note}</div></article>)}</section>
      <div className="workspace">
        <div className="left-column">
          <Panel title="Динамика по месяцам" caption="Сумма строк из доступных отчётов; итог области не подтверждён"><div className="chart">{[["10.2025","12,4 млн",48],["11.2025","14,8 млн",58],["12.2025","11,2 млн",44],["01.2026","16,9 млн",67],["02.2026","15,1 млн",60],["03.2026","18,4 млн",78]].map(([label,val,height],i)=><div className="chart-item" key={String(label)}><span className="chart-value">{val}</span><div className="chart-bar" style={{height:`${height}%`,background:i===5?"#b67b2c":undefined}}/><span className="chart-label">{label}</span></div>)}</div></Panel>
          <Panel title="Территориальный обзор" caption="Карта отображается только при наличии проверенных границ"><div className="map-unavailable"><div className="map-message"><div className="map-icon">◇</div><strong>Геометрия недоступна</strong><p>Территориальные границы не показаны без подтверждённого набора геоданных.</p></div></div><div className="panel-body"><span className="source-note">Проверенные границы не переданы.</span></div></Panel>
        </div>
        <aside className="right-column"><Panel title="Строки отчётов" caption="Детали, происхождение и доступные сравнения"><div className="status-line">Источник: 50 строк на этой странице. Показаны реквизиты и происхождение из отчёта.</div><div className="table-scroll"><table><thead><tr>{["Организация","ИНН / КПП","Территория","Период / источник","Долг","Сопоставление"].map(x=><th key={x}>{x}</th>)}</tr></thead><tbody>{rows.map(r=><tr key={r[0]}><td><div className="company-cell">{r[0]}</div><div className="subcell">ООО «Поставщик ТЭР»</div></td><td className="mono">{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td><td className="amount">{r[4]}</td><td><span className="badge">{r[5]}</span></td></tr>)}</tbody></table></div><div className="pagination"><span>Показаны 1–50 из 1 236</span><span>‹　1 / 25　›</span></div></Panel><Panel title="Последние загрузки" caption="Файлы и версии как зарегистрированы"><div className="imports-list"><div className="import-item"><div><div className="import-file">Отчёт_теплосеть_март_2026.xlsx</div><div className="import-meta">Владелец данных: Теплосеть Балашиха · загрузил: operator</div><div className="import-meta">Март 2026 · Тепло · версия 2 · 18.03.2026, 14:32</div></div><span className="import-meta">284 строки</span></div><div className="import-item"><div><div className="import-file">Долги_водоканал_03-2026.xlsx</div><div className="import-meta">Владелец данных: Водоканал · загрузил: analyst</div><div className="import-meta">Март 2026 · Вода · версия 1 · 17.03.2026, 10:06</div></div><span className="import-meta">512 строк</span></div></div></Panel></aside>
      </div>
    </main>
  </div>;
}

function Field({label,value}:{label:string;value:string}) { return <div className={`field ${label.startsWith("Поиск")?"search-field":""}`}><label>{label}</label><input value={value} readOnly /></div>; }
function Panel({title,caption,children}:{title:string;caption:string;children:ReactNode}) { return <section className="panel"><div className="panel-head"><div><h2 className="panel-title">{title}</h2><div className="panel-caption">{caption}</div></div></div>{children}</section>; }
