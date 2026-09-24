// Run production network functions and registered event handlers without a backend.
// Canvas calls are recorded; visual presentation is also checked in a browser.
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');

const html = readFileSync(join(__dirname, '../../frontend/dashboard.html'), 'utf8');
const source = html.slice(html.indexOf('var networkData = null;'), html.indexOf('function esc(s)'));

test('all inline dashboard scripts parse completely', () => {
  for (const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) {
    new vm.Script(match[1]);
  }
});

function setup() {
  const elements = new Map(), frames = new Map(), lines = [];
  let serial = 0, path = [];
  const ctx = new Proxy({}, { get(target, key) {
    if (key in target) return target[key];
    if (key === 'createRadialGradient' || key === 'createLinearGradient') return () => ({ addColorStop() {} });
    if (key === 'beginPath') return () => { path = []; };
    if (key === 'moveTo' || key === 'lineTo') return (x, y) => path.push([x, y]);
    if (key === 'stroke') return () => { if (path.length === 2) lines.push(path); };
    return () => {};
  }});
  function element(id) {
    const listeners = {};
    const el = {
      id, style: {}, textContent: '', value: '', disabled: false, offsetWidth: 1000, offsetHeight: 600,
      classList: { add() {}, remove() {} },
      addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
      fire(type, event = {}) { for (const fn of listeners[type] || []) fn.call(el, event); },
      listenerCount(type) { return (listeners[type] || []).length; },
      getBoundingClientRect() { return { left: 0, top: 0 }; }, getContext() { return ctx; },
      set innerHTML(value) {
        for (const match of value.matchAll(/id="([^"]+)"/g)) element(match[1]);
      },
    };
    if (id) elements.set(id, el);
    return el;
  }
  const canvas = element('network-canvas');
  canvas.parentElement = { insertBefore(el) { elements.set(el.id, el); } };
  element('network-terminal'); element('network-mode').value = 'fernweh';
  const sandbox = vm.createContext({
    document: { getElementById: id => elements.get(id), createElement: () => element(),
      body: { appendChild(el) { elements.set(el.id, el); } }, documentElement: {} },
    window: { devicePixelRatio: 2 }, performance: { now: () => 0 },
    getComputedStyle: () => ({ getPropertyValue: () => '' }), BASE: '',
    requestAnimationFrame(fn) { frames.set(++serial, fn); return serial; },
    cancelAnimationFrame(id) { frames.delete(id); }, esc: s => String(s), _SV: { anchor: '' },
  });
  vm.runInContext(source, sandbox);
  function tick() {
    lines.length = 0;
    const callbacks = [...frames.values()]; frames.clear();
    callbacks.forEach(fn => fn(100));
  }
  function start(data, mode = 'fernweh') {
    sandbox.stopNetwork(); sandbox.networkData = data;
    sandbox[mode === 'fernweh' ? 'startFernweh' : 'initConceptNetwork'](canvas, ctx, 1000, 600, data);
    tick();
  }
  function depth(value) {
    const slider = elements.get('depth-slider'); slider.value = String(value); slider.fire('input'); tick();
  }
  function click(id) {
    const p = sandbox.networkState.positions[id];
    canvas.fire('click', { clientX: p.x, clientY: p.y }); tick();
  }
  return { sandbox, elements, canvas, frames, lines, tick, start, depth, click };
}

function chain(count = 6) {
  return {
    nodes: Array.from({ length: count }, (_, i) => ({ id: String(i), name: `Memory ${i}`, importance: 10 - i / 100, score: 1 })),
    edges: Array.from({ length: count - 1 }, (_, i) => ({ source: String(i), target: String(i + 1), weight: 0.9 })),
  };
}
const visible = h => [...h.sandbox.networkState.visibleNodes].sort();

test('dragging in overview selects an explicit center, then grows and shrinks real hop neighborhoods', () => {
  const h = setup(); h.start(chain());
  assert.equal(visible(h).length, 6);
  h.depth(1);
  assert.equal(h.sandbox.networkState.focusNode, '0');
  assert.deepEqual(visible(h), ['0', '1']);
  assert.equal(h.sandbox.networkState.positions['0'].x, 500);
  assert.equal(h.sandbox.networkState.positions['0'].y, 300);
  h.depth(4); assert.deepEqual(visible(h), ['0', '1', '2', '3', '4']);
  h.depth(2); assert.deepEqual(visible(h), ['0', '1', '2']);
  assert.match(h.elements.get('network-depth-hint').textContent, /Memory 0/);
  assert.equal(h.elements.get('network-reset').disabled, false);
});

test('click re-roots the graph; clicking the center or reset restores every star and its original position', () => {
  const h = setup(); h.start(chain(40));
  const overview = JSON.stringify(h.sandbox.networkState.positions);
  h.depth(2); h.click('2');
  assert.equal(h.sandbox.networkState.focusNode, '2');
  assert.deepEqual(visible(h), ['0', '1', '2', '3', '4']);
  h.click('2');
  assert.equal(h.sandbox.networkState.focusNode, null);
  assert.equal(visible(h).length, 40);
  assert.equal(JSON.stringify(h.sandbox.networkState.positions), overview);
  h.depth(4); h.elements.get('network-reset').fire('click'); h.tick();
  assert.equal(JSON.stringify(h.sandbox.networkState.positions), overview);
});

test('every rendered constellation edge is traversable and every traversable edge is drawn', () => {
  const h = setup(), data = chain(8);
  // Dense weak links used to remain visible while being excluded from traversal.
  for (let i = 0; i < 8; i++) for (let j = i + 2; j < 8; j++) {
    data.edges.push({ source: String(i), target: String(j), weight: 0.7 });
  }
  h.start(data);
  const positions = h.sandbox.networkState.positions;
  const drawn = new Set(h.lines.map(line => JSON.stringify(line)));
  let directed = 0;
  for (const [id, neighbors] of Object.entries(data._adj)) for (const nb of neighbors) {
    directed++;
    const a = positions[id], b = positions[nb];
    assert.ok(drawn.has(JSON.stringify([[a.x, a.y], [b.x, b.y]])) || drawn.has(JSON.stringify([[b.x, b.y], [a.x, a.y]])));
  }
  assert.equal(drawn.size, directed / 2);
});

test('isolated stars, exhausted neighborhoods and empty data explain why depth adds nothing', () => {
  const h = setup(); h.start(chain(1)); h.depth(4);
  assert.deepEqual(visible(h), ['0']);
  assert.match(h.elements.get('network-depth-hint').textContent, /暂无关联/);
  h.start(chain(2)); h.depth(1);
  assert.match(h.elements.get('network-depth-hint').textContent, /全部关联/);
  h.start({ nodes: [], edges: [] });
  assert.equal(h.elements.get('depth-slider').disabled, true);
  assert.equal(h.frames.size, 0);
});

test('switching modes does not duplicate handlers or lose the chosen depth', () => {
  const h = setup(); h.start(chain()); h.depth(3);
  h.start(chain(), 'concept');
  assert.equal(h.sandbox.networkState.focusNode, null);
  assert.equal(h.elements.get('depth-slider').value, 3);
  h.start(chain());
  assert.equal(h.canvas.listenerCount('click'), 1);
  assert.equal(h.elements.get('depth-slider').listenerCount('input'), 1);
  h.depth(1); assert.deepEqual(visible(h), ['0', '1']);
});

test('resizing preserves focus and click targets; overlapping targets choose the nearest star', () => {
  const h = setup(); h.start(chain()); h.depth(2);
  h.canvas.offsetWidth = 720; h.canvas.offsetHeight = 400; h.tick();
  assert.equal(h.sandbox.networkState.positions['0'].x, 360);
  assert.equal(h.sandbox.networkState.positions['0'].y, 200);
  h.click('0'); assert.equal(h.sandbox.networkState.focusNode, null);
  h.sandbox.networkState.positions = { '0': { x: 100, y: 100 }, '1': { x: 105, y: 100 } };
  assert.equal(h.sandbox.hitTest(100, 100, 720, 400), '0');
});

test('late fetch responses cannot replace a newer mode or restart animation after leaving the view', async () => {
  const h = setup(), pending = [];
  h.sandbox.fetch = () => new Promise(resolve => pending.push(resolve));
  const first = h.sandbox.loadNetwork();
  h.elements.get('network-mode').value = 'concept';
  const second = h.sandbox.loadNetwork();
  pending[1]({ ok: true, json: async () => chain() }); await second;
  pending[0]({ ok: true, json: async () => chain() }); await first;
  assert.equal(h.sandbox.networkState.mode, 'concept');
  assert.equal(h.frames.size, 1);
  const leaving = h.sandbox.loadNetwork(); h.sandbox.stopNetwork();
  pending[2]({ ok: true, json: async () => chain() }); await leaving;
  assert.equal(h.frames.size, 0);
});
