/* Actual person TSX with isolated fake HTTP/browser. No robot or native audio. */
const assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const { createRequire } = require("node:module");
const web = "/home/unitree/go2move/apps/nano-deploy/dashboard_platform/web";
const req = createRequire(path.join(web, "package.json"));
const ts = req("typescript"), { renderToStaticMarkup } = req("react-dom/server");
const fixed = Date.now(); class FakeDate extends Date { static now() { return fixed; } }
function load(name, overrides, globals = {}) {
  const compiled = ts.transpileModule(fs.readFileSync(path.join(web, "src/components", name), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 } }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(compiled, { module, exports: module.exports, require: key => key in overrides ? overrides[key] : req(key), AbortController, Date: FakeDate, console, ...globals }, { filename: name });
  return module.exports;
}
const jsx = req("react/jsx-runtime");
const now = fixed / 1000;
const target = { id: "target-a", name: "红衣候选", kind: "description", description: "找穿红色上衣的人", color: "red", color_label: "红" };
const event = { id: "event-a", target_id: target.id, target_name: target.name, target_kind: "description", target_description: target.description,
  color_label: "红", score_kind: "color_fraction", similarity: .8, found_at: now, last_seen_at: now, source: "live_camera", review_status: "suspected" };
const status = { models_ready: true, active: false, target, camera: { connected: true }, search_task: null };
const fakeWindow = { setTimeout: () => 1, clearTimeout: () => {}, setInterval: () => 1, clearInterval: () => {} };
const style = (...args) => args.filter(Boolean).join(" ");
function text(node) {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (!node || typeof node !== "object") return "";
  const children = node.props?.children;
  return (Array.isArray(children) ? children : [children]).map(text).join("");
}
function walk(node, output = []) {
  if (!node || typeof node !== "object") return output;
  output.push(node);
  const children = node.props?.children;
  for (const child of Array.isArray(children) ? children : [children]) Array.isArray(child) ? child.forEach(item => walk(item, output)) : walk(child, output);
  return output;
}
function fixture({ current = status, records = [event], mode = "description", description = target.description, postFailure = false } = {}) {
  let index = 0, localClears = 0;
  const requests = [], changes = [], fetched = [], anchors = [], urls = [];
  const values = { 0: current, 1: records, 2: true, 15: mode, 16: description };
  const fakeReact = { useState(initial) { const i = index++; return [i in values ? values[i] : typeof initial === "function" ? initial() : initial,
    value => changes.push({ index: i, value })]; }, useRef: value => ({ current: value }), useEffect: () => {} };
  const fakeLib = { API_BASE: "", classNames: style, async apiFetch(url, token, options = {}) {
    requests.push({ url, token, method: options.method || "GET", body: options.body ? JSON.parse(options.body) : undefined });
    if (postFailure && options.method === "POST") throw new Error('{"detail":"暂不支持这种描述"}');
    return url.includes("/history?") ? { items: records, total: records.length } : url.endsWith("/status") ? current : { target };
  } };
  const Dummy = () => null;
  const Command = () => null;
  const feedback = { Go2HealthHint: Dummy, useLocalPersonSpeech: () => ({ enabled: false, supported: true, error: "", toggle: () => {}, clear: () => localClears++ }) };
  const recordsModule = load("Go2PersonRecords.tsx", { react: fakeReact, "../lib": fakeLib }, { window: fakeWindow });
  const { Go2PersonPanel } = load("Go2PersonPanel.tsx", { react: fakeReact, "../lib": fakeLib, "./Go2PersonFeedback": feedback,
    "./Go2PatrolPanel": { Go2PatrolPanel: Dummy }, "./Go2PersonRecords": recordsModule, "./Go2TaskCommand": { Go2TaskCommand: Command },
    "./Go2FollowPanel": { Go2FollowPanel: Dummy }, "./Go2TargetLibrary": { Go2TargetLibrary: Dummy },
    "./Panel": { Panel: props => jsx.jsxs("section", { children: [props.title, props.subtitle, props.children] }) }
  }, { window: fakeWindow, URL: { createObjectURL: () => { urls.push("blob:fake-export"); return "blob:fake-export"; }, revokeObjectURL: () => {} },
    fetch: async (url, options) => { fetched.push({ url, options }); return { ok: true, blob: async () => ({ type: "application/json" }) }; },
    document: { createElement: () => { const a = { click: () => { a.clicked = true; }, remove: () => { a.removed = true; } }; anchors.push(a); return a; }, body: { appendChild: () => {} } } });
  const tree = Go2PersonPanel({ token: "fake-auth" });
  const nodes = walk(tree), html = renderToStaticMarkup(tree);
  const button = label => nodes.find(node => node.type === "button" && text(node) === label);
  return { tree, nodes, html, button, requests, changes, fetched, anchors, urls, localClears: () => localClears, command: nodes.find(node => node.type === Command) };
}
let passed = 0;
async function test(label, body) { await body(); passed++; console.log(`PASS ${label}`); }
const settle = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  await test("D435i source and valid optical depth are visible without map-coordinate claims", () => {
    const depth = { depth_m: 1.25, measurement: "optical_axis_depth", frame_id: "d435i_color_optical_frame" };
    const f = fixture({ current: { ...status, active: true, camera: { connected: true, source: "realsense_d435i", label: "D435i 彩色＋深度" },
      last: { captured_at: now, processed_at: now, suspected_target: true, candidates: [{ similarity: .8, depth }] } }, records: [{ ...event, depth }] });
    assert.ok(f.html.includes("D435i 彩色＋深度"));
    assert.ok(f.html.includes("前向距离约 1.25 米"));
    assert.ok(f.html.includes("发现时前向距离约"));
    assert.ok(f.html.includes("相对深度相机"));
  });
  await test("missing or invalid depth stays unknown and legacy records do not invent a distance", () => {
    for (const depth of [null, { depth_m: NaN }, { depth_m: Infinity }]) {
      const f = fixture({ current: { ...status, active: true, camera: { connected: true, source: "realsense_d435i" },
        last: { captured_at: now, processed_at: now, suspected_target: true, candidates: [{ similarity: .8, depth }] } } });
      assert.ok(f.html.includes("等待有效深度测距"));
      assert.ok(!f.html.includes("发现时前向距离约"));
      assert.ok(!f.html.includes("NaN 米") && !f.html.includes("Infinity 米"));
    }
  });
  await test("description target/history render text, no target photo, color not identity score", () => {
    const f = fixture({ current: { ...status, active: true, last: { captured_at: now, processed_at: now, suspected_target: true, candidates: [{ similarity: .8, score_kind: "color_fraction" }] } } });
    assert.ok(f.html.includes("描述找人")); assert.ok(f.html.includes("上衣颜色符合度")); assert.ok(f.html.includes("颜色只筛选候选"));
    assert.ok(f.html.includes("人工标注")); assert.ok(!f.html.includes("外观相似度"));
    assert.ok(!f.nodes.some(node => node.props?.path?.includes("target.jpg")));
    assert.ok(f.nodes.some(node => node.props?.path?.includes("snapshot.jpg")));
    assert.ok(f.requests.length === 0 && f.fetched.length === 0);
  });
  await test("description click uses authenticated API body, never motion/audio", async () => {
    const f = fixture(); f.button("使用这个描述").props.onClick(); await settle();
    assert.deepEqual(f.requests[0], { url: "/api/go2/person/description", token: "fake-auth", method: "POST", body: { text: target.description, name: "目标人物" } });
    assert.deepEqual(f.requests.map(item => item.url), ["/api/go2/person/description", "/api/go2/person/status"]);
    assert.equal(f.localClears(), 1);
  });
  await test("invalid description keeps existing target and displays server rejection", async () => {
    const f = fixture({ postFailure: true }); f.button("使用这个描述").props.onClick(); await settle();
    assert.ok(f.changes.some(change => change.index === 8 && change.value === "暂不支持这种描述"));
    assert.ok(!f.changes.some(change => change.index === 0));
    assert.equal(f.requests.length, 1);
    const blank = fixture({ description: "  " }); assert.equal(blank.button("使用这个描述").props.disabled, true);
  });
  await test("start creates 120-second stationary task with native speech false", async () => {
    const f = fixture(); f.button("开始 120 秒查找").props.onClick(); await settle();
    assert.deepEqual(f.requests[0].body, { mode: "stationary", similarity_threshold: .75, timeout_seconds: 120, speak: false });
    assert.equal(f.requests[0].url, "/api/go2/person/start");
  });
  await test("legacy photo target still keeps authenticated reference photo", () => {
    const f = fixture({ current: { ...status, target: { id: "photo-old", name: "旧照片目标" } }, records: [{ ...event, target_kind: undefined }], mode: "photo" });
    assert.ok(f.nodes.some(node => node.props?.path === "/api/go2/person/target.jpg?target=photo-old"));
    assert.ok(f.nodes.some(node => node.props?.path?.includes("/history/event-a/target.jpg")));
    assert.ok(f.button("重新拍照 / 更换"));
  });
  await test("confirm / reject / undo are explicit authenticated manual reviews", async () => {
    for (const [label, value, previous] of [["是目标", "confirmed", "suspected"], ["不是目标", "rejected", "suspected"], ["撤回", "suspected", "confirmed"]]) {
      const f = fixture({ records: [{ ...event, review_status: previous }] }); f.button(label).props.onClick(); await settle();
      assert.equal(f.requests[0].url, "/api/go2/person/history/event-a/review"); assert.equal(f.requests[0].token, "fake-auth");
      assert.deepEqual(f.requests[0].body, { status: value }); assert.equal(f.requests[1].method, "GET"); assert.equal(f.localClears(), 0);
    }
    assert.ok(fixture({ records: [{ ...event, review_status: "confirmed" }] }).html.includes("人工确认是目标"));
    assert.ok(fixture({ records: [{ ...event, review_status: undefined, status: "rejected" }] }).html.includes("人工排除"));
  });
  await test("JSON export uses authenticated GET and a local download", async () => {
    const f = fixture(); f.button("导出记录").props.onClick(); await settle();
    assert.equal(f.fetched[0].url, "/api/go2/person/history/export.json");
    assert.equal(f.fetched[0].options.headers.Authorization, "Bearer fake-auth");
    assert.equal(f.anchors[0].download, "go2-person-records.json"); assert.ok(f.anchors[0].clicked && f.anchors[0].removed);
    assert.equal(f.requests.length, 0); assert.equal(f.localClears(), 0);
  });
  await test("safe command photo callback preserves native capture/multiple selection entry", async () => {
    const f = fixture(); assert.ok(f.command); f.command.props.onCapture(); await settle();
    assert.equal(f.requests[0].url, "/api/go2/person/capture"); assert.deepEqual(f.requests[0].body, { name: "目标人物" });
    f.command.props.onChanged(); await settle(); assert.equal(f.requests.at(-1).url, "/api/go2/person/status");
  });
  await test("task states show remaining/progress, timeout, pending confirmation, stop and manual completion", () => {
    const fakeReact = { useState: value => [typeof value === "function" ? value() : value, () => {}], useEffect: () => {} };
    const { Go2SearchTaskStatus } = load("Go2PersonRecords.tsx", { react: fakeReact, "../lib": { classNames: style } }, { window: fakeWindow });
    for (const [state, label] of [["running", "剩余 60 秒"], ["timed_out", "超时未找到"], ["found_pending_confirmation", "候选待确认"], ["stopped", "已停止"], ["completed", "人工确认完成"], ["interrupted", "查找已中断"]]) {
      const html = renderToStaticMarkup(Go2SearchTaskStatus({ task: { id: "task", state, started_at: now - 60, deadline_at: now + 60, target_id: target.id } }));
      assert.ok(html.includes(label)); if (state === "running") assert.ok(html.includes('aria-valuenow="50"')); else assert.ok(!html.includes('role="progressbar"'));
    }
  });
  await test("task history makes no request while collapsed", () => {
    const calls = [], cleanups = [];
    for (const open of [false, true]) {
      let i = 0; const fakeReact = { useState: initial => [i++ === 0 ? open : initial, () => {}], useEffect: effect => { const cleanup = effect(); if (cleanup) cleanups.push(cleanup); } };
      const { Go2RecentTasks } = load("Go2PersonRecords.tsx", { react: fakeReact, "../lib": { classNames: style, apiFetch: (url, token) => { calls.push({ url, token }); return Promise.resolve({ items: [], total: 0 }); } } }, { window: fakeWindow });
      Go2RecentTasks({ token: "fake-auth", refreshKey: "task-a:running" });
      assert.equal(calls.length, open ? 1 : 0);
    }
    assert.equal(calls[0].url, "/api/go2/person/tasks"); assert.equal(calls[0].token, "fake-auth"); cleanups.forEach(cleanup => cleanup());
  });
  console.log(`${passed} person workbench UI cases passed; fake API only.`);
})().catch(error => { console.error(error); process.exitCode = 1; });
