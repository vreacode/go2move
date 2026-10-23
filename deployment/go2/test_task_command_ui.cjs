/* Actual command component with fake React/browser/HTTP only; no microphone or Go2. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { Blob } = require('node:buffer');
const { createRequire } = require('node:module');
const web = '/home/unitree/go2move/apps/nano-deploy/dashboard_platform/web';
const req = createRequire(path.join(web, 'package.json'));
const ts = req('typescript');
const compiled = ts.transpileModule(fs.readFileSync(path.join(web, 'src/components/Go2TaskCommand.tsx'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 }
}).outputText;
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const flush = async () => { for (let i = 0; i < 5; i++) await new Promise(resolve => setImmediate(resolve)); };
const media = label => { const track = { stopped: 0, stop() { this.stopped++; } }; return { label, track, getTracks: () => [track] }; };

function browser() {
  const hooks = [], pendingEffects = [], timers = new Map();
  const permissions = [], requests = [], recorders = [], contexts = [], decodeQueue = [];
  let cursor = 0, dirty = false, mounted = true, tree, timerId = 0, writesAfterUnmount = 0;
  let props = { token: 'token-a', onChanged: () => { counters.changed++; }, onCapture: () => { counters.capture++; } };
  const counters = { changed: 0, capture: 0 };
  const react = {
    useState(initial) {
      const id = cursor++;
      if (!hooks[id]) hooks[id] = { value: typeof initial === 'function' ? initial() : initial };
      return [hooks[id].value, value => {
        if (!mounted) writesAfterUnmount++;
        const next = typeof value === 'function' ? value(hooks[id].value) : value;
        if (!Object.is(next, hooks[id].value)) { hooks[id].value = next; dirty = true; }
      }];
    },
    useRef(initial) { const id = cursor++; if (!hooks[id]) hooks[id] = { current: initial }; return hooks[id]; },
    useEffect(effect, deps) {
      const id = cursor++; const previous = hooks[id];
      if (!previous || deps.some((value, index) => !Object.is(value, previous.deps[index]))) {
        hooks[id] = { deps, cleanup: previous?.cleanup };
        pendingEffects.push(() => { hooks[id].cleanup?.(); hooks[id].cleanup = effect(); });
      }
    }
  };
  class Recorder {
    constructor(stream) { this.stream = stream; this.state = 'inactive'; this.mimeType = 'audio/webm'; this.stops = 0; recorders.push(this); }
    start() { this.state = 'recording'; }
    stop() {
      this.state = 'inactive'; this.stops++;
      queueMicrotask(() => { this.ondataavailable?.({ data: new Blob(['synthetic audio']) }); this.onstop?.(); });
    }
    fail() { this.onerror?.(new Error('synthetic recorder failure')); }
  }
  class Audio {
    constructor() { this.destination = {}; contexts.push(this); }
    decodeAudioData() { const next = decodeQueue.shift(); return next ? next.promise : Promise.resolve({ duration: .2 }); }
    createBufferSource() { return { buffer: null, connect() {}, start() {} }; }
    startRendering() { return Promise.resolve({ getChannelData: () => new Float32Array(3200) }); }
  }
  const fakeWindow = { isSecureContext: true, setTimeout(callback, ms) { const id = ++timerId; timers.set(id, { callback, ms }); return id; }, clearTimeout(id) { timers.delete(id); } };
  const jsx = (type, props) => ({ type, props });
  const overrides = { react, 'react/jsx-runtime': { jsx, jsxs: jsx }, '@heroicons/react/24/outline': { MicrophoneIcon() {}, PaperAirplaneIcon() {} },
    '../lib': { apiFetch(url, token, options) { const result = deferred(); requests.push({ url, token, options, ...result }); return result.promise; } } };
  const module = { exports: {} };
  vm.runInNewContext(compiled, { module, exports: module.exports, require: key => overrides[key] || req(key),
    window: fakeWindow, navigator: { mediaDevices: { getUserMedia() { const result = deferred(); permissions.push(result); return result.promise; } } },
    MediaRecorder: Recorder, OfflineAudioContext: Audio, AbortController, Blob, Uint8Array, Float32Array, DataView,
    btoa: value => Buffer.from(value, 'binary').toString('base64'), console }, { filename: 'Go2TaskCommand.tsx' });
  function render(next = {}) {
    props = { ...props, ...next };
    do { dirty = false; cursor = 0; tree = module.exports.Go2TaskCommand(props); while (pendingEffects.length) pendingEffects.shift()(); } while (dirty);
    return tree;
  }
  function nodes(node) {
    if (!node || typeof node !== 'object') return [];
    const children = node.props?.children;
    return [node, ...(Array.isArray(children) ? children : [children]).flatMap(child => nodes(child))];
  }
  const find = (type, predicate = () => true) => nodes(tree).find(node => node.type === type && predicate(node.props));
  const input = () => find('input').props;
  const mic = () => find('button', props => props.type === 'button').props;
  const role = name => nodes(tree).find(node => node.props?.role === name)?.props.children;
  const change = text => { input().onChange({ target: { value: text } }); render(); };
  const record = () => { mic().onClick(); render(); };
  const send = () => { find('form').props.onSubmit({ preventDefault() {} }); render(); };
  const unmount = () => { mounted = false; for (const hook of hooks) hook?.cleanup?.(); };
  render();
  return { render, input, mic, role, change, record, send, unmount, permissions, requests, recorders, contexts,
    decodeQueue, timers, counters, get writesAfterUnmount() { return writesAfterUnmount; } };
}
async function granted(h, label = 'current') { h.record(); const owned = media(label); h.permissions.at(-1).resolve(owned); await flush(); h.render(); return owned; }
async function recorded(h) { const owned = await granted(h); h.record(); await flush(); h.render(); return owned; }
let passed = 0;
async function test(label, body) { await body(); passed++; console.log('PASS ' + label); }

(async () => {
  await test('token change resets recording and releases only its captured microphone', async () => {
    const h = browser(); const first = await granted(h, 'old'); const old = h.recorders[0];
    h.render({ token: 'token-b' });
    assert.ok(first.track.stopped); assert.equal(old.state, 'inactive'); assert.equal(old.onstop, null);
    assert.equal(h.input().disabled, false); assert.equal(h.mic().disabled, false);
    const second = await granted(h, 'new'); assert.equal(second.track.stopped, 0); assert.equal(h.recorders[1].state, 'recording');
    h.unmount(); assert.ok(second.track.stopped); assert.equal(h.writesAfterUnmount, 0);
  });
  await test('late getUserMedia from old token stops old tracks without touching newer recording', async () => {
    const h = browser(); h.record(); const oldPermission = h.permissions[0];
    h.render({ token: 'token-b' }); const newer = await granted(h, 'new');
    const older = media('late old'); oldPermission.resolve(older); await flush(); h.render();
    assert.ok(older.track.stopped); assert.equal(newer.track.stopped, 0); assert.equal(h.recorders.length, 1);
    assert.equal(h.recorders[0].state, 'recording'); assert.equal(h.input().disabled, true); assert.equal(h.role('alert'), undefined);
    assert.equal([...h.timers.values()].filter(timer => timer.ms === 8000).length, 1); h.unmount();
  });
  await test('late rejected microphone permission does not clear current permission busy state', async () => {
    const h = browser(); h.record(); const previous = h.permissions[0];
    h.render({ token: 'token-b' }); h.record(); previous.reject(new Error('old permission error'));
    await flush(); h.render(); assert.equal(h.input().disabled, true); assert.equal(h.mic().disabled, true); assert.equal(h.role('alert'), undefined);
    const current = media('new'); h.permissions[1].resolve(current); await flush(); h.render(); assert.equal(h.recorders.length, 1); h.unmount();
  });
  await test('token change during audio decoding discards old decoded data before any HTTP', async () => {
    const h = browser(); const decoding = deferred(); h.decodeQueue.push(decoding);
    await recorded(h); assert.equal(h.contexts.length, 1);
    h.render({ token: 'token-b' }); decoding.resolve({ duration: .2 }); await flush(); h.render();
    assert.equal(h.requests.length, 0); assert.equal(h.contexts.length, 1); assert.equal(h.input().disabled, false); h.unmount();
  });
  await test('late transcription cannot replace new text or release a current command request', async () => {
    const h = browser(); await recorded(h); const old = h.requests[0];
    assert.equal(old.url, '/api/go2/task/transcribe');
    h.render({ token: 'token-b' }); assert.equal(old.options.signal.aborted, true); h.change('新口令'); h.send();
    old.resolve({ text: '旧识别', reply: '旧结果' }); await flush(); h.render();
    assert.equal(h.input().value, '新口令'); assert.equal(h.input().disabled, true); assert.equal(h.role('status'), undefined);
    assert.equal(h.counters.changed, 0); h.requests[1].resolve({ intent: 'status', executed: true, reply: '新状态' });
    await flush(); h.render(); assert.equal(h.input().disabled, false); assert.equal(h.role('status'), '新状态'); assert.equal(h.counters.changed, 1); h.unmount();
  });
  await test('late command response from old token cannot trigger capture or clear a new command', async () => {
    const h = browser(); h.change('拍照'); h.send(); const old = h.requests[0];
    h.render({ token: 'token-b' }); h.change('状态'); h.send();
    old.resolve({ intent: 'capture', executed: false, reply: '旧拍照' }); await flush(); h.render();
    assert.equal(h.counters.capture, 0); assert.equal(h.input().disabled, true); assert.equal(h.role('status'), undefined);
    h.requests[1].resolve({ intent: 'status', executed: true, reply: '新状态' }); await flush(); h.render();
    assert.equal(h.counters.changed, 1); assert.equal(h.role('status'), '新状态'); h.unmount();
  });
  await test('recorder error then late stop never transcribes or releases the new recorder', async () => {
    const h = browser(); const older = await granted(h, 'old'); const active = h.recorders[0]; const lateStop = active.onstop;
    active.fail(); await flush(); h.render(); assert.ok(older.track.stopped); assert.equal(active.onstop, null); assert.match(h.role('alert'), /录音失败/);
    const newer = await granted(h, 'new'); lateStop(); await flush(); h.render();
    assert.equal(h.requests.length, 0); assert.equal(newer.track.stopped, 0); assert.equal(h.recorders[1].state, 'recording');
    assert.equal(h.input().disabled, true); assert.equal([...h.timers.values()].filter(timer => timer.ms === 8000).length, 1); h.unmount();
  });
  await test('unmount prevents state writes from late permissions and transcription results', async () => {
    const permission = browser(); permission.record(); const pending = permission.permissions[0]; permission.unmount();
    const late = media('after unmount'); pending.resolve(late); await flush(); assert.ok(late.track.stopped); assert.equal(permission.writesAfterUnmount, 0);
    const transcription = browser(); await recorded(transcription); const request = transcription.requests[0]; transcription.unmount();
    assert.equal(request.options.signal.aborted, true); request.resolve({ text: 'late', reply: 'late' }); await flush();
    assert.equal(transcription.writesAfterUnmount, 0);
  });
  await test('normal recording only fills recognized text; execution still requires manual send', async () => {
    const h = browser(); const owned = await recorded(h); assert.ok(owned.track.stopped);
    assert.equal(h.requests.length, 1); h.requests[0].resolve({ text: '开始找人', reply: '请检查再发送' }); await flush(); h.render();
    assert.equal(h.input().value, '开始找人'); assert.equal(h.input().disabled, false); assert.equal(h.counters.changed, 0); assert.equal(h.counters.capture, 0);
    h.send(); assert.equal(h.requests[1].url, '/api/go2/task/command'); h.requests[1].resolve({ intent: 'start', executed: true, reply: '已开始' });
    await flush(); h.render(); assert.equal(h.counters.changed, 1); h.unmount();
  });
  console.log(`${passed} task command UI regressions passed; fake browser/HTTP only.`);
})().catch(error => { console.error(error); process.exitCode = 1; });
