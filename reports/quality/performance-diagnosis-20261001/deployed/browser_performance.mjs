import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
const output=path.resolve('output/acceptance/performance-diagnosis-20261001/browser');
await mkdir(output,{recursive:true});
const base=process.env.MINI_DROP_BROWSER_BASE_URL;
if(base!=='https://120.24.187.205') throw Error('Unexpected host');
const browser=spawn(process.env.MINI_DROP_ACCEPTANCE_CHROME,['--headless=new','--remote-debugging-port=9381',
  `--user-data-dir=${path.join(os.tmpdir(),'mini-drop-performance-'+process.pid)}`,'--no-first-run','about:blank'],{windowsHide:true,stdio:'ignore'});
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let socket;const errors=[];
try {
  let target;
  for(let i=0;i<40;i++){try{target=await(await fetch('http://127.0.0.1:9381/json/new?about:blank',{method:'PUT'})).json();break;}catch{await pause(250);}}
  if(!target)throw Error('Browser unavailable');
  socket=new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
  let seq=0;const pending=new Map();
  socket.addEventListener('message',event=>{
    const msg=JSON.parse(String(event.data));
    if(msg.method==='Runtime.exceptionThrown')errors.push({type:'JS',message:msg.params.exceptionDetails.text});
    if(msg.method==='Network.responseReceived'&&msg.params.response.status>=400)errors.push({url:msg.params.response.url,status:msg.params.response.status});
    const cb=pending.get(msg.id);if(cb){pending.delete(msg.id);msg.error?cb.reject(Error(msg.error.message)):cb.resolve(msg.result);}
  });
  const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
  const evaluate=async expression=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error('Page evaluation failed');return r.result.value;};
  const until=async expression=>{for(let i=0;i<100;i++){if(await evaluate(expression))return;await pause(300);}throw Error('Expected page content missing');};
  const shot=async name=>{const result=await send('Page.captureScreenshot',{format:'png'});await writeFile(path.join(output,name+'.png'),Buffer.from(result.data,'base64'));};
  const width=async value=>{await send('Emulation.setDeviceMetricsOverride',{width:value,height:1000,deviceScaleFactor:1,mobile:value===375});await pause(300);};
  await send('Page.enable');await send('Runtime.enable');await send('Network.enable');
  await send('Network.setExtraHTTPHeaders',{headers:{'X-API-Key':process.env.MINI_DROP_API_KEY}});
  await width(1440);await send('Page.navigate',{url:base+'/ai-diagnosis'});
  await until(`(()=>{const n=Array.from(document.querySelectorAll('.ant-segmented-item-label')).find(x=>x.textContent==='案例验证');if(n){n.click();return true;}return false;})()`);
  await until(`document.querySelectorAll('.fault-scenario').length===21 && document.body.innerText.includes('为什么旧 21 类根因为 0')`);
  const layouts=[];
  for(const value of [1440,1024,768,375]){
    await width(value);
    layouts.push(await evaluate(`({width:innerWidth,body_width:document.documentElement.clientWidth,body_scroll:document.documentElement.scrollWidth,cards:document.querySelectorAll('.fault-scenario').length})`));
    await shot('performance-'+value);
  }
  await width(1440);
  await evaluate(`Array.from(document.querySelectorAll('.ant-collapse-header')).find(x=>x.textContent.includes('为什么旧 21')).click();true`);
  await until(`document.body.innerText.includes('归档记录证据链一致 18/21')`);
  const auditBody=await evaluate('document.body.innerText');await shot('failure-audit');
  const audit=await evaluate(`fetch('/report-assets/performance-audit/index.json').then(r=>r.json())`);
  if(audit.historical_root_passes!==0||audit.cases.length!==21)throw Error('Historical score changed');
  await evaluate(`Array.from(document.querySelectorAll('.eval-center-nav .ant-segmented-item-label')).find(x=>x.textContent.includes('工程修复回归')).click();true`);
  await until(`document.body.innerText.includes('4 个缺陷闭环已验证')`);
  const index=await evaluate(`fetch('/report-assets/engineering-cases/index.json').then(r=>r.json())`);
  const hashes=[];
  for(const item of index.cases)for(const file of item.evidence){
    const hash=await evaluate(`fetch(${JSON.stringify('/report-assets/engineering-cases/'+file.filename)}).then(r=>r.arrayBuffer()).then(async bytes=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(x=>x.toString(16).padStart(2,'0')).join(''))`);
    if(hash!==file.sha256)throw Error('Artifact mismatch');hashes.push({filename:file.filename,sha256:hash});
  }
  await shot('engineering');
  const diagnosisId=process.env.MINI_DROP_HEALTH_DIAGNOSIS_ID;
  if(!/^insight_[a-z0-9]+$/.test(diagnosisId||''))throw Error('Missing health diagnosis');
  await send('Page.navigate',{url:base+'/ai-diagnosis?case='+encodeURIComponent('drop_insight_v2:'+diagnosisId)});
  await until(`document.body.innerText.includes('本次检查正常（已检查范围）')`);
  const healthBody=await evaluate('document.body.innerText'); if(!healthBody.includes('检查结果：正常'))throw Error('Normal primary result missing');
  const healthClass=await evaluate(`document.querySelector('.observation-assessment')?.className`);
  await shot('health-normal');
  const result={passed:errors.length===0&&layouts.every(x=>x.cards===21&&x.body_scroll<=x.body_width+1),
    default_performance_cases:21,engineering_cases:index.cases.length,artifact_downloads_verified:hashes,
    audit_root_passes:audit.historical_root_passes,recorded_chain_consistent:audit.recorded_chain_consistent_count,
    layouts,health_diagnosis_id:diagnosisId,health_class:healthClass,healthBody,auditBody,errors};
  await writeFile(path.join(output,'result.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify({passed:result.passed,default_performance_cases:21,engineering_cases:index.cases.length,
    downloaded_sha:hashes.length,layouts,health_class:healthClass,errors}));
  if(!result.passed)process.exitCode=1;
}finally{socket?.close();browser.kill();}
