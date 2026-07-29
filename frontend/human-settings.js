// Human-name settings are kept separate from the dashboard runtime so the
// settings boundary can evolve without growing the main page script.

async function loadHumanName() {
  try {
    var res = await fetch(BASE + '/api/settings/human');
    if (res.ok) {
      var data = await res.json();
      var el = document.getElementById('settings-human-name');
      if (el) el.value = data.human || '';
    }
  } catch (e) { /* silent */ }
}

async function saveHumanName() {
  var el = document.getElementById('settings-human-name');
  var msg = document.getElementById('settings-human-msg');
  var name = (el ? el.value.trim() : '') || '人类';
  try {
    var res = await fetch(BASE + '/api/settings/human', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ human: name }),
    });
    var data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    var changed = (data.renamed && data.renamed.buckets_changed) || 0;
    if (msg) {
      msg.textContent = changed > 0 ? ('已保存，并同步了 ' + changed + ' 条旧记忆') : '已保存';
      msg.style.color = 'var(--positive)';
    }
    setTimeout(function() { if (msg) msg.textContent = ''; }, 4000);
  } catch (e) {
    if (msg) { msg.textContent = '保存失败：' + e.message; msg.style.color = 'var(--negative)'; }
  }
}

async function syncExistingHuman() {
  var fromEl = document.getElementById('settings-human-from');
  var msg = document.getElementById('settings-human-sync-msg');
  var from = fromEl ? fromEl.value.trim() : '';
  if (!from) from = '用户';
  if (msg) { msg.textContent = '替换中… / Replacing…'; msg.style.color = 'var(--text-dim)'; }
  try {
    var res = await fetch(BASE + '/api/settings/human/sync-existing', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from: from }),
    });
    var data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || res.statusText);
    if (data.note) {
      if (msg) { msg.textContent = data.note; msg.style.color = 'var(--text-dim)'; }
      return;
    }
    var changed = (data.renamed && data.renamed.buckets_changed) || 0;
    if (msg) {
      msg.textContent = changed > 0
        ? ('已把「' + data.from + '」→「' + data.to + '」，更新了 ' + changed + ' 条记忆')
        : ('没有找到含「' + data.from + '」的旧记忆');
      msg.style.color = changed > 0 ? 'var(--positive)' : 'var(--text-dim)';
    }
  } catch (e) {
    if (msg) { msg.textContent = '替换失败：' + e.message; msg.style.color = 'var(--negative)'; }
  }
}
