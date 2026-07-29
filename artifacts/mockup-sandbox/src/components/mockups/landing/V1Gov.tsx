export function V1Gov() {
  return (
    <div style={{
      minHeight: '100vh',
      background: 'linear-gradient(160deg, #0a1628 0%, #0d2249 50%, #0f2f5e 100%)',
      fontFamily: "'Segoe UI', system-ui, sans-serif",
      display: 'flex',
      flexDirection: 'column',
      color: '#fff',
      position: 'relative',
      overflow: 'hidden',
    }}>
      {/* Background grid pattern */}
      <div style={{
        position: 'absolute', inset: 0, opacity: 0.06,
        backgroundImage: `linear-gradient(rgba(255,255,255,.4) 1px, transparent 1px),
          linear-gradient(90deg, rgba(255,255,255,.4) 1px, transparent 1px)`,
        backgroundSize: '60px 60px',
      }} />

      {/* Top accent line */}
      <div style={{ height: 4, background: 'linear-gradient(90deg, #c8102e 0%, #e8c84a 50%, #c8102e 100%)', flexShrink: 0 }} />

      {/* Header */}
      <header style={{ padding: '18px 48px', display: 'flex', alignItems: 'center', gap: 16, borderBottom: '1px solid rgba(255,255,255,.08)', flexShrink: 0 }}>
        {/* Emblem */}
        <svg width="42" height="42" viewBox="0 0 42 42" fill="none">
          <circle cx="21" cy="21" r="20" fill="rgba(255,255,255,.08)" stroke="rgba(255,255,255,.2)" strokeWidth="1"/>
          <path d="M21 8 L24 15 L32 15 L26 20 L28 28 L21 23 L14 28 L16 20 L10 15 L18 15 Z" fill="#e8c84a" opacity=".9"/>
          <circle cx="21" cy="21" r="5" fill="none" stroke="rgba(255,255,255,.3)" strokeWidth="1"/>
        </svg>
        <div>
          <div style={{ fontSize: 11, letterSpacing: '.18em', textTransform: 'uppercase', color: '#8ba8d4', marginBottom: 1 }}>Московская область</div>
          <div style={{ fontSize: 13, fontWeight: 600, color: '#c8d8f0', letterSpacing: '.04em' }}>Министерство энергетики</div>
        </div>
        <div style={{ marginLeft: 'auto', fontSize: 11, color: '#4a6a9a', letterSpacing: '.06em' }}>ГИС УЗТЭР · v1.0</div>
      </header>

      {/* Main */}
      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '60px 48px', position: 'relative', zIndex: 1 }}>

        {/* Central emblem */}
        <div style={{ marginBottom: 40, position: 'relative' }}>
          <svg width="110" height="110" viewBox="0 0 110 110" fill="none">
            <circle cx="55" cy="55" r="52" fill="rgba(255,255,255,.04)" stroke="rgba(200,210,240,.15)" strokeWidth="1.5"/>
            <circle cx="55" cy="55" r="40" fill="rgba(255,255,255,.04)" stroke="rgba(200,210,240,.12)" strokeWidth="1"/>
            {/* Shield */}
            <path d="M55 22 C55 22 35 30 35 55 C35 70 45 80 55 85 C65 80 75 70 75 55 C75 30 55 22 55 22Z" fill="rgba(14,60,120,.7)" stroke="#4a80c8" strokeWidth="1.5"/>
            {/* Bolt */}
            <path d="M52 40 L46 56 L53 56 L50 72 L64 52 L57 52 L62 40 Z" fill="#e8c84a"/>
            {/* Ring ornament */}
            <circle cx="55" cy="55" r="52" fill="none" stroke="rgba(200,210,240,.08)" strokeWidth="6" strokeDasharray="4 8"/>
          </svg>
        </div>

        {/* Titles */}
        <div style={{ textAlign: 'center', maxWidth: 680, marginBottom: 52 }}>
          <div style={{ fontSize: 12, letterSpacing: '.22em', textTransform: 'uppercase', color: '#6a90c0', marginBottom: 14 }}>
            Государственная информационная система
          </div>
          <h1 style={{
            fontSize: 28, fontWeight: 700, lineHeight: 1.3, margin: '0 0 16px',
            background: 'linear-gradient(135deg, #ffffff 0%, #c0d8f8 100%)',
            WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent',
          }}>
            Управление задолженностью за топливно-энергетические ресурсы
          </h1>
          <div style={{ fontSize: 14, color: '#6a90c0', letterSpacing: '.04em' }}>Московская область</div>
        </div>

        {/* Divider */}
        <div style={{ width: 220, height: 1, background: 'linear-gradient(90deg, transparent, rgba(200,210,240,.25), transparent)', marginBottom: 48 }} />

        {/* Buttons */}
        <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', justifyContent: 'center' }}>
          <a href="/login" style={{
            display: 'inline-flex', alignItems: 'center', gap: 10,
            background: 'linear-gradient(135deg, #1a4fa0 0%, #0d3278 100%)',
            color: '#fff', textDecoration: 'none',
            border: '1px solid rgba(100,150,220,.4)',
            borderRadius: 10, padding: '16px 36px',
            fontSize: 14, fontWeight: 600, letterSpacing: '.04em',
            boxShadow: '0 4px 24px rgba(20,70,180,.35)',
            transition: 'all .2s',
          }}>
            <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <circle cx="10" cy="7" r="3.5"/><path d="M3 17c0-3.5 3.1-6 7-6s7 2.5 7 6"/>
            </svg>
            Войти в систему
          </a>
          <a href="#" style={{
            display: 'inline-flex', alignItems: 'center', gap: 10,
            background: 'rgba(255,255,255,.05)',
            color: '#c0d4f0', textDecoration: 'none',
            border: '1px solid rgba(200,210,240,.18)',
            borderRadius: 10, padding: '16px 36px',
            fontSize: 14, fontWeight: 600, letterSpacing: '.04em',
            transition: 'all .2s',
          }}>
            <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <rect x="2" y="3" width="16" height="14" rx="2"/><path d="M6 8h8M6 12h5"/>
            </svg>
            Портал данных
          </a>
        </div>

        {/* Status strip */}
        <div style={{ marginTop: 52, display: 'flex', gap: 28, fontSize: 11, color: '#4a6a9a', letterSpacing: '.05em' }}>
          <span>🔒 Защищённое соединение</span>
          <span>·</span>
          <span>⚡ Мониторинг в режиме реального времени</span>
          <span>·</span>
          <span>📊 Аналитика ФХД РСО</span>
        </div>
      </main>

      {/* Footer */}
      <footer style={{ padding: '16px 48px', borderTop: '1px solid rgba(255,255,255,.06)', display: 'flex', justifyContent: 'space-between', fontSize: 11, color: '#3a5a8a', flexShrink: 0 }}>
        <span>© 2025 Министерство энергетики Московской области</span>
        <span>ГИС УЗТЭР — Анализ ФХД РСО</span>
      </footer>
    </div>
  );
}
