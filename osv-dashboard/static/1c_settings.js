window.OneC = (function() {
  const state = {
    channels: [],
    companies: [],
    transport: {},
    activeChannelId: null,
    dialogTab: 'data',
    dataState: { offset: 0, limit: 50, colOffset: 0, colLimit: 30, receiptId: null },
    journalState: { page: 1, hasMore: false, filterChannelId: '' }
  };

  let previousFocus = null;
  let currentDataFetchId = 0;
  let currentJournalFetchId = 0;
  let currentSettingsGen = 0;

  function getRoot() {
    const el = document.getElementById('one_c_config');
    return el ? JSON.parse(el.dataset.root) : '';
  }

  const getCsrf = () => {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : '';
  };
  
  function escapeHtml(str) {
    if (str == null) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function formatDate(ts) {
    if (!ts) return '';
    const t = (ts.includes('Z') || ts.includes('+')) ? ts : ts + 'Z';
    return new Date(t).toLocaleString('ru-RU', {timeZone: 'UTC'}) + ' UTC';
  }

  async function api(path, options = {}) {
    const res = await fetch(getRoot() + path, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrf(),
        ...(options.headers || {})
      }
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Ошибка сервера');
    return data;
  }

  function wipeCreds() {
    ['one_c_edit_url', 'one_c_edit_login', 'one_c_creds_url', 'one_c_creds_login', 'one_c_creds_pass'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.value = '';
    });
    const res = document.getElementById('one_c_creds_result');
    if(res) res.style.display = 'none';
  }

  window.addEventListener('pagehide', wipeCreds);

  function copyField(id) {
    const el = document.getElementById(id);
    if (!el || !el.value) return;
    
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(el.value).catch(() => fallbackCopy(el));
    } else {
      fallbackCopy(el);
    }
  }

  function fallbackCopy(el) {
    el.select();
    try { document.execCommand('copy'); } catch(err) {}
  }

  function renderChannels() {
    const c = document.getElementById('one_c_channels_container');
    if (!c) return;
    
    if (state.transport && state.transport.http_note) {
      const noteEl = document.getElementById('one_c_global_http_note');
      if (noteEl) noteEl.textContent = state.transport.http_note;
    }

    if (state.channels.length === 0) {
      c.innerHTML = '<div style="padding: 20px; text-align: center; color: var(--mut);">Нет созданных каналов обмена</div>';
      return;
    }
    
    let html = `
      <div class="table-scroll">
        <table class="one-c-table">
          <thead>
            <tr>
              <th>Предприятие / ИНН</th>
              <th>Статус</th>
              <th>HTTP</th>
              <th>Последние данные</th>
              <th>Действия</th>
            </tr>
          </thead>
          <tbody>
    `;
    
    state.channels.forEach(ch => {
      const statusHtml = ch.enabled 
        ? `<span class="one-c-badge-enabled">Включен</span>` 
        : `<span class="one-c-badge-disabled">Отключен</span>`;
        
      const isHttpAllowed = ch.allow_http && !(state.transport && state.transport.production);
      const httpHtml = isHttpAllowed 
        ? `<span style="color: var(--bad); font-size: 12px; font-weight: 600;">Разрешён</span>` 
        : `<span style="color: var(--ok); font-size: 12px;">Запрещён</span>`;
      
      let lastRec = 'Данные ещё не поступали';
      if (ch.last_received_at) {
        lastRec = formatDate(ch.last_received_at);
      }
      
      html += `
        <tr>
          <td>
            <strong>${escapeHtml(ch.company_name || 'Неизвестно')}</strong><br>
            <span style="font-size: 12px; color: var(--mut);">ИНН: ${escapeHtml(ch.inn)}</span>
          </td>
          <td>${statusHtml}</td>
          <td>${httpHtml}</td>
          <td style="font-size: 12px; color: var(--mut);">
            ${lastRec}<br>
            Строк: ${ch.row_count || 0}
          </td>
          <td style="white-space: nowrap; display: flex; gap: 4px;">
            <button type="button" class="btn btn-test" style="padding: 5px 10px; display: flex; align-items: center; justify-content: center;" onclick="window.OneC.openDataDialog(${ch.id})" aria-label="Последние данные" title="Последние данные">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
            </button>
            <button type="button" class="btn btn-test" style="padding: 5px 10px; font-size: 12px;" onclick="window.OneC.openSettingsModal(${ch.id})">Настройки</button>
          </td>
        </tr>
      `;
    });
    html += `</tbody></table></div>`;
    c.innerHTML = html;

    const jf = document.getElementById('one_c_journal_channel_filter');
    if (jf) {
      let sel = jf.value;
      let opts = '<option value="">Все каналы</option>';
      state.channels.forEach(ch => {
        opts += `<option value="${ch.id}">${escapeHtml(ch.company_name)} (ИНН: ${escapeHtml(ch.inn)})</option>`;
      });
      jf.innerHTML = opts;
      jf.value = sel;
    }
  }
  
  async function loadChannels() {
    try {
      const c = document.getElementById('one_c_channels_container');
      if (c) c.innerHTML = '<div class="one-c-loading">Загрузка...</div>';
      
      const res = await api('/settings/1c/channels');
      state.channels = res.channels || [];
      state.companies = res.companies || [];
      state.transport = res.transport || {};
      renderChannels();
    } catch (err) {
      const c = document.getElementById('one_c_channels_container');
      if (c) c.innerHTML = `<div class="one-c-error-msg">Ошибка загрузки: ${escapeHtml(err.message)}</div>`;
    }
  }

  function resetMutationControls() {
    ['one_c_btn_create', 'one_c_btn_rotate', 'one_c_new_company',
      'one_c_edit_enabled'].forEach(id => {
      document.getElementById(id).disabled = false;
    });
    document.getElementById('one_c_edit_http').disabled = !!state.transport?.production;
  }

  function openCreateModal() {
    currentSettingsGen++;
    resetMutationControls();
    state.activeChannelId = null;
    document.getElementById('one_c_settings_title').textContent = 'Новый канал 1С';
    document.getElementById('one_c_settings_create').style.display = 'block';
    document.getElementById('one_c_settings_edit').style.display = 'none';
    wipeCreds();
    document.getElementById('one_c_settings_error').textContent = '';
    
    const sel = document.getElementById('one_c_new_company');
    sel.innerHTML = '<option value="">-- Выберите предприятие --</option>' + 
      state.companies.map(c => `<option value="${escapeHtml(String(c.id))}">${escapeHtml(c.name)} (ИНН: ${escapeHtml(c.inn)})</option>`).join('');
      
    document.getElementById('one_c_settings_dialog').showModal();
  }

  function openSettingsModal(chId) {
    currentSettingsGen++;
    resetMutationControls();
    state.activeChannelId = chId;
    const ch = state.channels.find(c => c.id === chId);
    if (!ch) return;
    
    document.getElementById('one_c_settings_title').textContent = 'Настройки канала';
    document.getElementById('one_c_settings_create').style.display = 'none';
    document.getElementById('one_c_settings_edit').style.display = 'block';
    wipeCreds();
    document.getElementById('one_c_settings_error').textContent = '';
    
    document.getElementById('one_c_edit_enabled').checked = ch.enabled;
    const httpCb = document.getElementById('one_c_edit_http');
    httpCb.checked = ch.allow_http;
    
    const hint = document.getElementById('one_c_http_hint');
    if (state.transport && state.transport.production) {
      httpCb.disabled = true;
      hint.textContent = 'В production окружении HTTP принудительно запрещён.';
      hint.style.color = 'var(--bad)';
    } else {
      httpCb.disabled = false;
      hint.textContent = state.transport?.http_note || 'Не рекомендуется. Пароль передаётся в открытом виде.';
      hint.style.color = 'var(--mut)';
    }

    let url = ch.url;
    if (url && url.startsWith('/')) {
      url = window.location.origin + getRoot() + url;
    }
    document.getElementById('one_c_edit_url').value = url || '';
    document.getElementById('one_c_edit_login').value = ch.login || '';
    
    document.getElementById('one_c_settings_dialog').showModal();
  }
  
  function closeSettingsModal() {
    document.getElementById('one_c_settings_dialog').close();
  }

  function handleSettingsClose() {
    currentSettingsGen++;
    wipeCreds();
  }

  function showCreds(channel, password, isEdit) {
    const box = document.getElementById('one_c_creds_result');
    box.style.display = 'block';
    
    let url = channel.url;
    if (url && url.startsWith('/')) {
      url = window.location.origin + getRoot() + url;
    }
    
    document.getElementById('one_c_creds_url').value = url || '';
    document.getElementById('one_c_creds_login').value = channel.login || '';
    document.getElementById('one_c_creds_pass').value = password || '';
  }
  
  async function createChannel() {
    const sel = document.getElementById('one_c_new_company');
    const compId = sel.value;
    if (!compId) {
      document.getElementById('one_c_settings_error').textContent = 'Выберите предприятие';
      return;
    }
    const btn = document.getElementById('one_c_btn_create');
    btn.disabled = true;
    const gen = currentSettingsGen;
    
    try {
      const res = await api('/settings/1c/channels', { 
        method: 'POST', 
        body: JSON.stringify({ company_id: parseInt(compId, 10) }) 
      });
      state.channels.push(res.channel);
      renderChannels();
      
      if (gen !== currentSettingsGen) return;
      
      document.getElementById('one_c_settings_create').style.display = 'none';
      showCreds(res.channel, res.password, false);
    } catch(err) {
      if (gen === currentSettingsGen) {
        document.getElementById('one_c_settings_error').textContent = err.message;
      }
    } finally {
      if (gen === currentSettingsGen) {
        btn.disabled = false;
      }
    }
  }

  async function updateChannel() {
    const enabledCb = document.getElementById('one_c_edit_enabled');
    const httpCb = document.getElementById('one_c_edit_http');
    const enabled = enabledCb.checked;
    const allow_http = httpCb.checked;
    const gen = currentSettingsGen;
    const targetId = state.activeChannelId;

    enabledCb.disabled = true;
    httpCb.disabled = true;

    try {
      const res = await api(`/settings/1c/channels/${targetId}`, {
        method: 'POST',
        body: JSON.stringify({ enabled, allow_http })
      });
      const idx = state.channels.findIndex(c => c.id === targetId);
      if (idx !== -1) state.channels[idx] = res.channel;
      renderChannels();
      
      if (gen !== currentSettingsGen) return;
      
      const errEl = document.getElementById('one_c_settings_error');
      errEl.style.color = 'var(--ok)';
      errEl.textContent = 'Сохранено';
      setTimeout(() => { 
        if (gen === currentSettingsGen) {
          errEl.textContent = ''; 
          errEl.style.color = 'var(--bad)'; 
        }
      }, 2000);
    } catch(err) {
      if (gen === currentSettingsGen) {
        document.getElementById('one_c_settings_error').textContent = err.message;
        const ch = state.channels.find(c => c.id === targetId);
        if (ch) {
          enabledCb.checked = ch.enabled;
          httpCb.checked = ch.allow_http;
        }
      }
    } finally {
      if (gen === currentSettingsGen) {
        enabledCb.disabled = false;
        if (!(state.transport && state.transport.production)) {
          httpCb.disabled = false;
        }
      }
    }
  }

  async function rotatePassword() {
    if (!confirm('Старый пароль перестанет работать. Продолжить?')) return;
    const btn = document.getElementById('one_c_btn_rotate');
    btn.disabled = true;
    const gen = currentSettingsGen;
    const targetId = state.activeChannelId;
    
    try {
      const res = await api(`/settings/1c/channels/${targetId}/rotate`, { method: 'POST' });
      
      if (gen !== currentSettingsGen) return;
      
      showCreds(res.channel, res.password, true);
    } catch(err) {
      if (gen === currentSettingsGen) {
        document.getElementById('one_c_settings_error').textContent = err.message;
      }
    } finally {
      if (gen === currentSettingsGen) {
        btn.disabled = false;
      }
    }
  }

  function openDataDialog(chId) {
    previousFocus = document.activeElement;
    state.activeChannelId = chId;
    state.dataState = { offset: 0, limit: 50, colOffset: 0, colLimit: 30, receiptId: null };
    state.journalState = { page: 1, hasMore: false, filterChannelId: String(chId) };
    
    document.getElementById('one_c_journal_channel_filter').value = String(chId);
    
    document.getElementById('one_c_409_banner').style.display = 'none';
    const dialog = document.getElementById('one_c_data_dialog');
    
    document.getElementById('one_c_tab_btn_data').style.display = 'block';
    
    dialog.showModal();
    switchTab('data');
  }

  function openStandaloneJournal() {
    previousFocus = document.activeElement;
    state.activeChannelId = null;
    state.journalState = { page: 1, hasMore: false, filterChannelId: '' };
    
    document.getElementById('one_c_journal_channel_filter').value = '';
    
    document.getElementById('one_c_409_banner').style.display = 'none';
    const dialog = document.getElementById('one_c_data_dialog');
    
    document.getElementById('one_c_tab_btn_data').style.display = 'none';
    
    dialog.showModal();
    switchTab('journal');
  }
  
  function closeDataDialog() {
    currentDataFetchId++;
    currentJournalFetchId++;
    const dialog = document.getElementById('one_c_data_dialog');
    dialog.close();
  }

  function switchTab(tab) {
    state.dialogTab = tab;
    document.getElementById('one_c_tab_data').style.display = tab === 'data' ? 'flex' : 'none';
    document.getElementById('one_c_tab_journal').style.display = tab === 'journal' ? 'flex' : 'none';
    
    document.querySelectorAll('.one-c-dialog-tab').forEach(el => el.classList.remove('active'));
    if (tab === 'data') document.getElementById('one_c_tab_btn_data').classList.add('active');
    if (tab === 'journal') document.getElementById('one_c_tab_btn_journal').classList.add('active');
    
    if (tab === 'data') refreshData();
    if (tab === 'journal') refreshJournal();
  }

  function setDataLoading(loading) {
    document.getElementById('one_c_btn_refresh_data').disabled = loading;
    document.getElementById('one_c_btn_row_prev').disabled = loading;
    document.getElementById('one_c_btn_row_next').disabled = loading;
    document.getElementById('one_c_btn_col_prev').disabled = loading;
    document.getElementById('one_c_btn_col_next').disabled = loading;
  }

  async function loadData() {
    const st = state.dataState;
    const content = document.getElementById('one_c_data_content');
    const banner = document.getElementById('one_c_409_banner');
    
    const fetchId = ++currentDataFetchId;
    
    document.getElementById('one_c_data_status').textContent = 'Загрузка...';
    content.innerHTML = '<div class="one-c-loading">Загрузка...</div>';
    setDataLoading(true);
    
    let url = `/settings/1c/channels/${state.activeChannelId}/data?offset=${st.offset}&limit=${st.limit}&column_offset=${st.colOffset}&column_limit=${st.colLimit}`;
    if (st.receiptId) url += `&receipt_id=${encodeURIComponent(st.receiptId)}`;
    
    try {
      const res = await fetch(getRoot() + url, { headers: { 'X-CSRFToken': getCsrf() } });
      if (fetchId !== currentDataFetchId) return;
      
      if (res.status === 409) {
        content.innerHTML = '<div class="one-c-loading">Снимок изменился. Нажмите «Обновить».</div>';
        banner.style.display = 'block';
        document.getElementById('one_c_data_status').textContent = 'Устарело';
        setDataLoading(false);
        return;
      }
      if (!res.ok) throw new Error((await res.json()).error || 'Ошибка');
      
      const data = await res.json();
      if (fetchId !== currentDataFetchId) return;
      
      banner.style.display = 'none';
      
      if (!data.received) {
        content.innerHTML = '<div class="one-c-loading">Данные ещё не поступали</div>';
        document.getElementById('one_c_data_status').innerHTML = '';
        document.getElementById('one_c_row_info').textContent = '0 - 0';
        document.getElementById('one_c_col_info').textContent = '0 - 0';
        setDataLoading(false);
        return;
      }
      
      st.receiptId = data.receipt_id;
      
      if (data.row_count === 0) {
        content.innerHTML = '<div class="one-c-loading">Пустой результат (0 строк).</div>';
        document.getElementById('one_c_row_info').textContent = '0 - 0';
        document.getElementById('one_c_col_info').textContent = '0 - 0';
      } else {
        let html = `<table class="one-c-data-table"><thead><tr><th>#</th>`;
        (data.columns || []).forEach(c => { html += `<th>${escapeHtml(c)}</th>`; });
        html += `</tr></thead><tbody>`;
        
        (data.rows || []).forEach((row, i) => {
          html += `<tr><td>${data.offset + i + 1}</td>`;
          row.forEach(cell => {
            let cls = '';
            let text = cell.text;
            if (cell.kind === 'missing') { cls = 'is-missing'; text = '—'; }
            else if (cell.kind === 'null') { cls = 'is-null'; text = 'null'; }
            else if (cell.kind === 'string' && text === '') { cls = 'is-empty'; text = '""'; }
            else if (cell.kind === 'json') { text = text; }
            else if (cell.kind === 'number') { text = String(text); }
            else if (cell.kind === 'boolean') { text = text; }
            
            html += `<td class="${cls}">${escapeHtml(text)}</td>`;
          });
          html += `</tr>`;
        });
        html += `</tbody></table>`;
        content.innerHTML = html;
        
        document.getElementById('one_c_row_info').textContent = `${data.offset + 1} - ${Math.min(data.offset + data.limit, data.row_count)} из ${data.row_count}`;
        document.getElementById('one_c_col_info').textContent = `${data.column_offset + 1} - ${Math.min(data.column_offset + data.column_limit, data.column_count)} из ${data.column_count}`;
      }
      
      const r_at = formatDate(data.received_at);
      let metaHtml = `Получено: ${r_at}`;
      if (data.receipt_id) {
        metaHtml = `Пакет: <code style="font-size:11px; background:var(--paper); padding:2px 4px; border-radius:3px;">${escapeHtml(data.receipt_id)}</code> &nbsp;|&nbsp; ` + metaHtml;
      }
      document.getElementById('one_c_data_status').innerHTML = metaHtml;
      
      setDataLoading(false);
      document.getElementById('one_c_btn_row_prev').disabled = st.offset === 0;
      document.getElementById('one_c_btn_row_next').disabled = data.row_count === 0 || (st.offset + st.limit >= data.row_count);
      document.getElementById('one_c_btn_col_prev').disabled = st.colOffset === 0;
      document.getElementById('one_c_btn_col_next').disabled = data.row_count === 0 || (st.colOffset + st.colLimit >= data.column_count);

    } catch (err) {
      if (fetchId !== currentDataFetchId) return;
      content.innerHTML = `<div class="one-c-error-msg">Ошибка: ${escapeHtml(err.message)}</div>`;
      document.getElementById('one_c_data_status').textContent = 'Ошибка';
      setDataLoading(false);
    }
  }

  function refreshData() {
    state.dataState.receiptId = null;
    state.dataState.offset = 0;
    state.dataState.colOffset = 0;
    loadData();
  }

  function dataPage(dir) {
    const st = state.dataState;
    const newOff = st.offset + dir * st.limit;
    if (newOff < 0) return;
    st.offset = newOff;
    loadData();
  }
  
  function colPage(dir) {
    const st = state.dataState;
    const newOff = st.colOffset + dir * st.colLimit;
    if (newOff < 0) return;
    st.colOffset = newOff;
    loadData();
  }

  function setJournalLoading(loading) {
    document.getElementById('one_c_btn_refresh_journal').disabled = loading;
    document.getElementById('one_c_btn_journal_prev').disabled = loading;
    document.getElementById('one_c_btn_journal_next').disabled = loading;
    document.getElementById('one_c_journal_channel_filter').disabled = loading;
  }

  async function loadJournal() {
    const st = state.journalState;
    const content = document.getElementById('one_c_journal_content');
    
    const fetchId = ++currentJournalFetchId;
    
    content.innerHTML = '<div class="one-c-loading">Загрузка...</div>';
    setJournalLoading(true);
    
    try {
      const chanParam = st.filterChannelId ? `&channel_id=${st.filterChannelId}` : '';
      const res = await fetch(getRoot() + `/settings/1c/attempts?page=${st.page}${chanParam}`, {
        headers: { 'X-CSRFToken': getCsrf() }
      });
      if (fetchId !== currentJournalFetchId) return;
      
      if (!res.ok) throw new Error((await res.json()).error || 'Ошибка');
      
      const data = await res.json();
      if (fetchId !== currentJournalFetchId) return;
      
      st.hasMore = data.has_more;
      
      if (!data.items || data.items.length === 0) {
        content.innerHTML = '<div class="one-c-loading">История загрузок пуста.</div>';
        setJournalLoading(false);
        document.getElementById('one_c_btn_journal_prev').disabled = st.page === 1;
        document.getElementById('one_c_btn_journal_next').disabled = true;
        document.getElementById('one_c_journal_page').textContent = `Стр ${st.page}`;
        return;
      }
      
      let html = `<table class="one-c-journal-table"><thead><tr>
        <th>Время (UTC)</th>
        <th>Результат</th>
        <th>Предприятие</th>
        <th>ID пакета</th>
        <th>Строки / Размер</th>
        <th>Ошибки</th>
      </tr></thead><tbody>`;
      
      data.items.forEach(it => {
        const dt = formatDate(it.received_at);
        
        let resHtml = '';
        if (it.result === 'success') resHtml = `<span style="color: var(--ok); font-weight: 600;">Успех</span>`;
        else if (it.result === 'accepted') resHtml = `<span style="color: var(--ok); font-weight: 600;">Принято</span>`;
        else if (it.result === 'duplicate') resHtml = `<span style="color: var(--mut); font-weight: 600;">Дубликат</span>`;
        else resHtml = `<span style="color: var(--bad); font-weight: 600;">Ошибка</span>`;
          
        let chanHtml = '';
        if (it.channel_id) {
          const ch = state.channels.find(c => c.id === it.channel_id);
          chanHtml = ch ? `${escapeHtml(ch.company_name)}<br><span style="font-size: 11px; color: var(--mut);">ИНН: ${escapeHtml(ch.inn)}</span>` : `ID: ${it.channel_id}`;
        } else {
          chanHtml = '<span style="color:var(--mut)">Неизвестно</span>';
        }

        html += `<tr>
          <td>${dt}<br><span style="font-size: 11px; color: var(--mut);">${escapeHtml(it.transport)}</span></td>
          <td>${resHtml}</td>
          <td>${chanHtml}</td>
          <td><code style="font-size: 11px;">${escapeHtml(it.receipt_id)}</code></td>
          <td>${it.row_count || 0} шт<br><span style="font-size: 11px; color: var(--mut);">${Math.round((it.byte_size||0)/1024)} KB</span></td>
          <td style="color: var(--bad); font-size: 12px; max-width: 200px; word-wrap: break-word;">${escapeHtml(it.error || '')}</td>
        </tr>`;
      });
      html += `</tbody></table>`;
      content.innerHTML = html;
      
      document.getElementById('one_c_journal_page').textContent = `Стр ${st.page}`;
      
      setJournalLoading(false);
      document.getElementById('one_c_btn_journal_prev').disabled = st.page === 1;
      document.getElementById('one_c_btn_journal_next').disabled = !st.hasMore;
      
    } catch(err) {
      if (fetchId !== currentJournalFetchId) return;
      content.innerHTML = `<div class="one-c-error-msg">Ошибка: ${escapeHtml(err.message)}</div>`;
      setJournalLoading(false);
    }
  }

  function refreshJournal() {
    state.journalState.page = 1;
    const filter = document.getElementById('one_c_journal_channel_filter');
    if (filter) state.journalState.filterChannelId = filter.value;
    loadJournal();
  }

  function journalPage(dir) {
    const st = state.journalState;
    if (dir === -1 && st.page > 1) {
      st.page--;
      loadJournal();
    } else if (dir === 1 && st.hasMore) {
      st.page++;
      loadJournal();
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    const observer = new MutationObserver(mutations => {
      mutations.forEach(m => {
        if (m.attributeName === 'aria-selected' && m.target.getAttribute('aria-selected') === 'true') {
          if (m.target.dataset.settingsTab === 'integrations') {
            loadChannels();
          }
        }
      });
    });
    
    const tabs = document.querySelectorAll('[data-settings-tab="integrations"]');
    tabs.forEach(t => observer.observe(t, { attributes: true }));
    
    const activeTab = document.querySelector('[data-settings-tab="integrations"][aria-selected="true"]');
    if (activeTab) {
      loadChannels();
    }
    
    const dataDialog = document.getElementById('one_c_data_dialog');
    if (dataDialog) {
      dataDialog.addEventListener('close', () => {
        if (previousFocus) previousFocus.focus();
        currentDataFetchId++;
        currentJournalFetchId++;
      });
    }

    const settingsDialog = document.getElementById('one_c_settings_dialog');
    if (settingsDialog) {
      settingsDialog.addEventListener('close', () => {
        if (previousFocus) previousFocus.focus();
        // Native onclose in HTML invokes window.OneC.handleSettingsClose()
      });
    }
  });

  return {
    loadChannels, openCreateModal, openSettingsModal, closeSettingsModal, handleSettingsClose,
    createChannel, updateChannel, rotatePassword, copyField,
    openDataDialog, openStandaloneJournal, closeDataDialog, switchTab,
    refreshData, dataPage, colPage,
    refreshJournal, journalPage
  };
})();