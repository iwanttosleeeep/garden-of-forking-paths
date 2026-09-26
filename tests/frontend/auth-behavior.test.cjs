const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');

const html = readFileSync(join(__dirname, '../../frontend/dashboard.html'), 'utf8');
const source = html.slice(html.indexOf('async function checkAuth()'), html.indexOf('async function doLogout()'));

function setup(fetch, tab = 'list') {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, { style: {}, value: 'password', textContent: '' });
    return elements.get(id);
  };
  element('auth-overlay').style.display = 'flex';
  const calls = { buckets: 0, tab: 0 };
  const sandbox = vm.createContext({ fetch, document: {
    getElementById: element,
    querySelector: () => ({ dataset: { tab }, click() { calls.tab++; } }),
  }, loadBuckets() { calls.buckets++; } });
  vm.runInContext(source, sandbox);
  return { sandbox, element, calls };
}

test('an unavailable authentication endpoint keeps the login screen visible', async () => {
  for (const fetch of [
    async () => { throw new Error('offline'); },
    async () => ({ ok: false, status: 503, json: async () => ({}) }),
    async () => ({ ok: true, json: async () => { throw new SyntaxError('not JSON'); } }),
  ]) {
    const h = setup(fetch);
    assert.equal(await h.sandbox.checkAuth(), false);
    assert.equal(h.element('auth-overlay').style.display, 'flex');
    assert.equal(h.element('auth-login-form').style.display, 'block');
    assert.match(h.element('auth-error').textContent, /无法确认登录状态/);
  }
});

test('authentication succeeds only on a confirmed session', async () => {
  const h = setup(async () => ({ ok: true, json: async () => ({ authenticated: true }) }));
  assert.equal(await h.sandbox.checkAuth(), true);
  assert.equal(h.element('auth-overlay').style.display, 'none');
});

test('an expired session or setup requirement redisplays a hidden login overlay', async () => {
  for (const data of [{ authenticated: false }, { setup_needed: true }]) {
    const h = setup(async () => ({ ok: true, json: async () => data }));
    h.element('auth-overlay').style.display = 'none';
    assert.equal(await h.sandbox.checkAuth(), false);
    assert.equal(h.element('auth-overlay').style.display, 'flex');
    const form = data.setup_needed ? 'auth-setup-form' : 'auth-login-form';
    assert.equal(h.element(form).style.display, 'block');
  }
});

test('login preserves server rate-limit messages and handles offline requests', async () => {
  const h = setup(async () => ({ ok: false, json: async () => ({ error: '请 30 秒后再试' }) }));
  await h.sandbox.doLogin();
  assert.equal(h.element('auth-error').textContent, '请 30 秒后再试');
  assert.equal(h.element('auth-overlay').style.display, 'flex');
  h.sandbox.fetch = async () => { throw new Error('offline'); };
  await h.sandbox.doLogin();
  assert.match(h.element('auth-error').textContent, /无法连接 Garden/);
});

test('successful login refreshes memos and the bookmarked tab and clears the password', async () => {
  for (const tab of ['list', 'radio', 'reading']) {
    const h = setup(async () => ({ ok: true }), tab);
    await h.sandbox.doLogin();
    assert.equal(h.element('auth-overlay').style.display, 'none');
    assert.equal(h.element('auth-login-pwd').value, '');
    assert.equal(h.calls.buckets, 1);
    assert.equal(h.calls.tab, tab === 'list' ? 0 : 1);
  }
});
