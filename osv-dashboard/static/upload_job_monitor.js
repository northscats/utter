(function () {
  'use strict';

  var STORAGE_KEY = 'brain4sales.activeUploadJobs';
  var POLL_MS = 2500;

  function readJobs() {
    try {
      var value = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
      return Array.isArray(value) ? value : [];
    } catch (_) {
      return [];
    }
  }

  function writeJobs(jobs) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(jobs));
  }

  function rememberJobFromUrl() {
    var params = new URLSearchParams(location.search);
    var jobId = params.get('job');
    var companyMatch = location.pathname.match(/\/company\/(\d+)/);
    if (!jobId || !companyMatch) return;
    var jobs = readJobs();
    if (!jobs.some(function (job) { return job.id === jobId; })) {
      jobs.push({
        id: jobId,
        companyId: Number(companyMatch[1]),
        startedAt: Date.now()
      });
      writeJobs(jobs);
    }
    params.delete('job');
    history.replaceState(
      null, '', location.pathname + (params.toString() ? '?' + params.toString() : '') +
      location.hash
    );
  }

  function toast(message, kind, companyId) {
    var host = document.getElementById('upload-job-toasts');
    if (!host) {
      host = document.createElement('div');
      host.id = 'upload-job-toasts';
      host.style.cssText =
        'position:fixed;right:18px;bottom:18px;z-index:10000;display:flex;' +
        'flex-direction:column;gap:10px;max-width:min(420px,calc(100vw - 36px))';
      document.body.appendChild(host);
    }
    var item = document.createElement('div');
    item.style.cssText =
      'background:#fff;border:1px solid ' + (kind === 'error' ? '#e7b6b0' : '#b8dfc6') +
      ';border-left:4px solid ' + (kind === 'error' ? '#c0392b' : '#3a7a57') +
      ';border-radius:9px;padding:12px 14px;box-shadow:0 8px 28px rgba(20,40,55,.18);' +
      'font:13px/1.4 Segoe UI,sans-serif;color:#243641';
    var text = document.createElement('span');
    text.textContent = message;
    item.appendChild(text);
    if (companyId) {
      var link = document.createElement('a');
      link.href = '/company/' + companyId;
      link.textContent = 'Открыть';
      link.style.cssText = 'margin-left:12px;color:#1f6680;font-weight:700';
      item.appendChild(link);
    }
    host.appendChild(item);
    setTimeout(function () { item.remove(); }, kind === 'error' ? 15000 : 9000);
  }

  function updateCompanyProgress(status, data) {
    var box = document.getElementById('job-progress');
    if (!box) return;
    box.style.cssText =
      'display:flex;align-items:center;gap:10px;background:#fff;border:1px solid #d3dade;' +
      'border-radius:10px;padding:14px 18px;margin-bottom:16px;font-size:14px';
    if (status === 'processing') {
      box.innerHTML =
        '<span style="display:inline-block;width:18px;height:18px;border:2px solid #d3dade;' +
        'border-top-color:#4a9ab5;border-radius:50%;animation:_jspin .7s linear infinite"></span>' +
        '<span>Обработка продолжается в фоне. Можно перейти в другой раздел.</span>';
    } else if (status === 'done') {
      box.innerHTML = '<strong style="color:#3a7a57">✓ Загрузка завершена</strong>';
    } else if (status === 'error') {
      box.innerHTML = '<strong style="color:#c0392b">✗ Ошибка загрузки:</strong> ' +
        '<span></span>';
      box.lastChild.textContent = data.error || 'неизвестная ошибка';
    }
  }

  function pollOne(job) {
    return fetch('/company/' + job.companyId + '/job/' + encodeURIComponent(job.id), {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
      cache: 'no-store'
    }).then(function (response) {
      if (response.status === 404) return { status: 'not_found' };
      if (!response.ok) throw new Error('status request failed');
      return response.json();
    }).then(function (data) {
      updateCompanyProgress(data.status, data);
      if (data.status === 'done') {
        var period = data.result && data.result.period_label;
        toast('Загрузка завершена' + (period ? ': ' + period : ''), 'done', job.companyId);
        return false;
      }
      if (data.status === 'error') {
        toast('Ошибка загрузки: ' + (data.error || 'неизвестная ошибка'), 'error', job.companyId);
        return false;
      }
      return data.status !== 'not_found';
    }).catch(function () {
      return true;
    });
  }

  var polling = false;
  function pollAll() {
    if (polling) return;
    var jobs = readJobs();
    if (!jobs.length) return;
    polling = true;
    Promise.all(jobs.map(function (job) {
      return pollOne(job).then(function (keep) { return { job: job, keep: keep }; });
    })).then(function (results) {
      writeJobs(results.filter(function (item) { return item.keep; })
        .map(function (item) { return item.job; }));
    }).finally(function () {
      polling = false;
    });
  }

  if (!document.getElementById('_jspin-style')) {
    var style = document.createElement('style');
    style.id = '_jspin-style';
    style.textContent = '@keyframes _jspin{to{transform:rotate(360deg)}}';
    document.head.appendChild(style);
  }

  rememberJobFromUrl();
  pollAll();
  setInterval(pollAll, POLL_MS);
})();