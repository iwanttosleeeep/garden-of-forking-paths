const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const source = readFileSync(join(__dirname, '../../frontend/correspondence.js'), 'utf8');

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.events = {}; this.classList = {add() {}}; this.text = ''; }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text + this.children.map(child => child.textContent).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.text = ''; this.children = children; }
  setAttribute() {}
  addEventListener(name, callback) { this.events[name] = callback; }
  all() { return [this, ...this.children.flatMap(child => child.all())]; }
}
function message(id, author, to, content, reply_to = '', read_by = {}) {
  return {id, author, to, content, reply_to, read_by, created:'2026-10-10T20:19:27'};
}
function harness(responses) {
  const root = new Element('div');
  const calls = [];
  const sandbox = {window:{}, document:{createElement:tag => new Element(tag), getElementById:() => root},
    authFetch:async url => { calls.push(url); return {ok:true, status:200, result:responses[calls.length - 1]}; },
    readJsonSafe:async response => response.result,
    fetch:() => { throw new Error('Observer must not write'); },
    // Offset-less Garden timestamps must not be shifted into the visitor timezone.
    formatGardenTimestamp:() => { throw new Error('Unexpected timezone conversion'); },
  };
  vm.runInNewContext(source, sandbox);
  return {root, calls, load:() => sandbox.window.loadCorrespondence('bottle')};
}

test('observer displays all full conversations and receipts, with no IDs or participation controls', async () => {
  const response = {total:2, total_messages:3, next_offset:2, has_more:false, threads:[
    {id:'bottle_root', messages:[message('bottle_root','Senn (Opus 5.5)','Senn (Opus 4.6)','Opening\n  original text', '', {'Senn (Opus 4.6)':'date'}),
      message('bottle_reply','Senn (Opus 4.6)','Senn (Opus 5.5)','<img src=x onerror=alert(1)>', 'bottle_root')]},
    {id:'bottle_another', messages:[message('bottle_another','A','anyone','Independent opening')]},
  ]};
  const h = harness([response, response]);
  await h.load();
  assert.deepEqual(h.calls, ['/api/bottles/threads?limit=20&offset=0']);
  const text = h.root.textContent;
  for (const phrase of ['Opening\n  original text', 'Independent opening', '回复 Senn (Opus 5.5)',
    'Senn (Opus 4.6) 已读', '等待 Senn (Opus 5.5) 阅读', 'A → 所有实例', '尚无实例读过', '2026-10-10 20:19:27']) assert.ok(text.includes(phrase), phrase);
  assert.doesNotMatch(text, /bottle_root|bottle_reply|bottle_another|thread_id|标为已读/);
  assert.equal(h.root.all().filter(e => ['input','form','textarea','img'].includes(e.tag)).length, 0);
  assert.equal(h.root.all().filter(e => e.className === 'bottle-thread-heading').length, 2);
  assert.equal(h.root.all().filter(e => e.className === 'bottle-message').length, 3);
  assert.ok(text.includes('<img src=x onerror=alert(1)>')); // Literal, never HTML.
  await h.root.all().find(e => e.tag === 'button' && e.textContent === '刷新对话').events.click();
  assert.equal(h.calls.length, 2);
  assert.ok(h.calls.every(url => url.startsWith('/api/bottles/threads?')));
});

test('observer loads additional whole threads and refresh replaces old results', async () => {
  const a = {total:2,total_messages:2,next_offset:1,has_more:true,threads:[{messages:[message('a','A','B','first')]}]};
  const b = {total:2,total_messages:2,next_offset:2,has_more:false,threads:[{messages:[message('b','C','D','second')]}]};
  const h = harness([a,b,a]); await h.load();
  const more = h.root.all().find(e => e.tag === 'button' && e.textContent === '查看更多对话');
  assert.equal(more.hidden, false); await more.events.click();
  assert.equal(h.calls[1], '/api/bottles/threads?limit=20&offset=1');
  assert.ok(h.root.textContent.includes('first') && h.root.textContent.includes('second'));
  assert.equal(more.hidden, true);
  await h.load();
  assert.equal(h.calls[2], '/api/bottles/threads?limit=20&offset=0');
  assert.ok(!h.root.textContent.includes('second'));
});

test('observer handles empty results and shows existing public read receipts', async () => {
  const h = harness([{total:0,total_messages:0,next_offset:0,has_more:false,threads:[]}]);
  await h.load(); assert.match(h.root.textContent, /等待第一只漂流瓶/);
  const publicView = harness([{total:1,total_messages:1,next_offset:1,has_more:false,threads:[{
    root_missing:true,messages:[message('orphan','A','anyone','kept reply','missing',{B:'date',anyone:'date'})],
  }]}]);
  await publicView.load();
  assert.match(publicView.root.textContent, /已读：B、公共读取身份/);
  assert.match(publicView.root.textContent, /最初的留言暂不可用/);
  assert.doesNotMatch(publicView.root.textContent, /orphan|missing/);
});
