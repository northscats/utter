export function V2Dark() {
  return (
    <div style={{
      minHeight: '100vh',
      background: '#0e0f14',
      fontFamily: "'Segoe UI', system-ui, sans-serif",
      display: 'flex',
      flexDirection: 'column',
      color: '#fff',
      position: 'relative',
      overflow: 'hidden',
    }}>
      {/* Glow blobs */}
      <div style={{ position: 'absolute', top: -140, left: '30%', width: 600, height: 600, borderRadius: '50%', background: 'radial-gradient(circle, rgba(0,180,200,.12) 0%, transparent 70%)', pointerEvents: 'none' }} />
      <div style={{ position: 'absolute', bottom: -100, right: '10%', width: 400, height: 400, borderRadius: '50%', background: 'radial-gradient(circle, rgba(80,0,200,.1) 0%, transparent 70%)', pointerEvents: 'none' }} />

      {/* Header */}
      <header style={{ padding: '20px 56px', display: 'flex', alignItems: 'center', gap: 14, borderBottom: '1px solid rgba(255,255,255,.05)', flexShrink: 0, position: 'relative', zIndex: 1 }}>
        {/* Logo mark */}
        <div style={{ width: 36, height: 36, borderRadius: 9, background: 'linear-gradient(135deg, #00c8e0, #0080c8)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
            <path d="M9 2 L12 8 H15 L10 13 L12 16 L9 14 L6 16 L8 13 L3 8 H6 Z" fill="white" opacity=".9"/>
          </svg>
        </div>
        <span style={{ fontSize: 13, fontWeight: 700, color: '#e0e8f4', letterSpacing: '.03em' }}>ГИС УЗТЭР</span>
        <span style={{ marginLeft: 'auto', fontSize: 11, color: '#2a3a4a', background: 'rgba(0,200,224,.08)', border: '1px solid rgba(0,200,224,.15)', borderRadius: 6, padding: '3px 9px', letterSpacing: '.06em' }}>МО · 2025</span>
      </header>

      {/* Main */}
      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '60px 56px', position: 'relative', zIndex: 1 }}>

        {/* Badge */}
        <div style={{
          display: 'inline-flex', alignItems: 'center', gap: 7, marginBottom: 32,
          background: 'rgba(0,200,224,.07)', border: '1px solid rgba(0,200,224,.2)',
          borderRadius: 100, padding: '6px 16px',
          fontSize: 11, color: '#00c8e0', letterSpacing: '.1em', textTransform: 'uppercase',
        }}>
          <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#00c8e0', display: 'inline-block', boxShadow: '0 0 8px #00c8e0' }} />
          Государственная информационная система
        </div>

        {/* Heading */}
        <div style={{ textAlign: 'center', maxWidth: 720, marginBottom: 20 }}>
          <h1 style={{ fontSize: 36, fontWeight: 800, lineHeight: 1.2, margin: 0, letterSpacing: '-.01em' }}>
            <span style={{ color: '#fff' }}>Управление</span>{' '}
            <span style={{ background: 'linear-gradient(90deg, #00c8e0, #0094ff)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>задолженностью</span>
            <br />
            <span style={{ color: '#8090a8', fontSize: 28, fontWeight: 600 }}>за топливно-энергетические ресурсы</span>
          </h1>
        </div>
        <p style={{ fontSize: 15, color: '#4a6080', maxWidth: 540, textAlign: 'center', lineHeight: 1.6, marginBottom: 52 }}>
          Московская область · Анализ финансово-хозяйственной деятельности ресурсоснабжающих организаций
        </p>

        {/* Buttons */}
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', justifyContent: 'center', marginBottom: 60 }}>
          <a href="/login" style={{
            display: 'inline-flex', alignItems: 'center', gap: 10,
            background: 'linear-gradient(135deg, #00a8c8 0%, #0070b8 100%)',
            color: '#fff', textDecoration: 'none', borderRadius: 10,
            padding: '15px 34px', fontSize: 14, fontWeight: 700, letterSpacing: '.03em',
            boxShadow: '0 0 28px rgba(0,160,200,.3)',
          }}>
            <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
              <rect x="3" y="11" width="14" height="8" rx="2"/><path d="M7 11V7a3 3 0 0 1 6 0v4"/>
            </svg>
            Войти в систему
          </a>
          <a href="#" style={{
            display: 'inline-flex', alignItems: 'center', gap: 10,
            background: 'rgba(255,255,255,.04)', border: '1px solid rgba(255,255,255,.1)',
            color: '#8090a8', textDecoration: 'none', borderRadius: 10,
            padding: '15px 34px', fontSize: 14, fontWeight: 600, letterSpacing: '.03em',
          }}>
            <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <circle cx="10" cy="10" r="8"/><path d="M10 6v4l3 3"/>
            </svg>
            Открытые данные
          </a>
        </div>

        {/* Stats row */}
        <div style={{ display: 'flex', gap: 1, borderRadius: 14, overflow: 'hidden', border: '1px solid rgba(255,255,255,.07)' }}>
          {[
            { val: 'РСО', label: 'Ресурсоснабжающие орг.' },
            { val: 'ОСВ', label: 'Обработка выгрузок 1С' },
            { val: 'КПЭ', label: '10 показателей рейтинга' },
          ].map((s, i) => (
            <div key={i} style={{ padding: '18px 32px', background: 'rgba(255,255,255,.02)', textAlign: 'center', borderRight: i < 2 ? '1px solid rgba(255,255,255,.06)' : 'none' }}>
              <div style={{ fontSize: 22, fontWeight: 800, color: '#00c8e0', marginBottom: 4 }}>{s.val}</div>
              <div style={{ fontSize: 11, color: '#3a5070', letterSpacing: '.04em' }}>{s.label}</div>
            </div>
          ))}
        </div>
      </main>

      {/* Footer */}
      <footer style={{ padding: '14px 56px', borderTop: '1px solid rgba(255,255,255,.04)', display: 'flex', justifyContent: 'space-between', fontSize: 11, color: '#2a3a4a', flexShrink: 0, position: 'relative', zIndex: 1 }}>
        <span>Московская область · Министерство энергетики</span>
        <span>Защищено · TLS 1.3</span>
      </footer>
    </div>
  );
}
