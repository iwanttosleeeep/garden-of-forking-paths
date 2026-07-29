// Self / I panel owns its local state and rendering outside the dashboard's
// primary script. Functions remain global because existing HTML controls call
// them through inline event handlers.

let _selfEntries = [];
let _selfAspectFilter = '';

async function openSelfPanel() {
  document.getElementById('self-panel').classList.add('open');
  document.getElementById('self-overlay').classList.add('show');
  await loadSelfEntries();
  if (window.lucide) lucide.createIcons();
}

function closeSelfPanel() {
  document.getElementById('self-panel').classList.remove('open');
  document.getElementById('self-overlay').classList.remove('show');
}

function setSelfFilter(btn) {
  document.querySelectorAll('.self-filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  _selfAspectFilter = btn.dataset.aspect;
  renderSelfEntries();
}

async function loadSelfEntries() {
  const body = document.getElementById('self-panel-body');
  try {
    const res = await authFetch('/api/self');
    if (!res || !res.ok) { body.innerHTML = '<div class="self-panel-empty">加载失败</div>'; return; }
    _selfEntries = await res.json();
    document.getElementById('self-fab').classList.toggle('has-entries', _selfEntries.length > 0);
    renderSelfEntries();
  } catch (e) {
    body.innerHTML = '<div class="self-panel-empty">' + esc(e.message) + '</div>';
  }
}

async function saveSelfEntry() {
  const contentEl = document.getElementById('self-content');
  const aspectEl = document.getElementById('self-aspect');
  const msg = document.getElementById('self-compose-msg');
  const content = (contentEl.value || '').trim();
  if (!content) { msg.textContent = '请先写下内容。'; return; }
  msg.textContent = '写入中…';
  try {
    const res = await authFetch('/api/self', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({content: content, aspect: aspectEl.value || ''}),
    });
    const data = await readJsonSafe(res);
    if (!res || !data || !data.ok) throw new Error((data && data.error) || '写入失败');
    contentEl.value = '';
    msg.textContent = '已写入。';
    await loadSelfEntries();
  } catch (e) {
    msg.textContent = e.message || '写入失败';
  }
}

function renderSelfEntries() {
  const body = document.getElementById('self-panel-body');
  const filtered = _selfAspectFilter
    ? _selfEntries.filter(e => e.aspect === _selfAspectFilter)
    : _selfEntries;

  if (!filtered.length) {
    body.innerHTML = '<div class="self-panel-empty">' + (_selfAspectFilter ? '该维度暂无条目' : '尚未写下任何自我认知') + '</div>';
    return;
  }

  body.innerHTML = filtered.map(e => {
    const ts = (e.created || '').slice(0, 16).replace('T', ' ');
    const aspectHtml = e.aspect
      ? '<span class="self-entry-aspect">' + esc(e.aspect) + '</span>'
      : '';
    return '<div class="self-entry">'
      + '<div class="self-entry-meta">' + aspectHtml + '<span class="self-entry-time">' + esc(ts) + '</span></div>'
      + '<div class="self-entry-content">' + esc(e.content) + '</div>'
      + '</div>';
  }).join('');
}

// Initialize the FAB marker after its DOM has been parsed.
(async function initSelfFab() {
  try {
    const res = await authFetch('/api/self');
    if (res && res.ok) {
      const data = await res.json();
      if (Array.isArray(data) && data.length > 0) {
        document.getElementById('self-fab').classList.add('has-entries');
        _selfEntries = data;
      }
    }
  } catch (_) {}
})();
