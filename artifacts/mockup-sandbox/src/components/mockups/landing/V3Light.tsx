export function V3Light() {
  return (
    <div style={{
      minHeight: '100vh',
      background: '#f4f6f9',
      fontFamily: "'Segoe UI', system-ui, sans-serif",
      display: 'flex',
      flexDirection: 'column',
      color: '#1b2733',
      position: 'relative',
      overflow: 'hidden',
    }}>
      {/* Top stripe */}
      <div style={{ height: 5, background: 'linear-gradient(90deg, #003f8a 0%, #1a6ec8 60%, #2ca4e8 100%)', flexShrink: 0 }} />

      {/* Subtle background shape */}
      <div style={{ position: 'absolute', top: 0, right: 0, width: '55%', height: '100%', background: 'linear-gradient(140deg, rgba(26,110,200,.05) 0%, transparent 60%)', pointerEvents: 'none' }} />
      <div style={{ position: 'absolute', bottom: 0, left: 0, width: 400, height: 400, borderRadius: '50%', background: 'radial-gradient(circle, rgba(26,110,200,.05) 0%, transparent 70%)', pointerEvents: 'none' }} />

      {/* Header */}
      <header style={{ padding: '20px 56px', display: 'flex', alignItems: 'center', gap: 16, background: '#fff', borderBottom: '1px solid #e2e8f0', flexShrink: 0, position: 'relative', zIndex: 1 }}>
        {/* Logo */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ width: 40, height: 40, borderRadius: 10, background: '#003f8a', display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 2px 10px rgba(0,63,138,.2)' }}>
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
              <path d="M10 2 L13 8 H17 L13 12 L14.5 18 L10 15 L5.5 18 L7 12 L3 8 H7 Z" fill="white"/>
            </svg>
          </div>
          <div>
            <div style={{ fontSize: 14, fontWeight: 700, color: '#003f8a', letterSpacing: '.01em' }}>ГИС УЗТЭР</div>
            <div style={{ fontSize: 11, color: '#8a9ab0', marginTop: 1 }}>Московская область</div>
          </div>
        </div>
        <div style={{ marginLeft: 'auto', fontSize: 11, color: '#aab4c0' }}>Анализ ФХД РСО</div>
      </header>

      {/* Hero */}
      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '64px 56px', position: 'relative', zIndex: 1 }}>

        {/* Icon block */}
        <div style={{ marginBottom: 36, position: 'relative' }}>
          <div style={{ width: 96, height: 96, borderRadius: 24, background: 'linear-gradient(135deg, #003f8a 0%, #1a6ec8 100%)', display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 8px 32px rgba(0,63,138,.22)' }}>
            <svg width="44" height="44" viewBox="0 0 44 44" fill="none">
              {/* Building/energy icon */}
              <rect x="8" y="18" width="28" height="20" rx="2" fill="white" opacity=".15"/>
              <rect x="8" y="18" width="28" height="20" rx="2" stroke="white" strokeWidth="1.5"/>
              <path d="M14 18V14a8 8 0 0 1 16 0v4" stroke="white" strokeWidth="1.5" strokeLinecap="round"/>
              {/* Bolt */}
              <path d="M22 22 L19 29 H22 L20 36 L27 28 H24 L26 22 Z" fill="#6dd8ff"/>
            </svg>
          </div>
        </div>

        {/* Titles */}
        <div style={{ textAlign: 'center', maxWidth: 640, marginBottom: 44 }}>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '.18em', textTransform: 'uppercase', color: '#6a8aaa', marginBottom: 12 }}>
            Государственная информационная система
          </div>
          <h1 style={{ fontSize: 30, fontWeight: 800, lineHeight: 1.25, color: '#0a1828', margin: '0 0 14px', letterSpacing: '-.01em' }}>
            Управление задолженностью<br />
            <span style={{ color: '#1a6ec8' }}>за топливно-энергетические ресурсы</span>
          </h1>
          <p style={{ fontSize: 15, color: '#6a8aaa', lineHeight: 1.6, margin: 0 }}>
            Единая платформа мониторинга и анализа финансово-хозяйственной деятельности<br />
            ресурсоснабжающих организаций Московской области
          </p>
        </div>

        {/* CTA buttons */}
        <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', justifyContent: 'center', marginBottom: 56 }}>
          <a href="/login" style={{
            display: 'inline-flex', alignItems: 'center', gap: 10,
            background: '#003f8a', color: '#fff', textDecoration: 'none',
            borderRadius: 10, padding: '15px 36px',
            fontSize: 14, fontWeight: 700, letterSpacing: '.03em',
            boxShadow: '0 4px 20px rgba(0,63,138,.28)',
          }}>
            <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
              <circle cx="10" cy="7" r="3.5"/><path d="M3 17c0-3.5 3.1-6 7-6s7 2.5 7 6"/>
            </svg>
            Войти в систему
          </a>
          <a href="#" style={{
            display: 'inline-flex', alignItems: 'center', gap: 10,
            background: '#fff', color: '#1b2733',
            border: '1.5px solid #d0d8e4', textDecoration: 'none',
            borderRadius: 10, padding: '15px 36px',
            fontSize: 14, fontWeight: 600, letterSpacing: '.03em',
            boxShadow: '0 2px 8px rgba(0,0,0,.06)',
          }}>
            <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M4 4h12v3H4zM4 10h12v3H4zM4 16h8"/>
            </svg>
            Открытые данные
          </a>
        </div>

        {/* Feature cards */}
        <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', justifyContent: 'center', maxWidth: 760 }}>
          {[
            { icon: '📊', title: 'Анализ ОСВ', desc: 'Автоматический разбор выгрузок из 1С' },
            { icon: '🏆', title: 'Рейтинг КПЭ', desc: '10 финансовых показателей организаций' },
            { icon: '⚡', title: 'Мониторинг', desc: 'Дашборды и суточная динамика' },
          ].map((f, i) => (
            <div key={i} style={{ background: '#fff', border: '1px solid #e2e8f0', borderRadius: 12, padding: '18px 22px', display: 'flex', alignItems: 'flex-start', gap: 12, flex: '1 1 200px', boxShadow: '0 2px 8px rgba(0,0,0,.04)' }}>
              <span style={{ fontSize: 22, flexShrink: 0 }}>{f.icon}</span>
              <div>
                <div style={{ fontSize: 13, fontWeight: 700, color: '#0a1828', marginBottom: 3 }}>{f.title}</div>
                <div style={{ fontSize: 12, color: '#8a9ab0', lineHeight: 1.45 }}>{f.desc}</div>
              </div>
            </div>
          ))}
        </div>
      </main>

      {/* Footer */}
      <footer style={{ padding: '16px 56px', background: '#fff', borderTop: '1px solid #e2e8f0', display: 'flex', justifyContent: 'space-between', fontSize: 11, color: '#aab4c0', flexShrink: 0 }}>
        <span>© 2025 Московская область · Министерство энергетики</span>
        <span>ГИС УЗТЭР — защищённое соединение</span>
      </footer>
    </div>
  );
}
