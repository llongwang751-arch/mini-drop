import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
const output=path.resolve(process.env.MINI_DROP_BROWSER_OUTPUT||'output/acceptance/performance-localization-20261001/browser');
await mkdir(output,{recursive:true});
const base=process.env.MINI_DROP_BROWSER_BASE_URL;
if(base!=='https://120.24.187.205') throw Error('Unexpected host');
const browser=spawn(process.env.MINI_DROP_ACCEPTANCE_CHROME,['--headless=new','--remote-debugging-port=9381',
  `--user-data-dir=${path.join(os.tmpdir(),'mini-drop-performance-'+process.pid)}`,'--no-first-run','about:blank'],{windowsHide:true,stdio:'ignore'});
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let socket;const errors=[];const network=new Map();
try {
  let target;
  for(let i=0;i<40;i++){try{target=await(await fetch('http://127.0.0.1:9381/json/new?about:blank',{method:'PUT'})).json();break;}catch{await pause(250);}}
  if(!target)throw Error('Browser unavailable');
  socket=new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
  let seq=0;const pending=new Map();
  socket.addEventListener('message',event=>{
    const msg=JSON.parse(String(event.data));
    if(msg.method==='Network.requestWillBeSent'&&msg.params.request.url.includes('/api/')) network.set(msg.params.requestId,{url:msg.params.request.url,method:msg.params.request.method,start:msg.params.timestamp});
    if(msg.method==='Network.responseReceived'&&network.has(msg.params.requestId))Object.assign(network.get(msg.params.requestId),{status:msg.params.response.status,protocol:msg.params.response.protocol,response:msg.params.timestamp});
    if(msg.method==='Network.loadingFinished'&&network.has(msg.params.requestId))network.get(msg.params.requestId).end=msg.params.timestamp;
    if(msg.method==='Network.loadingFailed'&&network.has(msg.params.requestId))Object.assign(network.get(msg.params.requestId),{failed:msg.params.errorText,canceled:msg.params.canceled});

    if(msg.method==='Runtime.exceptionThrown')errors.push({type:'JS',message:msg.params.exceptionDetails.text});
    if(msg.method==='Network.responseReceived'&&msg.params.response.status>=400)errors.push({url:msg.params.response.url,status:msg.params.response.status});
    const cb=pending.get(msg.id);if(cb){pending.delete(msg.id);msg.error?cb.reject(Error(msg.error.message)):cb.resolve(msg.result);}
  });
  const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
  const evaluate=async expression=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error('Page evaluation failed');return r.result.value;};
  const until=async expression=>{for(let i=0;i<100;i++){if(await evaluate(expression))return;await pause(300);}
    const state=await evaluate(`({url:location.href,body:document.body.innerText,resources:performance.getEntriesByType('resource').filter(x=>x.name.includes('/api/')).map(x=>({url:x.name,duration:x.duration,transfer:x.transferSize}))})`);
    await writeFile(path.join(output,'network.json'),JSON.stringify([...network.values()],null,2));
    await evaluate(`(()=>{const n=Array.from(document.querySelectorAll('button')).find(x=>x.textContent==='诊断案例');if(n)n.click();return true;})()`);
    await pause(300);
    state.drawerBody=await evaluate('document.body.innerText');
    await writeFile(path.join(output,'failure.json'),JSON.stringify({expression,state,errors},null,2));
    await shot('failure');throw Error('Expected page content missing');};
  const shot=async name=>{const result=await send('Page.captureScreenshot',{format:'png'});await writeFile(path.join(output,name+'.png'),Buffer.from(result.data,'base64'));};
  const width=async value=>{await send('Emulation.setDeviceMetricsOverride',{width:value,height:1000,deviceScaleFactor:1,mobile:value===375});await pause(300);};
  await send('Page.enable');await send('Runtime.enable');await send('Network.enable');
  await send('Network.setExtraHTTPHeaders',{headers:{'X-API-Key':process.env.MINI_DROP_API_KEY}});
  await width(1440);await send('Page.navigate',{url:base+'/ai-diagnosis'});
  await until(`(()=>{const n=Array.from(document.querySelectorAll('.ant-segmented-item-label')).find(x=>x.textContent==='案例验证');if(n){n.click();return true;}return false;})()`);
  await until(`document.querySelectorAll('.fault-scenario').length===21 && document.body.innerText.includes('为什么旧 21 类根因为 0')`);
  await until(`document.body.innerText.includes('工程诊断判断通过 3/3') && document.body.innerText.includes('异常路径定位 2/3') && document.body.innerText.includes('待验收 18 类')`);
  const current=await evaluate(`fetch('/report-assets/engineering-diagnosis/index.json',{cache:'no-store'}).then(r=>r.json())`);
  if(current.profile!=='engineering-diagnosis.v1'||current.diagnosis_accepted!==3||current.localization_accepted!==2||current.refuted!==1||current.fresh_live_run!==false||current.not_evaluated.length!==18||current.cases.some(c=>c.causal_root_cause_verified!==false))throw Error('Wrong engineering score or scope');
  const cardGrades=await evaluate(`Array.from(document.querySelectorAll('.fault-scenario')).map(x=>x.innerText).filter(x=>x.includes('工程定位通过')||x.includes('判断通过 · 异常假设被反驳'))`);
  if(cardGrades.length!==3||cardGrades.filter(x=>x.includes('工程定位通过')).length!==2)throw Error('Current card grade hidden by old historical failures');
  const currentBody=await evaluate('document.body.innerText');
  await shot('current-engineering-acceptance');
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
  await until(`document.body.innerText.includes('检查结果与下一步已生成') && !!document.querySelector('button[aria-label="再次检查"]')`);
  if(await evaluate(`!!document.querySelector('.diagnosis-finding') || document.body.innerText.includes('未形成报告')`))throw Error('Check shown as missing root report');
  const healthLayouts=[];
  for(const value of [1440,375]) {
    await width(value);
    healthLayouts.push(await evaluate(`({width:innerWidth,body_width:document.documentElement.clientWidth,body_scroll:document.documentElement.scrollWidth})`));
    await shot('completed-check-'+value);
  }
  await width(1440);
  await evaluate(`document.querySelector('button[aria-label="再次检查"]').click();true`);
  await until(`!location.search.includes(${JSON.stringify(diagnosisId)}) && location.search.includes('insight_')`);
  await until(`document.body.innerText.includes('检查结果与下一步已生成') && document.body.innerText.includes('本次检查正常（已检查范围）')`);
  const followupId=await evaluate(`new URLSearchParams(location.search).get('case').replace('drop_insight_v2:','')`);
  const followup=await evaluate(`fetch('/api/v2/diagnoses/'+${JSON.stringify(followupId)}+'/events').then(r=>r.json()).then(x=>x.data||x)`);
  const events=Array.isArray(followup)?followup:followup.items;
  const createdEvent=events.find(e=>e.event_type==='diagnosis.created');
  const resultEvent=events.find(e=>e.event_type==='health_check.completed');
  if(createdEvent?.payload?.follow_up_diagnosis_id!==diagnosisId || resultEvent?.payload?.code!=='NORMAL_OBSERVED')throw Error('Browser fresh check linkage invalid');
  await writeFile(path.join(output,'browser-created-id.txt'),followupId);
  await shot('browser-created-normal-check');
  const paths=[];
  for(const item of JSON.parse(process.env.MINI_DROP_LOCALIZATION_DIAGNOSES||'[]')) {
    await send('Page.navigate',{url:base+'/ai-diagnosis?case='+encodeURIComponent('drop_insight_v2:'+item.diagnosis_id)});
    await pause(2500);
    await until(`!!document.querySelector('.observation-assessment')`);
    await until(`(()=>{const n=Array.from(document.querySelectorAll('.ant-segmented-item-label')).find(x=>x.textContent==='调查记录');if(n){n.click();return true;}return false;})()`);
    await until(`!!document.querySelector('.observation-assessment') && !!document.querySelector('.diagnosis-conclusion-card')`);
    if(item.localization_accepted)await until(`document.body.innerText.includes('性能路径已定位，根因仍待确认')`);
    if(item.observation?.status==='REFUTED')await until(`document.body.innerText.includes('本次观测未发现该性能异常') && document.body.innerText.includes('异常假设已反驳')`);
    const body=await evaluate('document.body.innerText');
    if(body.includes('因果根因已验证'))throw Error('Observed path promoted to causal root');
    const summary=await evaluate(`document.querySelector('.diagnosis-finding')?.innerText || ''`);
    const refutationDisplayed=item.observation?.status==='REFUTED'
      ? summary.includes('已测量，异常假设被反驳') && body.includes('0.081') && body.includes('684') && /数值判据已检查\s*3\/3/.test(body)
      : null;
    if(refutationDisplayed===false)throw Error('Measured I/O counterexample hidden from the primary result');
    paths.push({scenario_id:item.scenario_id,diagnosis_id:item.diagnosis_id,localized:item.localization_accepted,
      refutation_displayed:refutationDisplayed,summary,body});
    await shot(item.scenario_id);
  }
  const history=await send('Page.getNavigationHistory');
  const last=history.entries[history.currentIndex];
  const previous=history.entries[history.currentIndex-1];
  const networkCase=paths.find(x=>x.scenario_id==='go-network-latency');
  if(!previous||!networkCase||!previous.url.includes(networkCase.diagnosis_id))throw Error('Unexpected browser case navigation history');
  await send('Page.navigateToHistoryEntry',{entryId:previous.id});
  await until(`location.search.includes(${JSON.stringify(networkCase.diagnosis_id)}) && !!document.querySelector('.observation-assessment')`);
  await shot('history-back-network');
  await send('Page.navigateToHistoryEntry',{entryId:last.id});
  await until(`document.body.innerText.includes('本次观测未发现该性能异常') && document.querySelector('.diagnosis-finding')?.innerText.includes('已测量，异常假设被反驳')`);
  await shot('history-forward-io');
  await send('Page.navigate',{url:base+'/ai-diagnosis'});
  await until(`(()=>{const n=Array.from(document.querySelectorAll('.ant-segmented-item-label')).find(x=>x.textContent==='案例验证');if(n){n.click();return true;}return false;})()`);
  await until(`document.querySelectorAll('section[aria-label="当前工程诊断验收"] button').length===3`);
  await evaluate(`document.querySelector('section[aria-label="当前工程诊断验收"] button').click();true`);
  const cpuCase=current.cases.find(x=>x.scenario_id==='go-cpu-hotspot');
  await until(`document.body.innerText.includes('性能路径已定位，根因仍待确认') && document.body.innerText.includes('goCPUHotFunction')`);
  const buttonBody=await evaluate('document.body.innerText');
  if(!buttonBody.includes('goCPUHotFunction'))throw Error('Engineering diagnosis button opened the wrong report');
  await shot('engineering-cpu-button');
  await pause(300);
  const pendingStreams=[...network.values()].filter(x=>x.url.includes('stream')&&!x.end&&!x.failed);
  // CDP may omit completion events for streams in cached documents; this is telemetry, not a socket count.
  const result={passed:errors.length===0&&healthLayouts.every(x=>x.body_scroll<=x.body_width+1)&&layouts.every(x=>x.cards===21&&x.body_scroll<=x.body_width+1),
    history_navigation_checked:true,engineering_diagnosis:current,current_card_grades:cardGrades,current_overview_body:currentBody,engineering_button_checked:true,stream_requests_without_cdp_completion:pendingStreams.length,
    default_performance_cases:21,engineering_cases:index.cases.length,artifact_downloads_verified:hashes,
    audit_root_passes:audit.historical_root_passes,recorded_chain_consistent:audit.recorded_chain_consistent_count,
    layouts,healthLayouts,browser_created_check:followupId,health_diagnosis_id:diagnosisId,health_class:healthClass,healthBody,auditBody,paths,errors};
  await writeFile(path.join(output,'network.json'),JSON.stringify([...network.values()],null,2));
  await writeFile(path.join(output,'result.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify({passed:result.passed,default_performance_cases:21,engineering_cases:index.cases.length,
    downloaded_sha:hashes.length,layouts,health_class:healthClass,errors}));
  if(!result.passed)process.exitCode=1;
}finally{socket?.close();browser.kill();}
