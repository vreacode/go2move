/* Actual TSX with persistent fake hooks/backend. No network, ROS, audio or robot. */
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const { createRequire } = require("node:module");
const web = "/home/unitree/go2move/apps/nano-deploy/dashboard_platform/web";
const req = createRequire(path.join(web,"package.json")), ts = req("typescript"), { renderToStaticMarkup } = req("react-dom/server");
const compiled = ts.transpileModule(fs.readFileSync(path.join(web,"src/components/AutoPatrolTools.tsx"),"utf8"),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}}).outputText;
const map = {id:"saved-map",name:"已保存区域",width:100,height:50,available:true};
const route = {id:"route-one",name:"自动观察区域",frame_id:"map",points:[{name:"观察点1",x:1,y:1,yaw:0},{name:"观察点2",x:2,y:1,yaw:0},{name:"观察点3",x:2,y:2,yaw:1}],generation:{map_id:map.id,live_localization:false,width:100,height:50,point_pixels:[{name:"观察点1",x:25,y:25},{name:"观察点2",x:75,y:25},{name:"观察点3",x:75,y:40}]}};
const idle = {state:"idle",active:false,simulation:true,physical_execution:false,motion_enabled:false,steps:[]};
const settle = () => new Promise(resolve=>setImmediate(resolve));
const text = node => typeof node==="string"?node:node&&typeof node==="object"?(Array.isArray(node.props?.children)?node.props.children:[node.props?.children]).map(text).join(""):"";
function walk(node,out=[]) {if(!node||typeof node!=="object")return out;out.push(node);for(const child of Array.isArray(node.props?.children)?node.props.children:[node.props?.children])Array.isArray(child)?child.forEach(item=>walk(item,out)):walk(child,out);return out;}
function fixture({savedRoute=route,execution=idle,scenario="not_found",maps=[map],api}={}) {
  const states = [maps,map.id,savedRoute,null,execution,scenario,"","",""];
  const refs=[], requests=[], effects=[], changes=[], timers=[];
  let si=0,ri=0;
  const fakeReact={useState(initial){const index=si++;if(!(index in states))states[index]=typeof initial==="function"?initial():initial;return [states[index],value=>{states[index]=typeof value==="function"?value(states[index]):value;changes.push({index,value:states[index]});}];},useRef(value){const i=ri++;return refs[i]??(refs[i]={current:value});},useEffect(effect){effects.push(effect);}};
  const window={setTimeout:()=>1,clearTimeout:()=>{},setInterval:(callback,ms)=>{timers.push({callback,ms});return timers.length;},clearInterval:()=>{}};
  const fakeLib={API_BASE:"",classNames:(...items)=>items.filter(Boolean).join(" "),async apiFetch(url,token,options={}) {
    const call={url,token,method:options.method||"GET",body:options.body&&JSON.parse(options.body),signal:options.signal};requests.push(call);
    if(api)return api(call);
    if(url.endsWith("/maps"))return {items:[map],default_id:map.id};
    if(url.endsWith("/status")&&!url.includes("execution"))return {routes:[route]};
    if(url.endsWith("/suggest"))return route;
    if(url.endsWith("/preview"))return {mode:"planning",ok:true,ordered_route_validated:false,segments:[{name:"观察点1",ok:true}]};
    if(url.endsWith("/rehearse"))return {...idle,state:"preparing",active:true,route_id:route.id};
    if(url.endsWith("/cancel"))return {...idle,state:"cancelled"};return idle;
  }};
  const module={exports:{}};
  vm.runInNewContext(compiled,{module,exports:module.exports,require:name=>name==="react"?fakeReact:name==="../lib"?fakeLib:req(name),window,AbortController,Date,console,fetch:()=>{throw new Error("No real fetch allowed");}},{filename:"AutoPatrolTools.cjs"});
  function render(token="fake-token") {si=ri=0;effects.length=0;const tree=module.exports.AutoPatrolTools({token});return {tree,nodes:walk(tree),html:()=>renderToStaticMarkup(tree),button:label=>walk(tree).find(node=>node.type==="button"&&text(node)===label)};}
  return {render,states,refs,requests,effects,changes,timers,module};
}
let passed=0;async function test(label,fn){await fn();passed++;console.log(`PASS ${label}`);}
(async()=>{
  await test("default visible auto-map preparation, no XY input, physical run absent",()=>{
    const f=fixture(),r=f.render();const html=r.html();assert.ok(html.includes("地图自动选点"));assert.ok(html.includes("无需填写坐标"));assert.ok(html.includes("尚未确认机器狗当前所在位置"));assert.ok(html.includes("不生成真实发现记录"));
    assert.ok(!r.nodes.some(node=>node.type==="input"));assert.equal(r.button("实机巡航暂停").props.disabled,true);assert.equal(r.button("实机巡航暂停").props.onClick,undefined);assert.equal(f.requests.length,0);
  });
  await test("no map disables generate/check/rehearse",()=>{
    const f=fixture({maps:[],savedRoute:null});f.states[1]="";const r=f.render();assert.equal(r.button("一键生成观察点").props.disabled,true);assert.equal(r.button("检查观察点").props.disabled,true);assert.equal(r.button("流程演练").props.disabled,true);
  });
  await test("generate requests only three local static-map points and stores returned route",async()=>{
    const f=fixture({savedRoute:null});f.render().button("一键生成观察点").props.onClick();await settle();
    assert.equal(f.requests[0].url,"/api/go2/person/patrol/suggest");assert.equal(f.requests[0].token,"fake-token");assert.deepEqual(f.requests[0].body,{map_id:map.id,count:3,radius_m:2});assert.equal(f.states[2],route);
  });
  await test("checks each point without claiming full-route validation",async()=>{
    const f=fixture();f.render().button("检查观察点").props.onClick();await settle();assert.equal(f.requests[0].url,"/api/go2/person/patrol/preview");assert.deepEqual(f.requests[0].body,{route_id:route.id});assert.ok(f.render().html().includes("尚未验证整段巡航"));
  });
  await test("three rehearsal scenarios use asynchronous simulation endpoint, cancel only simulation",async()=>{
    for(const scenario of ["not_found","found","navigation_failed"]){const f=fixture({scenario});f.render().button("流程演练").props.onClick();await settle();assert.equal(f.requests[0].url,"/api/go2/person/patrol/execution/rehearse");assert.deepEqual(f.requests[0].body,{route_id:route.id,scenario});const r=f.render();assert.ok(r.button("取消演练"));assert.equal(r.button("重新自动选点").props.disabled,true);r.button("取消演练").props.onClick();await settle();assert.equal(f.requests[1].url,"/api/go2/person/patrol/execution/cancel");assert.deepEqual(f.requests[1].body,{});assert.equal(f.states[4].state,"cancelled");assert.ok(f.requests.every(call=>!call.url.endsWith("/run")&&!call.url.includes("scan")));}
  });
  await test("cancel supersedes a delayed rehearsal reply",async()=>{
    let release;const gate=new Promise(resolve=>release=resolve);
    const f=fixture({api:call=>call.url.endsWith("/rehearse")?gate:{...idle,state:"cancelled"}});
    f.render().button("流程演练").props.onClick();f.render().button("取消演练").props.onClick();await settle();release({...idle,state:"preparing",active:true});await settle();assert.equal(f.states[4].state,"cancelled");assert.equal(f.states[6],"");
  });
  await test("simulated candidate/failure/cancel reports never become real discoveries",()=>{
    for(const state of ["found","not_found","failed","cancelled","interrupted","cancel_failed"]){const f=fixture({execution:{...idle,state,reason:"simulation_complete"}});const html=f.render().html();assert.ok(html.includes("不生成真实发现记录"));assert.ok(!html.includes("地图准备服务暂时不可用"));if(state==="found")assert.ok(html.includes("模拟出现候选"));assert.equal(f.requests.length,0);}
    const first=fixture({execution:{...idle,state:"observing",active:true,index:1,total:3,point:"观察点1"}});assert.ok(first.render().html().includes("观察点 1 / 3"));
  });
  await test("map overlay uses exact preview pixels, no line/current-dog marker",()=>{
    const module={exports:{}};let index=0;
    const fakeReact={useState:initial=>[index++===0?"blob:fake-map":initial,()=>{}],useEffect:()=>{}};
    vm.runInNewContext(compiled,{module,exports:module.exports,require:name=>name==="react"?fakeReact:name==="../lib"?{classNames:(...items)=>items.filter(Boolean).join(" ")}:req(name),console},{filename:"MapPreview.cjs"});
    const tree=module.exports.PatrolMapPreview({token:"fake",map,route});const nodes=walk(tree);const marker=nodes.find(node=>node.props?.["aria-label"]==="观察点1");assert.equal(marker.props.style.left,"25%");assert.equal(marker.props.style.top,"50%");
    assert.ok(!nodes.some(node=>["svg","path","polyline","canvas"].includes(node.type)));assert.ok(!text(tree).includes("当前位置"));assert.equal(nodes.find(node=>node.type==="img").props.className,"block w-full");
    index=0;const mismatch=module.exports.PatrolMapPreview({token:"fake",map:{...map,id:"another-map"},route});assert.ok(!walk(mismatch).some(node=>node.props?.["aria-label"]==="观察点1"));
    index=0;const dimensionsOnly=module.exports.PatrolMapPreview({token:"fake",map,route:{...route,generation:{map_id:map.id,live_localization:false,preview:{width:100,height:50}}}});assert.ok(!walk(dimensionsOnly).some(node=>node.props?.["aria-label"]==="观察点1"));
    index=0;const changed=module.exports.PatrolMapPreview({token:"fake",map:{...map,map_sha256:"new"},route:{...route,generation:{...route.generation,map_sha256:"old"}}});assert.ok(!walk(changed).some(node=>node.props?.["aria-label"]==="观察点1"));
  });
  await test("token cleanup discards old map results and initial requests remain read-only",async()=>{
    let release, mapRequests=0;const gate=new Promise(resolve=>release=resolve);
    const freshMap={...map,id:"new-session-map"};
    const f=fixture({api:call=>call.url.endsWith("/maps")?(++mapRequests===1?gate:{items:[freshMap],default_id:freshMap.id}):call.url.includes("execution")?idle:{routes:[]}});
    f.render("old-token");const oldCleanups=f.effects.map(effect=>effect()).filter(Boolean);oldCleanups.forEach(fn=>fn());
    f.render("new-token");const newCleanups=f.effects.map(effect=>effect()).filter(Boolean);await settle();release({items:[map],default_id:map.id});await settle();assert.equal(f.states[1],freshMap.id);assert.equal(f.states[0][0].id,freshMap.id);assert.ok(f.requests.every(call=>call.method==="GET"));assert.ok(f.requests.filter(call=>call.token==="old-token").every(call=>call.signal.aborted));newCleanups.forEach(fn=>fn());f.render("");f.effects.forEach(effect=>effect());assert.equal(f.states[6],"");
  });
  console.log(`${passed} auto patrol UI cases passed; fake backend only.`);
})().catch(error=>{console.error(error);process.exitCode=1;});
