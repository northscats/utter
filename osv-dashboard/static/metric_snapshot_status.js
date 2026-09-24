/* The compact publication is independent of the live key-indicators payload. */
(function () {
  'use strict';
  const config = window.KI_CONFIG;
  const status = document.getElementById('ki-snapshot-status');
  const error = document.getElementById('ki-snapshot-error');
  const retry = document.getElementById('ki-snapshot-retry');
  if (!status || !config) return;

  const labels = {
    ready: 'готов', stale: 'ожидает пересборки',
    building: 'пересчитывается', failed: 'ошибка пересборки',
    not_built: 'ещё не создан'
  };
  const today = new Date().toISOString().slice(0, 10);
  const query = new URLSearchParams({
    company_ids: String(config.companyId), metric_ids: 'revenue',
    granularity: 'month', from: today, to: today
  });
  let pending = false;
  let revision = 0;

  async function refresh() {
    if (pending) return;
    const startedAt = revision;
    try {
      const response = await fetch('/analytics/summary?' + query, {
        credentials: 'same-origin', cache: 'no-store'
      });
      if (!response.ok) throw new Error('Не удалось получить статус пересборки.');
      const result = await response.json();
      if (startedAt !== revision) return;
      const item = result.companies && result.companies[0];
      if (!item) throw new Error('Нет статуса предприятия.');
      status.textContent = 'Снимок показателей: ' + (labels[item.status] || item.status);
      retry.hidden = item.status !== 'failed';
      error.textContent = item.status === 'failed' ? (item.error || 'Причина ошибки не указана.') : '';
    } catch (exc) {
      if (startedAt !== revision) return;
      status.textContent = 'Снимок показателей: статус недоступен';
      retry.hidden = true;
      error.textContent = exc.message;
    }
  }

  retry.addEventListener('click', async function () {
    if (pending) return;
    pending = true;
    revision++;
    retry.disabled = true;
    error.textContent = '';
    try {
      const response = await fetch(config.snapshotRetryUrl, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'X-CSRFToken': config.csrfToken, 'Accept': 'application/json' }
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Не удалось повторить пересборку.');
      status.textContent = 'Снимок показателей: ожидает пересборки';
      retry.hidden = true;
    } catch (exc) {
      error.textContent = exc.message;
    } finally {
      pending = false;
      retry.disabled = false;
    }
  });
  refresh();
  setInterval(function () {
    if (document.visibilityState === 'visible') refresh();
  }, 10000);
})();