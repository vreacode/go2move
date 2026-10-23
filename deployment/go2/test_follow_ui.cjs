/* Real TSX, fake HTTP/hooks; never connects to ROS, camera or robot. */
const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const {createRequire} = require('node:module');
const web = path.resolve(__dirname, '../../apps/nano-deploy/dashboard_platform/web');
const req = createRequire(path.join(web, 'package.json'));
const ts = req('typescript'), {renderToStaticMarkup} = req('react-dom/server');
const compiled = ts.transpileModule(fs.readFileSync(path.join(web, 'src/components/Go2FollowPanel.tsx'), 'utf8'),
  {compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020}}).outputText;
const text = n => typeof n === 'string' ? n : n && typeof n === 'object' ?
  (Array.isArray(n.props?.children) ? n.props.children : [n.props?.children]).map(text).join('') : '';
function walk(n, out=[]) {
  if (!n || typeof n !== 'object') return out;
  out.push(n);
  for (const c of Array.isArray(n.props?.children) ? n.props.children : [n.props?.children])
    Array.isArray(c) ? c.forEach(x => walk(x, out)) : walk(c, out);
  return out;
}
function fixture(ready=true, active=false) {
  const states = [false, '1.8', ''], calls=[]; let index=0, changes=0;
  const fakeReact = {useState(initial) {const i=index++; return [states[i] ?? initial, v=>states[i]=v];}};
  const mod = {exports: {}};
  vm.runInNewContext(compiled, {module:mod, exports:mod.exports,
    require:n=>n==='react' ? fakeReact : n==='../lib' ? {apiFetch: async(url, token, options)=>calls.push({url, token, body:JSON.parse(options.body)})} : req(n),
    AbortController, window:{setTimeout:()=>1, clearTimeout:()=>{}}});
  const status = {active, state: active?'tracking':'idle', reason:'测试状态', distance_m:1.8,
    depth_m:2.3, bearing_rad:-.1, suggested_velocity:[.2,0,-.08], motion_enabled:false, physical_execution:false};
  function render() {
    index=0;
    const tree=mod.exports.Go2FollowPanel({token:'fake-token', ready, status, onChanged:async()=>changes++});
    return {html:renderToStaticMarkup(tree), button:label=>walk(tree).find(n=>n.type==='button'&&text(n)===label),
      select:walk(tree).find(n=>n.type==='select')};
  }
  return {render, calls, get changes(){return changes;}};
}
(async()=>{
  let f=fixture(false); let r=f.render();
  assert.equal(r.button('开始跟随预览').props.disabled,true);
  assert.ok(r.html.includes('不驱动机器狗'));
  f=fixture(); r=f.render(); r.select.props.onChange({target:{value:'2.0'}});
  await f.render().button('开始跟随预览').props.onClick(); await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(f.calls,[{url:'/api/go2/person/follow/start',token:'fake-token',body:{mode:'preview',distance_m:2.0}}]);
  assert.equal(f.changes,1);
  f=fixture(true,true); r=f.render();
  assert.equal(r.button('开始跟随预览').props.disabled,true);
  assert.equal(r.select.props.disabled,true);
  assert.ok(r.html.includes('2.30 米') && r.html.includes('0.20 m/s'));
  r.button('停止预览').props.onClick(); await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(f.calls,[{url:'/api/go2/person/follow/stop',token:'fake-token',body:{}}]);
  console.log('Follow UI: readiness, preview-only wording, selected distance, start/stop, telemetry passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
