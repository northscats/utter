import "./_group.css";

type Variant = "current" | "aligned";

export function DialogPreview({ variant }: { variant: Variant }) {
  return (
    <div className={`upload-dialog-preview ${variant}`}>
      <div className="cabinet-backdrop" aria-hidden="true">
        <div className="cabinet-topbar">
          <span className="cabinet-brand">ГИС УЗТЭР</span>
          <span className="cabinet-user">Министерство</span>
        </div>
        <div className="cabinet-page">
          <div className="cabinet-heading" />
          <div className="cabinet-panels">
            <div className="cabinet-panel" />
            <div className="cabinet-panel" />
          </div>
        </div>
      </div>
      <div className="modal-backdrop">
        <section
          className="import-dialog"
          role="dialog"
          aria-modal="true"
          aria-labelledby={`import-title-${variant}`}
          tabIndex={-1}
        >
          <div className="dialog-head">
            <div>
              <h2 id={`import-title-${variant}`}>Загрузить отчёт XLSX</h2>
              <p>Сначала проверьте состав файла, затем отдельно подтвердите импорт.</p>
            </div>
            <button className="icon-button" type="button" aria-label="Закрыть окно загрузки">×</button>
          </div>
          <form>
            <div className="upload-zone">
              <input
                type="file"
                name="file"
                accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                aria-label="Файл отчёта XLSX"
              />
              <p>Поддерживается обычная незашифрованная книга XLSX.</p>
            </div>
            <div className="field provider-field">
              <label htmlFor={`provider-id-${variant}`}>Поставщик — владелец данных</label>
              <select id={`provider-id-${variant}`} defaultValue="">
                <option value="">Выберите организацию</option>
                <option value="provider">Теплосеть Балашиха · ИНН 5001023456</option>
              </select>
            </div>
            <div className="import-form-actions">
              <button className="button primary" type="button">Проверить файл</button>
            </div>
          </form>
          <div className="live-message" role="status" />
          <div id={`preview-result-${variant}`} />
        </section>
      </div>
    </div>
  );
}
