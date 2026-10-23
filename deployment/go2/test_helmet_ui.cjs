/* Real helmet UI with fake hooks/HTTP/MediaRecorder; no camera, audio or robot. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const {createRequire}=require('node:module');
const web=path.resolve(__dirname,'../../apps/nano-deploy/dashboard_platform/web'),req=createRequire(path.join(web,'package.json'));
const ts=req('typescript'),React=req('react'),{renderToStaticMarkup}=req('react-dom/server');
const compiled=ts.transpileModule(fs.readFileSync(path.join(web,'src/components/Go2HelmetPanel.tsx'),'utf8'),
 {compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}}).outputText;
const label=n=>typeof n==='string'?n:typeof n==='number'?String(n):n&&typeof n==='object'?
 (Array.isArray(n.props?.children)?n.props.children:[n.props?.children]).map(label).join(''):'';
function walk(n,out=[]){if(!n||typeof n!=='object')return out;out.push(n);for(const c of Array.isArray(n.props?.children)?n.props.children:[n.props?.children])Array.isArray(c)?c.forEach(x=>walk(x,out)):walk(c,out);return out;}
function fixture(active=false){
 const status={active,loading:false,models_ready:true,record_count:2,frames:5,last:{captured_at:Date.now()/1000,total:3,counts:{helmet:1,no_helmet:2,unknown:0},people:[{track_id:2,state:'no_helmet',hits:3},{track_id:3,state:'no_helmet',hits:3}]}};
 const states=[status,[], '',false,false,0,true],calls=[],downloads=[],timers=[];let index=0,refIndex=0;
 let tracksStopped=false;
 const canvas={captureStream:()=>({getTracks:()=>[{stop:()=>tracksStopped=true}]})};
 const refs=[{current:canvas},{current:null},{current:true},{current:Date.now()}];
 class Recorder {static isTypeSupported(t){return t.includes('webm');} constructor(){this.state='inactive';}start(){this.state='recording';this.ondataavailable({data:new Blob(['encoded-video-chunk'])});}stop(){this.state='inactive';this.onstop();}}
 const fakeReact={useState(initial){const i=index++;return[states[i]??initial,v=>states[i]=typeof v==='function'?v(states[i]):v];},useRef(){return refs[refIndex++];},useEffect(){}};
 const module={exports:{}};
 const url={createObjectURL:()=> 'blob:local-demo',revokeObjectURL:()=>{}};
 const window={setTimeout:()=>1,clearTimeout:()=>{},setInterval:(fn)=>{timers.push(fn);return 1;},clearInterval:()=>{}};
 const document={body:{appendChild:()=>{}},createElement:()=>({click(){downloads.push(this.download);},remove(){}})};
 vm.runInNewContext(compiled,{module,exports:module.exports,require:n=>n==='react'?fakeReact:n==='../lib'?{API_BASE:'',apiFetch:async(url,token,opts)=>{calls.push({url,token,method:opts.method});return {...status,active:!url.endsWith('/stop')};}}:n==='./Panel'?{Panel:({children})=>React.createElement('section',null,children)}:req(n),Blob,MediaRecorder:Recorder,URL:url,window,document,AbortController,Date,console});
 function render(){index=0;refIndex=0;const tree=module.exports.Go2HelmetPanel({token:'fake-token'});return{html:renderToStaticMarkup(tree),button:s=>walk(tree).find(n=>n.type==='button'&&label(n)===s)};}
 return{render,calls,downloads,states,get tracksStopped(){return tracksStopped;}};
}
(async()=>{
 let f=fixture(),r=f.render();assert.equal(r.button('录制 Demo').props.disabled,true);
 r.button('开始识别').props.onClick();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(f.calls[0].url,'/api/go2/person/helmet/start');assert.equal(f.calls[0].method,'POST');
 f=fixture(true);r=f.render();assert.equal(r.button('开始识别').props.disabled,true);
 assert.ok(r.html.includes('未戴安全帽')&&r.html.includes('#2')&&r.html.includes('#3'));
 assert.ok(r.html.includes('头部遮挡'));
 r.button('录制 Demo').props.onClick();assert.equal(f.states[4],true);
 f.render().button('结束并下载录像 0s').props.onClick();
 assert.equal(f.states[4],false);assert.equal(f.downloads.length,1);assert.ok(f.downloads[0].endsWith('.webm'));assert.equal(f.tracksStopped,true);
 f.render().button('停止识别').props.onClick();await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(f.calls.map(x=>x.url),['/api/go2/person/helmet/stop']);
 console.log('Helmet UI: start/stop, multi-person display, stationary boundary, canvas record/download passed. Codec/browser capture requires onsite validation.');
})().catch(e=>{console.error(e);process.exitCode=1;});
