const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const read = name => readFileSync(join(__dirname, '../../frontend', name), 'utf8');

test('new mail UI parses and only loads a section on explicit selection', () => {
  const source = read('correspondence.js');
  assert.doesNotThrow(() => new vm.Script(source));
  const calls = [];
  const sandbox = {window:{}, document:new Proxy({}, {get(_target, name) {calls.push(name);}})};
  vm.runInNewContext(source, sandbox);
  assert.equal(typeof sandbox.window.loadCorrespondence, 'function');
  assert.deepEqual(calls, []);
  assert.doesNotMatch(source, /\.innerHTML\s*=/);
  assert.match(source, /textContent = text/);
});

test('map locations, branch origins and deep links match the marked reference', () => {
  const map = read('front-page.html');
  assert.match(map, /data-trail="postcard" d="M 992 480 /);
  assert.match(map, /data-trail="bottle" d="M 720 268 /);
  assert.match(map, /href="\/garden#postcard"[^>]*left:1035px; top:371px/);
  assert.match(map, /href="\/garden#bottle"[^>]*left:545px; top:110px/);
  const dashboard = read('dashboard.html');
  for (const name of ['postcard', 'bottle']) {
    assert.ok(dashboard.includes(`data-tab="${name}"`));
    assert.ok(dashboard.includes(`id="${name}-view"`));
  }
  // Initial deep links execute in the inline dashboard script: load the tiny
  // standalone mail script first, rather than deferring it until after parsing.
  assert.match(dashboard, /<script src="\/static\/correspondence.js"><\/script>/);
});

function luminance(hex) {
  const values = hex.match(/[a-f0-9]{2}/gi).map(s => parseInt(s,16)/255)
    .map(n => n <= .04045 ? n/12.92 : ((n+.055)/1.055)**2.4);
  return values[0]*.2126+values[1]*.7152+values[2]*.0722;
}
test('bottle trail is a single smooth arc without an S-bend', () => {
  const path = read('front-page.html').match(/data-trail="bottle" d="([^"]+)"/)[1];
  // One quadratic segment has no inflection; preserve the branch and icon endpoints.
  assert.equal(path, 'M 720 268 Q 710 105, 545 110');
});
test('both new sector colors are distinct with readable light labels', () => {
  const colors = ['A65442','75647E'];
  const existing = ['E8A33D','D96C2C','8A9A3B','D9A8A0','B8860B','7B3F1E','A8B0A0','7FCDBB','8A8065','6E9486','2F4F4F','577183'];
  for (const color of colors) {
    assert.ok(!existing.includes(color));
    const contrast = (luminance('FFFDF5')+.05)/(luminance(color)+.05);
    assert.ok(contrast >= 4.5, `${color} contrast ${contrast}`);
  }
});
