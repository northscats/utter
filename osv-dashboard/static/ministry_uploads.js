(function () {
  "use strict";

  const $ = selector => document.querySelector(selector);
  const csrfToken = $('meta[name="csrf-token"]')?.content || "";
  const numberFormat = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });
  const state = {
    imports: [],
    canDelete: false,
    total: 0,
    limit: 100,
    loaded: false,
  };

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, char => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[char]);
  }
  function display(value, fallback = "—") {
    return value === null || value === undefined || value === "" ? fallback : String(value);
  }
  function fmt(value) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numberFormat.format(numeric) : "—";
  }
  function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? String(value)
      : new Intl.DateTimeFormat("ru-RU", {
        dateStyle: "medium", timeStyle: "short"
      }).format(date);
  }
  function setActionStatus(message, type = "") {
    const status = $("#uploads-action-status");
    status.hidden = !message;
    status.className = `uploads-action-status${type ? ` ${type}` : ""}`;
    status.textContent = message;
  }
  function renderHistory() {
    const list = $("#uploads-list");
    const query = ($("#uploads-search").value || "").trim().toLocaleLowerCase("ru");
    const imports = [...state.imports].sort((left, right) => {
      const leftDate = Date.parse(left.createdAt || "") || 0;
      const rightDate = Date.parse(right.createdAt || "") || 0;
      return rightDate - leftDate || Number(right.id || 0) - Number(left.id || 0);
    });
    const filtered = imports.filter(item => [
      item.file, item.owner, item.sourceSupplier, item.resource,
      item.year, item.month, item.version, item.uploader
    ].join(" ").toLocaleLowerCase("ru").includes(query));

    $("#uploads-total").textContent = state.total > imports.length
      ? `Последние ${fmt(imports.length)} из ${fmt(state.total)} загрузок`
      : `Всего загрузок: ${fmt(state.total)}`;
    $("#uploads-result-count").textContent =
      `Найдено ${fmt(filtered.length)} из ${fmt(imports.length)}`;

    if (!filtered.length) {
      list.innerHTML = state.loaded
        ? `<li class="uploads-empty">${query
          ? "По этому запросу загрузки не найдены."
          : "Принятых загрузок пока нет."}</li>`
        : "";
      return;
    }

    list.innerHTML = filtered.map(item => {
      const period = item.year && item.month
        ? `${String(item.month).padStart(2, "0")}.${item.year}`
        : "Период не указан";
      const warnings = Number(item.warningCount || 0);
      const itemId = Number(item.id);
      return `<li class="uploads-item">
        <div>
          <span class="uploads-date-label">Дата загрузки</span>
          <time class="uploads-date" datetime="${escapeHtml(item.createdAt || "")}">${escapeHtml(formatDate(item.createdAt))}</time>
        </div>
        <div>
          <span class="uploads-file">${escapeHtml(display(item.file, "Файл без имени"))}</span>
          <div class="uploads-meta">
            Поставщик: ${escapeHtml(display(item.owner))} · ${escapeHtml(period)} · ${escapeHtml(display(item.resource))}<br>
            Загрузил: ${escapeHtml(display(item.uploader, "не указан"))} · версия ${escapeHtml(display(item.version))}<br>
            Строк в источнике: ${escapeHtml(fmt(item.rowCount))} · строк данных: ${escapeHtml(fmt(item.dataRowCount))}
          </div>
        </div>
        <div class="uploads-badges">
          ${item.isLatest ? '<span class="uploads-badge">Актуальная версия</span>' : ""}
          ${warnings ? `<span class="uploads-badge">Предупреждений: ${escapeHtml(fmt(warnings))}</span>` : ""}
          <a class="button" href="/ministry/import/${encodeURIComponent(itemId)}/file" download>Скачать исходный файл</a>
          ${state.canDelete ? `<button class="button uploads-delete" type="button" data-delete-import="${itemId}" aria-label="Удалить загрузку ${escapeHtml(display(item.file, "без имени"))}">Удалить загрузку</button>` : ""}
        </div>
      </li>`;
    }).join("");
  }

  async function requestJson(url, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("Accept", "application/json");
    const response = await fetch(url, {
      credentials: "same-origin",
      ...options,
      headers,
    });
    let payload = {};
    try {
      payload = await response.json();
    } catch (_) {
      payload = {};
    }
    if (!response.ok) {
      throw new Error(payload.message || payload.error || `Ошибка ${response.status}`);
    }
    return payload;
  }

  async function loadHistory() {
    const status = $("#uploads-status");
    status.hidden = false;
    status.classList.remove("error");
    status.textContent = "Загружаем историю файлов…";
    try {
      const data = await requestJson("/ministry/api/imports");
      state.imports = Array.isArray(data.imports) ? data.imports : [];
      state.canDelete = Boolean(data.permissions?.canDeleteImports);
      state.total = Number(data.total || 0);
      state.limit = Number(data.limit || 100);
      state.loaded = true;
      renderHistory();
      status.hidden = true;
      return true;
    } catch (error) {
      status.hidden = false;
      status.classList.add("error");
      status.textContent = `Не удалось загрузить историю файлов: ${error.message}`;
      return false;
    }
  }

  async function deleteImport(importIdValue, button) {
    const importId = Number(importIdValue);
    const item = state.imports.find(entry => Number(entry.id) === importId);
    if (!state.canDelete || !Number.isInteger(importId) || importId <= 0 || !item) return;
    const latestImpact = item.isLatest
      ? "Это актуальная версия. Если есть предыдущая версия, она станет актуальной; если её нет, данные отчёта исчезнут из кабинета."
      : "Другие версии отчёта останутся без изменений.";
    const confirmed = window.confirm(
      `Удалить загрузку «${item.file || "Файл без имени"}»?\n\n`
      + `Будут удалены все строки этого отчёта (${fmt(item.dataRowCount)}) и исходный XLSX-файл.\n`
      + `${latestImpact}\n\nДействие нельзя отменить.`,
    );
    if (!confirmed) return;

    button.disabled = true;
    setActionStatus("Удаляем загрузку…");
    try {
      const result = await requestJson(
        `/ministry/import/${importId}/delete`,
        { method: "POST", headers: { "X-CSRFToken": csrfToken } },
      );
      state.imports = state.imports.filter(entry => Number(entry.id) !== importId);
      state.total = Math.max(0, state.total - 1);
      renderHistory();
      const refreshed = await loadHistory();
      const message = result.message || "Загрузка удалена.";
      setActionStatus(
        refreshed ? message : `${message} Не удалось обновить список; обновите страницу.`,
        result.fileCleanupPending || !refreshed ? "warning" : "success",
      );
    } catch (error) {
      setActionStatus(`Не удалось удалить загрузку: ${error.message}`, "error");
      button.disabled = false;
    }
  }

  $("#uploads-search").addEventListener("input", renderHistory);
  $("#uploads-list").addEventListener("click", event => {
    const button = event.target.closest("[data-delete-import]");
    if (button) void deleteImport(button.dataset.deleteImport, button);
  });
  void loadHistory();
})();
