/* Actual web modules, fake browser/HTTP only: no Go2 services or native audio. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { createRequire } = require("node:module");
const web = "/home/unitree/go2move/apps/nano-deploy/dashboard_platform/web";
const req = createRequire(path.join(web, "package.json"));
const ts = req("typescript");
const { renderToStaticMarkup } = req("react-dom/server");
function load(name, overrides = {}, globals = {}) {
  const source = fs.readFileSync(path.join(web, "src/components", name), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 } }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(compiled, { module, exports: module.exports, require: key => key in overrides ? overrides[key] : req(key), AbortController, Date, console, ...globals }, { filename: name });
  return module.exports;
}
const { LocalPersonSpeech } = load("localPersonSpeech.ts");
const sample = (id = "old", time = 99, target = "t", active = true) => ({ active, target: { id: target, name: "测试人物" },
  latest_event: { id, target_id: target, target_name: "测试人物", found_at: time, last_seen_at: time, source: "live_camera" },
  last: { captured_at: time, suspected_target: true } });
let passed = 0;
function test(label, body) { body(); passed++; console.log(`PASS ${label}`); }

test("default off / refresh baseline does not replay", () => {
  const manager = new LocalPersonSpeech(() => {});
  assert.equal(manager.next(sample(), 100), null);
  assert.equal(manager.next(sample("new", 101), 101), null);
  manager.enable(true, null, 102);
  assert.equal(manager.next(sample("freshly loaded", 103), 103), null);
});
test("enabling skips old history / first new event once / another new event", () => {
  const manager = new LocalPersonSpeech(() => {});
  manager.enable(true, sample(), 100);
  assert.equal(manager.next(sample(), 100), null);
  assert.match(manager.next(sample("new", 101), 101), /发现疑似测试人物/);
  assert.equal(manager.next(sample("new", 101), 102), null);
  assert.match(manager.next(sample("next", 103), 103), /现场确认/);
});
test("old history, stale camera, stale evidence, wrong source rejected", () => {
  const manager = new LocalPersonSpeech(() => {});
  manager.enable(true, sample(), 100);
  const history = sample("late history", 99); history.latest_event.last_seen_at = 101; history.last.captured_at = 101;
  assert.equal(manager.next(history, 101), null);
  const camera = sample("stale camera", 101); camera.last.captured_at = 90;
  assert.equal(manager.next(camera, 101), null);
  const evidence = sample("stale evidence", 101); evidence.latest_event.last_seen_at = 90;
  assert.equal(manager.next(evidence, 101), null);
  const source = sample("not live", 101); source.latest_event.source = "history";
  assert.equal(manager.next(source, 101), null);
});
test("target changes / stop / disabled cancel queues without replay", () => {
  let canceled = 0; const manager = new LocalPersonSpeech(() => canceled++);
  manager.enable(true, sample(), 100);
  assert.match(manager.next(sample("new", 101), 101), /发现/);
  assert.equal(manager.next(sample("other", 102, "other-target"), 102), null);
  assert.match(manager.next(sample("other-new", 103, "other-target"), 103), /发现/);
  assert.equal(manager.next(sample("other-new", 103, "other-target", false), 104), null);
  assert.equal(manager.next(sample("other-new", 103, "other-target"), 105), null);
  assert.match(manager.next(sample("after-stop", 106, "other-target"), 106), /发现/);
  manager.enable(false, sample("after-stop", 106, "other-target"), 107);
  assert.equal(manager.next(sample("off-new", 108, "other-target"), 108), null);
  manager.dispose(); assert.ok(canceled >= 4);
});
test("pending target/stop clears queue and prevents older pending event", () => {
  let canceled = 0; const manager = new LocalPersonSpeech(() => canceled++);
  manager.enable(true, sample(), 100); manager.quiet(sample("pending", 101), 102);
  assert.equal(manager.next(sample("pending", 101), 103), null);
  const delayed = sample("created while pending", 101); delayed.latest_event.last_seen_at = 103; delayed.last.captured_at = 103;
  assert.equal(manager.next(delayed, 103), null); assert.equal(canceled, 2);
});

const now = Date.now();
const healthy = { connected: true, sampled_at: now / 1000, age_s: 0,
  max_temperature_c: 84, rear_left_hip_temperature_c: 83, body_height_m: .13, soc: 60,
  faults: [{ index: 9, joint: "RL_0", code_hex: "0x4", messages: ["电机故障"] }], motion_allowed: false, reason: "温度与事故暂停" };
function staticHealth(health, receivedAt = now) {
  let index = 0;
  const fakeReact = { useState: initial => [index++ === 0 ? { health, receivedAt } : now, () => {}], useEffect: () => {} };
  const { Go2HealthHint } = load("Go2PersonFeedback.tsx", { react: fakeReact, "../lib": { classNames: (...items) => items.filter(Boolean).join(" ") }, "./localPersonSpeech": { LocalPersonSpeech } });
  return renderToStaticMarkup(Go2HealthHint({ token: "fake" }));
}
test("fault is red with distinct real left rear hip temperature", () => {
  const html = staticHealth(healthy);
  assert.ok(html.includes("has-fault")); assert.ok(html.includes("左后髋 83°C"));
  assert.ok(html.includes("最高 84°C")); assert.ok(html.includes("左后髋 0x4")); assert.ok(html.includes("温度与事故暂停"));
});
test("disconnected / aged / future invalid data never show retained temperatures", () => {
  for (const [health, receivedAt] of [[{ ...healthy, connected: false }, now], [{ ...healthy, age_s: 12 }, now], [healthy, now - 11000], [{ ...healthy, sampled_at: null }, now]]) {
    const html = staticHealth(health, receivedAt);
    assert.ok(html.includes("健康状态离线")); assert.ok(html.includes("未收到新鲜数据"));
    assert.ok(!html.includes("83°C")); assert.ok(!html.includes("84°C")); assert.ok(!html.includes("0x4"));
  }
});

(async () => {
  let index = 0, latestSample, cleanup, fail = false;
  const intervals = [], requests = [];
  const fakeReact = { useState: initial => { const i = index++; return [typeof initial === "function" ? initial() : initial, value => { if (i === 0) latestSample = value; }]; },
    useEffect: effect => { cleanup = effect(); } };
  const fakeWindow = { setTimeout: () => 1, clearTimeout: () => {}, setInterval: (callback, ms) => { intervals.push({ callback, ms }); return intervals.length; }, clearInterval: () => {} };
  const { Go2HealthHint } = load("Go2PersonFeedback.tsx", { react: fakeReact, "./localPersonSpeech": { LocalPersonSpeech }, "../lib": {
    classNames: (...items) => items.filter(Boolean).join(" "), async apiFetch(url, token, options = {}) { requests.push({ url, token, method: options.method || "GET" }); if (fail) throw new Error("offline"); return healthy; }
  } }, { window: fakeWindow });
  Go2HealthHint({ token: "fake-token" }); await new Promise(resolve => setImmediate(resolve));
  assert.equal(latestSample.health, healthy); assert.ok(intervals.some(item => item.ms === 5000));
  fail = true; intervals.find(item => item.ms === 5000).callback(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(latestSample, null); assert.ok(requests.every(item => item.url === "/api/go2/health" && item.method === "GET" && item.token === "fake-token"));
  cleanup(); passed++; console.log("PASS 5-second authenticated read-only polling clears failed health");

  const jsxRuntime = req("react/jsx-runtime");
  const voiceStatus = enabled => ({ voice_policy: { enabled, reason: "isolated voice policy" },
    camera: { connected: true }, audio: { connected: true }, asr: { model_available: true, offline: true }, motion_enabled: false });
  function mountVoice(initialStatus, failStatus = false, initialPending = "", initialResult = null) {
    const requests = [], buttons = [], selects = [], changes = [];
    let stateIndex = 0;
    const voiceReact = { useState: initial => { const slot = stateIndex++;
      return [slot === 0 ? initialStatus : slot === 4 ? initialPending : slot === 6 ? initialResult : initial,
        value => changes.push({ slot, value })]; }, useEffect: effect => effect() };
    const wrapJsx = runtime => (type, props, key) => {
      if (type === "button") buttons.push(props);
      if (type === "select") selects.push(props);
      return runtime(type, props, key);
    };
    const { Go2VoicePanel } = load("Go2VoicePanel.tsx", { react: voiceReact,
      "react/jsx-runtime": { jsx: wrapJsx(jsxRuntime.jsx), jsxs: wrapJsx(jsxRuntime.jsxs) },
      "../lib": { async apiFetch(url, token, options = {}) {
        requests.push({ url, token, method: options.method || "GET", body: options.body });
        if (url.endsWith("/status")) { if (failStatus) throw new Error("service stopped"); return initialStatus; }
        if (url.endsWith("/select")) return { ...initialStatus.tts, voice_id: "kokoro_zf002", voice_label: "中文女声 002" };
        if (url.endsWith("/pack/import")) return { imported_count: 4, played: false };
        return { text: "拍照", intent: "transcription", executed: false, reply: "拍照" };
      }, classNames: (...items) => items.filter(Boolean).join(" ") },
      "./Panel": { Panel: ({ title, subtitle, children }) => jsxRuntime.jsxs("section", { children: [title, subtitle, children] }) }
    }, { window: fakeWindow });
    return { requests, buttons, selects, changes, html: renderToStaticMarkup(Go2VoicePanel({ token: "fake-token" })) };
  }
  const held = mountVoice(voiceStatus(false));
  const voiceHtml = held.html;
  assert.ok(voiceHtml.includes("机身音频暂停")); assert.ok(voiceHtml.includes("不会自动恢复"));
  assert.ok(voiceHtml.includes("已暂停"));
  assert.ok(held.buttons.length >= 7 && held.buttons.every(button => button.disabled));
  held.buttons.find(button => Array.isArray(button.children) && button.children.includes("机身麦克风收听 5 秒")).onClick();
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(held.requests.length > 0 && held.requests.every(request => request.url === "/api/go2/voice/status" && request.method === "GET"));
  passed++; console.log("PASS held native audio: only status GET, every action remains disabled");

  const enabled = mountVoice(voiceStatus(true));
  assert.ok(enabled.html.includes("机身语音助手"));
  const listenButton = enabled.buttons.find(button => Array.isArray(button.children) && button.children.includes("机身麦克风收听 5 秒"));
  assert.equal(listenButton.disabled, false); listenButton.onClick();
  await new Promise(resolve => setImmediate(resolve));
  const listenRequest = enabled.requests.find(request => request.url === "/api/go2/voice/listen");
  assert.equal(listenRequest.method, "POST");
  assert.deepEqual(JSON.parse(listenRequest.body), { seconds: 5, execute: false, speak: false });
  assert.ok(enabled.changes.some(change => change.slot === 2 && change.value === "拍照"));
  assert.ok(enabled.requests.every(request => !request.url.endsWith("/command") && !request.url.endsWith("/say")));
  passed++; console.log("PASS enabled microphone transcribes to editable text without executing or speaking");

  for (const [phase, label] of [["connecting", "正在准备机身麦克风"], ["recording", "正在收听，请对机器狗说话"], ["recognizing", "收听已结束，正在识别"]]) {
    const capture = mountVoice({ ...voiceStatus(true), capture: { phase } }, false, "listen");
    assert.ok(capture.html.includes(label));
    assert.ok(capture.html.includes("声音来自机器狗身上的麦克风"));
    await new Promise(resolve => setImmediate(resolve));
    assert.ok(capture.requests.every(request => request.url.endsWith("/status") && request.method === "GET"));
  }
  assert.ok(intervals.some(item => item.ms === 500));
  passed++; console.log("PASS server capture phases distinguish preparing, actual listening, and recognition; only read polling");
  const unclear = mountVoice(voiceStatus(true), false, "", { reply:"没有听清", listen_hint:"人声可能被背景声盖住，请靠近机器狗" });
  assert.ok(unclear.html.includes("人声可能被背景声盖住"));
  passed++; console.log("PASS unclear capture shows the backend microphone hint");

  const tts = { voice_id: "kokoro_zf001", voice_label: "中文女声 001", engine: "kokoro", model_available: true,
    voices: [{ id: "kokoro_zf001", label: "中文女声 001", available: true },
      { id: "kokoro_zf002", label: "中文女声 002", available: true }] };
  const selected = mountVoice({ ...voiceStatus(true), tts });
  selected.selects[0].onChange({ target: { value: "kokoro_zf002" } });
  await new Promise(resolve => setImmediate(resolve));
  const selection = selected.requests.find(request => request.url.endsWith("/select"));
  assert.equal(selection.method, "POST");
  assert.deepEqual(JSON.parse(selection.body), { voice_id: "kokoro_zf002" });
  assert.ok(selected.changes.some(change => change.slot === 6 && change.value?.reply?.includes("中文女声 002")));
  assert.ok(selected.requests.every(request => !request.url.endsWith("/say") && !request.url.endsWith("/command")));
  passed++; console.log("PASS voice selection saves the chosen voice without speech or commands");

  const imported = mountVoice({ ...voiceStatus(true), tts });
  imported.buttons.find(button => button.children === "导入常用提示音到机身").onClick();
  await new Promise(resolve => setImmediate(resolve));
  const packRequest = imported.requests.find(request => request.url.endsWith("/pack/import"));
  assert.equal(packRequest.method, "POST");
  assert.deepEqual(JSON.parse(packRequest.body), {});
  assert.ok(imported.changes.some(change => change.slot === 6 && change.value?.reply?.includes("4 条")));
  assert.ok(imported.requests.every(request => !request.url.endsWith("/say") && !request.url.endsWith("/command")));
  const heldTts = mountVoice({ ...voiceStatus(false), tts });
  assert.equal(heldTts.selects[0].disabled, true);
  assert.ok(heldTts.buttons.every(button => button.disabled));
  passed++; console.log("PASS prompt import reports saved clips without speech; paused audio disables voice controls");

  const disconnected = mountVoice(null, true);
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(disconnected.buttons.every(button => button.disabled));
  assert.ok(disconnected.changes.some(change => change.slot === 1 && change.value.includes("语音服务暂不可用")));
  passed++; console.log("PASS unavailable service keeps actions disabled and reports status failure");
  console.log(`${passed} person feedback UI cases passed; fake HTTP/browser only.`);
})().catch(error => { console.error(error); process.exitCode = 1; });
