/* Actual reference-library UI with isolated fake hooks/API. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const {createRequire}=require('node:module');
const web=path.resolve(__dirname,'../../apps/nano-deploy/dashboard_platform/web'),req=createRequire(path.join(web,'package.json'));
const ts=req('typescript'),React=req('react'),{renderToStaticMarkup}=req('react-dom/server');
const source=ts.transpileModule(fs.readFileSync(path.join(web,'src/components/Go2TargetLibrary.tsx'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}}).outputText;
const label=n=>typeof n==='string'?n:typeof n==='number'?String(n):n&&typeof n==='object'?(Array.isArray(n.props?.children)?n.props.children:[n.props?.children]).map(label).join(''):'';
function walk(n,out=[]){if(!n||typeof n!=='object')return out;out.push(n);for(const c of Array.isArray(n.props?.children)?n.props.children:[n.props?.children])Array.isArray(c)?c.forEach(x=>walk(x,out)):walk(c,out);return out;}
function fixture(confirm='',fail=false){
 const saved={id:'a'.repeat(32),name:'张同学',uploaded_at:Date.now()/1000,discovery_count:2};
 const values=[[saved],'','',confirm,'',true],calls=[],changes=[];let index=0,changed=0;
 const hooks={useState:init=>{const i=index++;return [i<values.length?values[i]:init,v=>changes.push({i,v})];},useEffect:()=>{}};
 const module={exports:{}};
 vm.runInNewContext(source,{module,exports:module.exports,require:n=>n==='react'?hooks:n==='../lib'?{API_BASE:'',apiFetch:async(url,token,opts={})=>{calls.push({url,token,method:opts.method||'GET'});if(fail)throw Error('保存失败');return {items:[]};}}:req(n),AbortController,Date,window:{setTimeout:()=>1,clearTimeout:()=>{}}});
 const tree=module.exports.Go2TargetLibrary({token:'test',currentId:'b'.repeat(32),onChanged:async()=>changed++});
 const nodes=walk(tree);return{calls,changes,html:renderToStaticMarkup(tree),button:s=>nodes.find(n=>n.type==='button'&&label(n)===s),changed:()=>changed};
}
const settle=()=>new Promise(r=>setImmediate(r));
(async()=>{
 let f=fixture();assert.ok(f.html.includes('目标列表')&&f.html.includes('张同学')&&f.html.includes('发现 2 次'));
 f.button('删除目标').props.onClick();assert.equal(f.calls.length,0);assert.equal(f.changes.at(-1).v,'a'.repeat(32));
 f=fixture('a'.repeat(32));f.button('确认删除').props.onClick();await settle();assert.equal(f.calls[0].method,'DELETE');assert.equal(f.changed(),1);
 f=fixture();f.button('选为目标').props.onClick();await settle();assert.equal(f.calls[0].method,'POST');assert.ok(f.calls[0].url.endsWith('/select'));assert.equal(f.changed(),1);
 assert.ok(f.calls.every(c=>!c.url.endsWith('/start')),'Selecting never starts a search');
 f=fixture('a'.repeat(32),true);f.button('确认删除').props.onClick();await settle();assert.equal(f.changed(),0);assert.ok(f.changes.some(c=>c.v==='保存失败'));
 console.log('Target library UI: list, explicit delete, reuse without auto-start, failure handling passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
