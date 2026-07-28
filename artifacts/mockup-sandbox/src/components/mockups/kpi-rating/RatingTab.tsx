import { useState } from "react";
type Signal = "green" | "yellow" | "red";

interface KPI {
  num: number;
  name: string;
  shortName: string;
  metric: string;
  actual: string;
  benchmark: string;
  signal: Signal;
  delta: string;
  deltaDir: "better" | "worse" | "neutral";
  comment: string;
  accounts: string;
}

const DATA: KPI[] = [
  {
    num: 1,
    name: "Дебиторская задолженность",
    shortName: "ДЗ",
    metric: "Период оборачиваемости, дней",
    actual: "94",
    benchmark: "≤ 60",
    signal: "red",
    delta: "+34 дня",
    deltaDir: "worse",
    comment: "Деньги «заморожены» у потребителей сверх нормы. Риск кассового разрыва.",
    accounts: "сч. 62, 76",
  },
  {
    num: 2,
    name: "Кредиторская задолженность",
    shortName: "КЗ",
    metric: "Доля просроченной КЗ, %",
    actual: "18%",
    benchmark: "≤ 10%",
    signal: "yellow",
    delta: "+8 п.п.",
    deltaDir: "worse",
    comment: "Просрочка по поставщикам выше нормы. Возможны пени и остановка поставок.",
    accounts: "сч. 60, 76, 68–70",
  },
  {
    num: 3,
    name: "Денежные средства",
    shortName: "ДС",
    metric: "Коэффициент ликвидности (ДС / КЗ)",
    actual: "0.31",
    benchmark: "≥ 0.50",
    signal: "red",
    delta: "−0.19",
    deltaDir: "worse",
    comment: "Подушка безопасности критически мала. Не покрывает и трети текущих обязательств.",
    accounts: "сч. 50, 51, 52, 55",
  },
  {
    num: 4,
    name: "Кредиты и займы",
    shortName: "Долг",
    metric: "Чистый долг / EBITDA",
    actual: "3.2×",
    benchmark: "≤ 2.5×",
    signal: "yellow",
    delta: "+0.7×",
    deltaDir: "worse",
    comment: "Долговая нагрузка выше комфортного уровня, но управляема. Контролировать.",
    accounts: "сч. 66, 67",
  },
  {
    num: 5,
    name: "Проценты по кредитам",
    shortName: "Проценты",
    metric: "Эффективная ставка, % годовых",
    actual: "16.4%",
    benchmark: "≤ КС+2% (18%)",
    signal: "green",
    delta: "−1.6 п.п.",
    deltaDir: "better",
    comment: "Стоимость долга в норме. Возможно рефинансирование при снижении ключевой ставки.",
    accounts: "сч. 91.02, 66.03, 67.03",
  },
  {
    num: 6,
    name: "Налоги и сборы",
    shortName: "Налоги",
    metric: "Налоговая нагрузка на ФОТ, %",
    actual: "31.2%",
    benchmark: "≤ 32%",
    signal: "green",
    delta: "−0.8 п.п.",
    deltaDir: "better",
    comment: "Нагрузка в пределах нормы. Отклонений от плана тарифа нет.",
    accounts: "сч. 68",
  },
  {
    num: 7,
    name: "Выручка",
    shortName: "Выручка",
    metric: "Выполнение НВВ, %",
    actual: "87%",
    benchmark: "≥ 95%",
    signal: "yellow",
    delta: "−8 п.п.",
    deltaDir: "worse",
    comment: "Недобор НВВ. Тёплая осень снизила отпуск тепла. Требуется корректировка у регулятора.",
    accounts: "сч. 90.01, 91.01",
  },
  {
    num: 8,
    name: "Себестоимость",
    shortName: "Себест-ть",
    metric: "Факт / план тарифной сметы, %",
    actual: "103%",
    benchmark: "≤ 100%",
    signal: "yellow",
    delta: "+3 п.п.",
    deltaDir: "worse",
    comment: "Небольшой перерасход по материалам. Часть затрат сверх тарифа ляжет на убытки.",
    accounts: "сч. 20, 26, 44, 90.02",
  },
  {
    num: 9,
    name: "Коммерческие расходы",
    shortName: "Сбыт",
    metric: "Стоимость сбора 1 руб., коп.",
    actual: "4.1",
    benchmark: "≤ 3.5",
    signal: "yellow",
    delta: "+0.6 коп.",
    deltaDir: "worse",
    comment: "Высокая доля офлайн-платежей через кассу. Перевод в онлайн снизит затраты.",
    accounts: "сч. 44",
  },
  {
    num: 10,
    name: "Управленческие расходы",
    shortName: "Управл.",
    metric: "Доля в выручке, %",
    actual: "6.8%",
    benchmark: "≤ 8%",
    signal: "green",
    delta: "−1.2 п.п.",
    deltaDir: "better",
    comment: "Административный аппарат в норме. Коэффициент надстройки 0.21 при норме 0.2.",
    accounts: "сч. 26",
  },
];

const SIGNAL_CFG = {
  green:  { bg: "#d1fae5", border: "#6ee7b7", dot: "#16a34a", label: "Норма",     icon: "●" },
  yellow: { bg: "#fef9c3", border: "#fde047", dot: "#ca8a04", label: "Внимание",  icon: "●" },
  red:    { bg: "#fee2e2", border: "#fca5a5", dot: "#dc2626", label: "Критично",  icon: "●" },
};

const COUNTS = {
  green:  DATA.filter(d => d.signal === "green").length,
  yellow: DATA.filter(d => d.signal === "yellow").length,
  red:    DATA.filter(d => d.signal === "red").length,
};

function StatusDot({ signal }: { signal: Signal }) {
  const cfg = SIGNAL_CFG[signal];
  return (
    <div style={{
      width: 10, height: 10, borderRadius: "50%",
      background: cfg.dot,
      flexShrink: 0, marginTop: 4,
    }} />
  );
}

function KPICard({ kpi }: { kpi: KPI }) {
  const cfg = SIGNAL_CFG[kpi.signal];
  return (
    <div style={{
      background: "#fff",
      border: `1px solid #d3dade`,
      borderRadius: 10,
      padding: "14px 16px",
      display: "flex", gap: 12, alignItems: "flex-start",
      boxShadow: "0 1px 4px rgba(0,0,0,.06)",
    }}>
      {/* Status dot */}
      <StatusDot signal={kpi.signal} />

      {/* Content */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 4 }}>
          <span style={{
            fontSize: 11, fontWeight: 700, color: "#7a96a8",
            background: "#f0f3f5", borderRadius: 4, padding: "1px 6px",
            flexShrink: 0,
          }}>№{kpi.num}</span>
          <span style={{ fontWeight: 700, fontSize: 13.5, color: "#1c2b38", lineHeight: 1.2 }}>
            {kpi.name}
          </span>
          <span style={{
            marginLeft: "auto", flexShrink: 0,
            fontSize: 11, fontWeight: 600, color: cfg.dot,
            background: cfg.bg, border: `1px solid ${cfg.border}`,
            borderRadius: 20, padding: "2px 9px",
          }}>{cfg.icon} {cfg.label}</span>
        </div>

        <div style={{ fontSize: 11.5, color: "#5e7a8a", marginBottom: 8 }}>
          {kpi.metric} · <span style={{ color: "#9ab0be" }}>{kpi.accounts}</span>
        </div>

        {/* Values row */}
        <div style={{ display: "flex", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
          <div style={{
            background: "#f0f3f5", borderRadius: 7, padding: "6px 12px",
            display: "flex", flexDirection: "column", alignItems: "center", minWidth: 80,
          }}>
            <span style={{ fontSize: 10, color: "#7a96a8", fontWeight: 600, marginBottom: 2 }}>ФАКТ</span>
            <span style={{ fontSize: 20, fontWeight: 800, color: "#1c2b38", lineHeight: 1 }}>{kpi.actual}</span>
          </div>
          <div style={{
            background: "#f0f3f5", borderRadius: 7, padding: "6px 12px",
            display: "flex", flexDirection: "column", alignItems: "center", minWidth: 80,
          }}>
            <span style={{ fontSize: 10, color: "#7a96a8", fontWeight: 600, marginBottom: 2 }}>ЭТАЛОН</span>
            <span style={{ fontSize: 20, fontWeight: 800, color: "#4a6070", lineHeight: 1 }}>{kpi.benchmark}</span>
          </div>
          <div style={{
            background: "#f0f3f5",
            borderRadius: 7, padding: "6px 12px",
            display: "flex", flexDirection: "column", alignItems: "center", minWidth: 80,
          }}>
            <span style={{ fontSize: 10, fontWeight: 600, marginBottom: 2, color: "#7a96a8" }}>ОТКЛ.</span>
            <span style={{ fontSize: 16, fontWeight: 800, lineHeight: 1, color: "#1c2b38" }}>{kpi.delta}</span>
          </div>
        </div>

        {/* Comment */}
        <div style={{
          fontSize: 11.5, color: "#5e7a8a", lineHeight: 1.4,
          borderTop: "1px dashed #d3dade", paddingTop: 7,
        }}>
          {kpi.comment}
        </div>
      </div>
    </div>
  );
}

export function RatingTab() {
  const [filter, setFilter] = useState<Signal | null>(null);
  const score = Math.round((COUNTS.green * 100 + COUNTS.yellow * 50) / (DATA.length * 100) * 100);
  const visible = filter ? DATA.filter(d => d.signal === filter) : DATA;

  return (
    <div style={{
      minHeight: "100vh",
      background: "#edf1f4",
      fontFamily: "'Segoe UI', system-ui, sans-serif",
      padding: "0",
    }}>
      {/* ── Top bar (имитация вкладки дашборда) ── */}
      <div style={{
        background: "#1c2b38", padding: "0 24px",
        display: "flex", alignItems: "center", gap: 0,
        borderBottom: "1px solid #2e4155",
      }}>
        {["Обзор","Поступления / расходы","Дебиторы / Кредиторы","Счёт 51/70","Рейтинг показателей"].map((tab, i) => (
          <div key={tab} style={{
            padding: "12px 16px", fontSize: 12.5, cursor: "pointer",
            fontWeight: i === 4 ? 700 : 400,
            color: i === 4 ? "#fff" : "#7a96a8",
            borderBottom: i === 4 ? "2px solid #1f7a9c" : "2px solid transparent",
            whiteSpace: "nowrap",
          }}>{tab}</div>
        ))}
      </div>

      <div style={{ padding: "20px 24px", maxWidth: 1280, margin: "0 auto" }}>

        {/* ── Summary header ── */}
        <div style={{
          background: "#fff", borderRadius: 12,
          padding: "18px 24px", marginBottom: 16,
          display: "flex", alignItems: "center", gap: 24,
          boxShadow: "0 1px 4px rgba(0,0,0,.07)",
          flexWrap: "wrap",
        }}>
          <div style={{ flex: 1, minWidth: 200 }}>
            <div style={{ fontSize: 18, fontWeight: 800, color: "#1c2b38", marginBottom: 4 }}>
              Рейтинг финансового здоровья РСО
            </div>
            <div style={{ fontSize: 12.5, color: "#7a96a8" }}>
              Сравнение с эталонным предприятием теплоснабжения · 10 показателей ФХД
            </div>
          </div>

          {/* Score gauge */}
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{
              width: 64, height: 64, borderRadius: "50%",
              background: `conic-gradient(${score >= 70 ? "#22c55e" : score >= 40 ? "#eab308" : "#ef4444"} ${score * 3.6}deg, #e2e8f0 0deg)`,
              display: "flex", alignItems: "center", justifyContent: "center",
              boxShadow: "0 2px 8px rgba(0,0,0,.12)",
            }}>
              <div style={{
                width: 46, height: 46, borderRadius: "50%",
                background: "#fff",
                display: "flex", alignItems: "center", justifyContent: "center",
                flexDirection: "column",
              }}>
                <span style={{ fontSize: 16, fontWeight: 800, color: "#1c2b38", lineHeight: 1 }}>{score}</span>
                <span style={{ fontSize: 9, color: "#7a96a8" }}>/ 100</span>
              </div>
            </div>
            <div style={{ fontSize: 13, color: "#1c2b38", fontWeight: 600 }}>
              Общий<br/>балл
            </div>
          </div>

          {/* Signal counts */}
          {([["green","Норма"],["yellow","Внимание"],["red","Критично"]] as [Signal,string][]).map(([s, lbl]) => {
            const cfg = SIGNAL_CFG[s];
            const active = filter === s;
            return (
              <div key={s} onClick={() => setFilter(active ? null : s as Signal)}
                style={{
                  background: active ? cfg.dot : cfg.bg,
                  border: `1.5px solid ${active ? cfg.dot : cfg.border}`,
                  borderRadius: 10, padding: "10px 18px",
                  display: "flex", alignItems: "center", gap: 10,
                  cursor: "pointer", userSelect: "none",
                  boxShadow: active ? `0 2px 10px ${cfg.dot}44` : "none",
                  transition: "all .15s",
                }}>
                <div style={{
                  width: 32, height: 32, borderRadius: "50%",
                  background: active ? "rgba(255,255,255,.25)" : cfg.dot,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  boxShadow: active ? "none" : `0 0 8px ${cfg.dot}55`,
                }}>
                  <span style={{ fontSize: 16, fontWeight: 800, color: "#fff" }}>{COUNTS[s]}</span>
                </div>
                <span style={{ fontSize: 12.5, fontWeight: 600, color: active ? "#fff" : cfg.dot }}>{lbl}</span>
              </div>
            );
          })}

          {/* Benchmark label */}
          <div style={{
            marginLeft: "auto",
            background: "#e8f4fb", border: "1px solid #b3d9ee",
            borderRadius: 10, padding: "10px 16px",
            display: "flex", alignItems: "center", gap: 8,
          }}>
            <div style={{
              width: 8, height: 8, borderRadius: "50%",
              background: "#1f7a9c",
            }} />
            <div>
              <div style={{ fontSize: 11, color: "#5b8fa8", fontWeight: 600 }}>ЭТАЛОН</div>
              <div style={{ fontSize: 12, color: "#1c2b38", fontWeight: 700 }}>ТеплоСнаб-Норма 2024</div>
              <div style={{ fontSize: 10.5, color: "#7a96a8" }}>Медиана по 50 РСО теплоснабжения РФ</div>
            </div>
          </div>
        </div>

        {/* ── 10 KPI cards grid ── */}
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(2, 1fr)",
          gap: 12,
        }}>
          {visible.map(kpi => <KPICard key={kpi.num} kpi={kpi} />)}
        </div>

        {/* ── Legend ── */}
        <div style={{
          marginTop: 16,
          background: "#fff", borderRadius: 10,
          padding: "12px 20px",
          display: "flex", gap: 24, alignItems: "center", flexWrap: "wrap",
          boxShadow: "0 1px 3px rgba(0,0,0,.05)",
        }}>
          <span style={{ fontSize: 11.5, fontWeight: 700, color: "#7a96a8" }}>СВЕТОФОР:</span>
          {([
            ["green","#d1fae5","#16a34a","Норма — значение лучше или в пределах эталона"],
            ["yellow","#fef9c3","#ca8a04","Внимание — отклонение до 20% от эталона, требует контроля"],
            ["red","#fee2e2","#dc2626","Критично — отклонение свыше 20%, требует немедленных действий"],
          ] as [string,string,string,string][]).map(([,bg,dot,text]) => (
            <div key={dot} style={{
              display: "flex", alignItems: "center", gap: 6,
            }}>
              <div style={{ width: 12, height: 12, borderRadius: "50%", background: dot, flexShrink: 0 }} />
              <span style={{ fontSize: 11.5, color: "#5e7a8a" }}>{text}</span>
            </div>
          ))}
        </div>

      </div>
    </div>
  );
}
