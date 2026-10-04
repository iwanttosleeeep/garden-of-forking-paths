const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const source = readFileSync(join(__dirname, '../../frontend/voice-bridge.js'), 'utf8');
const valid = {app:'stackchan-voice-lab', bridge_protocol:'garden-voice-v1'};
function harness(fetch) {
  const elements = new Map(), ready = [], events = [];
  const el = id => {
    if (!elements.has(id)) elements.set(id, {disabled:true, hidden:false, textContent:'',
      classList:{toggle(){}}, listeners:{}, addEventListener(event, fn){this.listeners[event]=fn;}});
    return elements.get(id);
  };
  const context = vm.createContext({fetch, Headers, AbortSignal, TypeError,
    document:{getElementById:el, querySelector:el, addEventListener:(_, fn)=>ready.push(fn)},
    async status(){}, async deliveryStatus(){}, voiceDisconnected(){events.push('disconnect');}});
  vm.runInContext(source, context); ready.forEach(fn=>fn());
  return {el, events, api:context.voiceFetch, connect:()=>el('bridge-connect').listeners.click()};
}
test('load and disconnected API calls never contact local services', async()=>{
  let calls=0; const h=harness(async()=>{calls++;});
  await assert.rejects(h.api('/api/stackchan/record', {method:'POST'}), /连接/);
  assert.equal(calls,0); assert.equal(h.el('voice-workspace').disabled,true);
});
test('explicit connection checks protocol; requests omit credentials and cannot change destination',async()=>{
  const calls=[]; const h=harness(async(url,opts)=>{calls.push({url,opts});return {ok:true,json:async()=>valid};});
  await h.connect();assert.equal(h.el('voice-workspace').disabled,false);
  await h.api('/api/voice/publish',{method:'POST',credentials:'include',redirect:'follow'});
  const last=calls.at(-1);
  assert.equal(last.url,'http://127.0.0.1:8765/api/voice/publish');
  assert.equal(last.opts.credentials,'omit');assert.equal(last.opts.redirect,'error');
  assert.equal(last.opts.headers.get('X-Garden-Voice'),'1');
  await assert.rejects(h.api('https://evil.example/api/status'), /允许/);
  assert.equal(calls.length,2);
});
test('old service, wrong service, HTTP errors and unavailable service stay disabled',async()=>{
  for(const fetch of [
    async()=>({ok:true,json:async()=>({app:'stackchan-voice-lab'})}),
    async()=>({ok:true,json:async()=>({...valid,app:'another-server'})}),
    async()=>({ok:false,json:async()=>valid}),
    async()=>{throw new TypeError('offline');},
  ]){const h=harness(fetch);await h.connect();assert.equal(h.el('voice-workspace').disabled,true);
    assert.equal(h.el('bridge-help').open,true);assert.equal(h.el('bridge-connect').disabled,false);}
});
test('network failure disables UI and never retries a recording or publication',async()=>{
  let calls=0;const h=harness(async()=>{if(++calls>1)throw new TypeError('offline');return {ok:true,json:async()=>valid};});
  await h.connect();await assert.rejects(h.api('/api/voice/publish',{method:'POST'}));
  assert.equal(calls,2);assert.equal(h.el('voice-workspace').disabled,true);
  assert.deepEqual(h.events,['disconnect']);
  await assert.rejects(h.api('/api/voice/publish',{method:'POST'}));assert.equal(calls,2);
});
test('disconnect closes a microphone without submitting its partial recording',()=>{
  const app=readFileSync(join(__dirname,'../../frontend/voice-app.js'),'utf8');
  const hook=app.slice(app.indexOf('function voiceDisconnected()'),app.indexOf("window.addEventListener('pagehide'"));
  const events=[];
  const recorder={state:'recording',onstop(){throw new Error('must not analyze');},stop(){assert.equal(this.onstop,null);events.push('stop');}};
  const context=vm.createContext({recorder,invalidate(){events.push('invalidate');},releaseMic(){events.push('release');},
    controls(locked){assert.equal(locked,false);events.push('unlock');},message(){}});
  vm.runInContext(hook,context);context.voiceDisconnected();
  assert.deepEqual(events,['invalidate','stop','release','unlock']);assert.equal(context.recorder,null);
});
