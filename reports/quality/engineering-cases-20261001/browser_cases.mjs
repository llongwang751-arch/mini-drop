import { spawn } from 'node:child_process';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import os from 'node:os';
const output = path.resolve('output/acceptance/engineering-cases-20261001/browser');
await mkdir(output, { recursive:true });
const base = process.env.MINI_DROP_BROWSER_BASE_URL;
if (base !== 'https://120.24.187.205') throw new Error('Unexpected verification host');
const browser = spawn(process.env.MINI_DROP_ACCEPTANCE_CHROME, ['--headless=new','--remote-debugging-port=9367',`--user-data-dir=${path.join(os.tmpdir(),'mini-drop-defects-'+process.pid)}`,'--no-first-run','about:blank'], { windowsHide:true, stdio:'ignore' });
const pause = ms => new Promise(resolve => setTimeout(resolve,ms));
const errors=[];
let socket;
try {
  let target;
  for(let i=0;i<40;i++) {
    try { target=await (await fetch('http://127.0.0.1:9367/json/new?about:blank',{method:'PUT'})).json();break; } catch { await pause(250); }
  }
  if(!target) throw new Error('Browser not available');
  socket=new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
  let seq=0;
  const pending=new Map();
  socket.addEventListener('message',event=>{
    const msg=JSON.parse(String(event.data));
    if(msg.method==='Runtime.exceptionThrown') errors.push({type:'JS',message:msg.params.exceptionDetails.text});
    if(msg.method==='Network.responseReceived' && msg.params.response.status>=400) errors.push({url:msg.params.response.url,status:msg.params.response.status});
    const cb=pending.get(msg.id);
    if(cb) { pending.delete(msg.id);msg.error?cb.reject(new Error(msg.error.message)):cb.resolve(msg.result); }
  });
  const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
  const evaluate=async expression=>{
    const result=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
    if(result.exceptionDetails) throw new Error('Page evaluation failed');
    return result.result.value;
  };
  await send('Page.enable');await send('Runtime.enable');await send('Network.enable');
  await send('Network.setExtraHTTPHeaders',{headers:{'X-API-Key':process.env.MINI_DROP_API_KEY}});
  await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await send('Page.navigate',{url:base+'/ai-diagnosis'});
  let entered=false;
  for(let i=0;i<50;i++) {
    await pause(400);
    entered=await evaluate(`(()=>{const node=Array.from(document.querySelectorAll('.ant-segmented-item-label')).find(n=>n.textContent==='案例验证');if(node){node.click();return true;}return false;})()`);
    if(entered) break;
  }
  if(!entered) throw new Error('Evaluation navigation missing');
  for(let i=0;i<40;i++) {
    await pause(250);
    if(await evaluate(`document.body.innerText.includes('4 个缺陷闭环已验证')`)) break;
  }
  const body=await evaluate('document.body.innerText');
  if(!body.includes('4 个缺陷闭环已验证') || !body.includes('不计入 AI 自动根因成绩')) throw new Error('Default defect catalog missing');
  const remote=await evaluate(`fetch('/report-assets/engineering-cases/index.json',{cache:'no-cache'}).then(r=>r.json())`);
  const artifactHashes=[];
  for(const item of remote.cases) for(const file of item.evidence) {
    const actual=await evaluate(`fetch(${JSON.stringify('/report-assets/engineering-cases/'+file.filename)},{cache:'no-cache'}).then(r=>{if(!r.ok)throw Error('download');return r.arrayBuffer()}).then(async bytes=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(x=>x.toString(16).padStart(2,'0')).join(''))`);
    if(actual!==file.sha256) throw new Error('Downloaded evidence hash mismatch');
    artifactHashes.push({filename:file.filename,sha256:actual});
  }
  const sizes=[];
  for(const width of [1440,1024,768,375]) {
    await send('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:width===375});
    await pause(350);
    const layout=await evaluate(`(()=>{const p=document.querySelector('.engineering-cases');const nav=document.querySelector('.eval-center-nav');return {width:innerWidth,panel_width:p.clientWidth,panel_scroll:p.scrollWidth,nav_width:nav.clientWidth,nav_scroll:nav.scrollWidth,cards:p.querySelectorAll('.ant-card').length};})()`);
    sizes.push(layout);
    const shot=await send('Page.captureScreenshot',{format:'png'});
    await writeFile(path.join(output,'cases-'+width+'.png'),Buffer.from(shot.data,'base64'));
  }
  await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await evaluate(`Array.from(document.querySelectorAll('button')).find(n=>n.textContent==='查看历史故障实验').click();true`);
  await pause(1600);
  const historical=await evaluate('document.body.innerText');
  const oldAPI=await evaluate(`fetch('/api/v2/showcases/fault-plaza').then(r=>r.json())`);
  const data=oldAPI.data||oldAPI;
  const scenarios=Array.isArray(data.scenarios)?data.scenarios:data.scenarios?.items;
  const shot=await send('Page.captureScreenshot',{format:'png'});
  await writeFile(path.join(output,'historical.png'),Buffer.from(shot.data,'base64'));
  const result={url:base+'/ai-diagnosis',default_cases:4,model_auto_root_cause:'NOT_EVALUATED',
    historical_catalog_count:scenarios?.length,active_faults:scenarios?.filter(x=>x.active).length,
    historical_control_visible:historical.includes('故障广场') && historical.includes('停止'),
    artifactHashes,sizes,errors,body,historical_body:historical};
  result.passed=errors.length===0 && sizes.every(x=>x.cards===4 && x.panel_scroll<=x.panel_width+1 && x.nav_scroll<=x.nav_width+1)
    && result.historical_catalog_count===21 && result.active_faults===0 && result.historical_control_visible;
  await writeFile(path.join(output,'result.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify({passed:result.passed,cases:4,download_hashes_verified:artifactHashes.length,sizes,errors,historical_count:result.historical_catalog_count,active_faults:result.active_faults,historical_control_visible:result.historical_control_visible}));
  if(!result.passed) process.exitCode=1;
} finally { socket?.close();browser.kill(); }
