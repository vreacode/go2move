/* Actual TSX component rendered with fake state/hooks/backend; no browser or robot. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const { createRequire } = require("node:module");
const web = "/home/unitree/go2move/apps/nano-deploy/dashboard_platform/web";
const req = createRequire(path.join(web,"package.json"));
const ts = req("typescript");
const { renderToStaticMarkup } = req("react-dom/server");
const source = fs.readFileSync(path.join(web,"src/components/Go2PatrolPanel.tsx"),"utf8");
const compiled = ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}}).outputText;

async function renderFixture(state, confirmed) {
  let hook = 0;
  const requests = [];
  const errors = [];
  const effects = [];
  const fakeReact = {
    useState(initial) {
      const index = hook++;
      const value = index===0 ? state : index===1 ? confirmed : typeof initial==="function" ? initial() : initial;
      return [value,next=>{ if(index===3 && typeof next==="string") errors.push(next); }];
    },
    useRef: value=>({current:value}),
    useEffect: effect=>{ const cleanup=effect(); if(cleanup) effects.push(cleanup); },
  };
  const module = {exports:{}};
  const window = {setTimeout:()=>1,clearTimeout:()=>{},setInterval:()=>1,clearInterval:()=>{}};
  const fakeLib = {
    classNames:(...values)=>values.filter(Boolean).join(" "),
    async apiFetch(url,token,options={}) {
      requests.push({url,method:options.method||"GET",body:options.body&&JSON.parse(options.body)});
      if(options.method==="POST" && url.endsWith("/start")) throw new Error(JSON.stringify({detail:"温度与事故暂停。"}));
      return state;
    },
  };
  vm.runInNewContext(compiled,{module,exports:module.exports,require:name=>name==="react"?fakeReact:name==="../lib"?fakeLib:name==="./AutoPatrolTools"?{AutoPatrolTools:()=>null}:req(name),window,AbortController,console},{filename:"Go2PatrolPanel.cjs"});
  const root = module.exports.Go2PatrolPanel({token:"isolated-token"});
  const scanElement=root.props.children[0];
  const scan=scanElement.type(scanElement.props);
  const html=renderToStaticMarkup(scan);
  function findButton(node,label) {
    if(!node || typeof node!=="object") return null;
    if(node.type==="button" && node.props.children===label) return node;
    const children=node.props?.children;
    for(const child of Array.isArray(children)?children:[children]) {
      const match=findButton(child,label); if(match) return match;
    }
    return null;
  }
  await Promise.resolve();
  return {html,requests,errors,start:findButton(scan,"原地扫描找人"),stop:findButton(scan,"停止扫描"),cleanup:()=>effects.forEach(fn=>fn())};
}

(async()=>{
  const paused={state:"blocked",available:false,physical_validated:false,reason:"温度与事故暂停",angle_deg:0,max_angle_deg:90};
  let fixture=await renderFixture(paused,true);
  assert.equal(fixture.start.props.disabled,true);
  assert.ok(fixture.html.includes("温度与事故暂停"));
  assert.ok(!fixture.html.includes('type="checkbox"'));
  assert.ok(fixture.html.includes("90°"));
  fixture.start.props.onClick(); await Promise.resolve();
  assert.deepEqual(fixture.requests.map(r=>r.method),["GET"]);
  fixture.cleanup();

  fixture=await renderFixture({...paused,state:"idle",available:true,physical_validated:false},true);
  assert.equal(fixture.start.props.disabled,true);
  assert.ok(!fixture.html.includes('type="checkbox"'));
  fixture.cleanup();

  fixture=await renderFixture({...paused,state:"idle",available:true,physical_validated:true},false);
  assert.equal(fixture.start.props.disabled,true);
  assert.ok(fixture.html.includes('type="checkbox"'));
  fixture.start.props.onClick(); await Promise.resolve();
  assert.deepEqual(fixture.requests.map(r=>r.method),["GET"]);
  fixture.cleanup();

  fixture=await renderFixture({...paused,state:"idle",available:true,physical_validated:true},true);
  assert.equal(fixture.start.props.disabled,false);
  fixture.start.props.onClick(); await new Promise(resolve=>setImmediate(resolve));
  assert.equal(fixture.requests[1].url,"/api/go2/person/scan/start");
  assert.deepEqual(fixture.requests[1].body,{confirmed:true});
  assert.ok(fixture.errors.includes("温度与事故暂停。"));
  fixture.cleanup();

  fixture=await renderFixture({...paused,state:"scanning",angle_deg:35},false);
  assert.equal(fixture.start.props.disabled,true);
  assert.equal(fixture.stop.props.disabled,false);
  assert.ok(fixture.html.includes("35° / 90°"));
  fixture.stop.props.onClick(); await Promise.resolve();
  assert.equal(fixture.requests[1].url,"/api/go2/person/scan/stop");
  assert.deepEqual(fixture.requests[1].body,{});
  fixture.cleanup();
  console.log("5 actual-component scan UI cases passed: hold/unvalidated disable start, no invalid confirmation, confirmed-only start, 409 reason, active stop; fake backend only.");
})().catch(error=>{console.error(error);process.exitCode=1;});
