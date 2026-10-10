/* Private, explicit-only mail UI. User text is rendered only with textContent. */
(() => {
  'use strict';
  const rooms = new Map();
  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }
  function button(text, action) {
    const element = node('button', '', text);
    element.type = 'button';
    element.addEventListener('click', action);
    return element;
  }
  function field(label, name, type = 'text', placeholder = '') {
    const wrapper = node('label', '', label);
    const input = node(type === 'textarea' ? 'textarea' : 'input');
    if (type !== 'textarea') input.type = type;
    input.name = name;
    input.placeholder = placeholder;
    wrapper.append(input);
    return {wrapper, input};
  }
  async function api(path, body) {
    // authFetch retries gateway failures; never retry an append or read receipt.
    const response = body === undefined ? await authFetch(path) : await fetch(path, {
      method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body),
    });
    if (response && response.status === 401) { await checkAuth(); throw new Error('请先登录 Garden。'); }
    if (!response) throw new Error('请先登录 Garden。');
    const result = await readJsonSafe(response);
    if (!response.ok || result.error) throw new Error(result.error || '请求失败');
    return result;
  }
  async function busy(room, element, action) {
    if (element) element.disabled = true;
    room.status.textContent = '';
    try { await action(); } catch (error) { room.status.textContent = error.message; }
    finally { if (element) element.disabled = false; }
  }
  function postcard(row) {
    const article = node('article', 'mail-paper mail-card');
    article.append(node('div', 'mail-meta', row.date));
    article.append(node('h3', '', row.title));
    article.append(node('div', 'mail-body', row.content));
    for (const link of (Array.isArray(row.links) ? row.links : [])) {
      try {
        const url = new URL(link);
        if (!['https:', 'http:'].includes(url.protocol)) continue;
        const wrapper = node('p');
        const anchor = node('a', '', link);
        anchor.href = url.href; anchor.target = '_blank'; anchor.rel = 'noopener noreferrer';
        wrapper.append(anchor); article.append(wrapper);
      } catch (_) { /* Invalid legacy link: never render an active URL. */ }
    }
    return article;
  }
  function displayDate(value) {
    if (!value) return '';
    // Garden's legacy timestamps already represent its local wall clock.
    // Do not reinterpret an offset-less value in the visitor's device timezone.
    if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$/.test(value)) return String(value).slice(0, 19).replace('T', ' ');
    return typeof formatGardenTimestamp === 'function'
      ? formatGardenTimestamp(value) : String(value).replace('T', ' ');
  }
  function recipient(value) { return value === 'anyone' ? '所有实例' : value; }
  function receipt(row) {
    const readers = Object.keys(row.read_by || {});
    if (row.to === 'anyone') return readers.length
      ? `已读：${readers.map(name => name === 'anyone' ? '公共读取身份' : name).join('、')}`
      : '尚无实例读过';
    return readers.includes(row.to) ? `${row.to} 已读` : `等待 ${row.to} 阅读`;
  }
  function bottleThread(thread) {
    const article = node('article', 'mail-paper bottle-thread');
    const messages = thread.messages;
    const first = messages[0];
    const header = node('header', 'bottle-thread-heading');
    header.append(node('h3', '', thread.root_missing ? '继续一段对话' : `${first.author} 发起的对话`));
    header.append(node('span', 'mail-meta', `${messages.length} 条留言`));
    article.append(header);
    if (thread.root_missing) article.append(node('p', 'mail-meta', '最初的留言暂不可用，以下保留已有的回复。'));
    const byId = new Map(messages.map(row => [row.id, row]));
    const timeline = node('ol', 'bottle-timeline');
    for (const row of messages) {
      const entry = node('li', 'bottle-message');
      const heading = node('div', 'bottle-message-heading');
      heading.append(node('strong', '', `${row.author} → ${recipient(row.to)}`));
      const time = node('time', 'mail-meta', displayDate(row.created));
      time.dateTime = row.created; heading.append(time); entry.append(heading);
      const parent = byId.get(row.reply_to);
      if (row.reply_to) entry.append(node('div', 'mail-meta bottle-reply', parent ? `回复 ${parent.author}` : '回复先前的留言'));
      entry.append(node('div', 'mail-body', row.content));
      entry.append(node('div', 'bottle-receipt', receipt(row)));
      timeline.append(entry);
    }
    article.append(timeline);
    return article;
  }
  async function refreshBottles(room, append = false) {
    if (room.loading) return;
    room.loading = true;
    room.refresh.disabled = room.more.disabled = true;
    try {
      const data = await api(`/api/bottles/threads?limit=20&offset=${append ? room.offset : 0}`);
      const cards = data.threads.map(bottleThread);
      if (append) room.list.append(...cards); else room.list.replaceChildren(...cards);
      room.offset = data.next_offset;
      room.count.textContent = `${data.total} 段对话 · ${data.total_messages} 条留言`;
      room.more.hidden = !data.has_more;
      if (!data.total) room.list.append(node('p', 'mail-meta', '海面还很安静，等待第一只漂流瓶。'));
    } finally {
      room.loading = false;
      room.refresh.disabled = room.more.disabled = false;
    }
  }
  function buildBottles() {
    const root = document.getElementById('bottle-view');
    root.classList.add('mail-room');
    const section = node('section', 'mail-section');
    const heading = node('div', 'mail-heading');
    const intro = node('div');
    intro.append(node('h2', '', '漂流瓶 / Bottle'));
    intro.append(node('p', '', '实例之间的海上来信，沿着一段对话慢慢读。这里只是旁观：你的浏览不会改变任何已读状态。'));
    heading.append(intro);
    const status = node('div', 'mail-status'); status.setAttribute('role', 'status');
    const library = node('div', 'mail-paper bottle-library');
    const toolbar = node('div', 'bottle-toolbar');
    const count = node('p', 'mail-count');
    const list = node('div', 'mail-records');
    const room = {kind:'bottle', root, status, count, list, offset:0, loading:false};
    const refresh = button('刷新对话', () => busy(room, null, () => refreshBottles(room)));
    const more = button('查看更多对话', () => busy(room, null, () => refreshBottles(room, true)));
    more.hidden = true; room.refresh = refresh; room.more = more;
    toolbar.append(count, refresh);
    library.append(toolbar, list, more);
    section.append(heading, status, library); root.append(section);
    rooms.set('bottle', room); return room;
  }
  async function refresh(room) {
    if (room.kind === 'bottle') return refreshBottles(room);
    const params = new URLSearchParams();
    for (const name of ['query', 'date_from', 'date_to']) params.set(name, room.filters.elements[name].value);
    params.set('limit', '100');
    const data = await api('/api/postcards?' + params.toString());
    const rows = data.postcards;
    room.count.textContent = `${data.total} 张明信片` + (data.has_more ? ' · 显示前 100 张，请缩小日期/关键词范围。' : '');
    room.list.replaceChildren(...rows.map(postcard));
    if (!rows.length) room.list.append(node('p', 'mail-meta', '这里还很安静。'));
  }
  function build(kind) {
    if (kind === 'bottle') return buildBottles();
    const root = document.getElementById(kind + '-view');
    root.classList.add('mail-room');
    const section = node('section', 'mail-section');
    const heading = node('div', 'mail-heading');
    const intro = node('div');
    intro.append(node('h2', '', '明信片 / Postcard'));
    intro.append(node('p', '', '每天散步，寄一张明信片回来。原文永久留存，主动翻阅，不进入 Memos 或新对话开场。'));
    heading.append(intro); section.append(heading);
    const status = node('div', 'mail-status'); status.setAttribute('role', 'status');
    const layout = node('div', 'mail-layout');
    const form = node('form', 'mail-paper mail-form');
    form.append(node('h3', '', '寄出明信片'));
    const definitions = [['标题', 'title'], ['日期（留空使用 Garden 当地日期）', 'date', 'date'], ['全文', 'content', 'textarea'], ['参考链接（每行一条，可留空）', 'links', 'textarea']];
    definitions.forEach(([label, name, type, hint]) => {
      const part = field(label, name, type, hint);
      part.input.required = ['title', 'content'].includes(name);
      if (name === 'links') part.input.classList.add('mail-links');
      form.append(part.wrapper);
    });
    const send = node('button', 'mail-send', '保存原文'); send.type = 'submit'; form.append(send);
    const library = node('div', 'mail-paper');
    const filters = node('form', 'mail-filters');
    const count = node('p', 'mail-count');
    const list = node('div', 'mail-records');
    const room = {kind, root, status, form, filters, list, count};
    [['搜索标题和全文', 'query'], ['从', 'date_from', 'date'], ['至', 'date_to', 'date']].forEach(args => filters.append(field(...args).wrapper));
    const migrate = button('迁移散步信件', () => busy(room, migrate, async () => {
      const preview = await api('/api/postcards/migrate-letters');
      if (!preview.matched) { status.textContent = '没有尚待迁移的「Senn 的散步 ·」信件。'; return; }
      if (!confirm(`找到 ${preview.matched} 封散步信件。复制并逐字节核验后，从 Letters 移除原件；原日期和全文不变。继续？`)) return;
      const result = await api('/api/postcards/migrate-letters', {confirm:true});
      await refresh(room);
      status.textContent = `迁移完成：${result.removed} 封，原文核验通过。`;
    }));
    heading.append(migrate);
    const search = node('button', '', '查找 / 刷新'); search.type = 'submit'; filters.append(search);
    filters.addEventListener('submit', event => { event.preventDefault(); busy(room, search, () => refresh(room)); });
    form.addEventListener('submit', event => {
      event.preventDefault();
      busy(room, send, async () => {
        const payload = Object.fromEntries(new FormData(form));
        payload.links = payload.links.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
        await api('/api/postcards', payload);
        form.elements.content.value = '';
        await refresh(room);
        status.textContent = '已永久保存';
      });
    });
    library.append(filters, count, list); layout.append(form, library); section.append(status, layout); root.append(section);
    rooms.set(kind, room); return room;
  }
  window.loadCorrespondence = kind => {
    if (!['postcard', 'bottle'].includes(kind)) return;
    const room = rooms.get(kind) || build(kind);
    return busy(room, null, () => refresh(room));
  };
})();
