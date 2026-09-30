// Read-only browser smoke against the real loopback stack; no API fixtures.
import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
const output = path.resolve('output/acceptance/performance-fix-20260930/cache/browser-exercise');
await mkdir(output, { recursive: true });
const chrome = process.env.MINI_DROP_ACCEPTANCE_CHROME || path.join(process.env.LOCALAPPDATA, 'ms-playwright/chromium-1161/chrome-win/chrome.exe');
const browser = spawn(chrome, ['--headless=new', '--remote-debugging-port=9364', `--user-data-dir=${path.join(os.tmpdir(), `mini-drop-local-${process.pid}`)}`, '--no-first-run', 'about:blank'], { windowsHide: true, stdio: 'ignore' });
const pause = ms => new Promise(r => setTimeout(r, ms));
const errors = [];
const base = process.env.MINI_DROP_BROWSER_BASE_URL || 'http://127.0.0.1:18080';
if (!['http://127.0.0.1:18080', 'https://120.24.187.205'].includes(base)) throw new Error('Unsupported verification host');
let socket;
try {
  let target;
  for (let i = 0; i < 40; i++) {
    try { target = await (await fetch('http://127.0.0.1:9364/json/new?about:blank', {method:'PUT'})).json(); break; } catch { await pause(250); }
  }
  if (!target) throw new Error('Browser failed to start');
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, {once:true}); socket.addEventListener('error', reject, {once:true}); });
  let seq = 0;
  const pending = new Map();
  socket.addEventListener('message', e => {
    const msg = JSON.parse(String(e.data));
    if (msg.method === 'Runtime.exceptionThrown') errors.push(msg.params.exceptionDetails);
    if (msg.method === 'Network.responseReceived' && msg.params.response.status >= 400) errors.push({url:msg.params.response.url,status:msg.params.response.status});
    const cb = pending.get(msg.id);
    if (cb) { pending.delete(msg.id); msg.error ? cb.reject(new Error(msg.error.message)) : cb.resolve(msg.result); }
  });
  const send = (method, params = {}) => new Promise((resolve,reject) => { const id=++seq; pending.set(id,{resolve,reject}); socket.send(JSON.stringify({id,method,params})); });
  await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
  if (process.env.MINI_DROP_API_KEY) await send('Network.setExtraHTTPHeaders', { headers: { 'X-API-Key': process.env.MINI_DROP_API_KEY } });
  await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  const id = null;
  await send('Page.addScriptToEvaluateOnNewDocument', {source: 'localStorage.setItem("agi_auth_token",'+JSON.stringify(process.env.OFFICE_ACCEPTANCE_TOKEN)+');'});
  await send('Page.navigate',{url:base+'/ai-diagnosis'+(id?'?case='+encodeURIComponent('drop_insight_v2:'+id):'')});
  let text='';
  for(let i=0;i<40;i++) {
    await pause(500);
    const r=await send('Runtime.evaluate',{expression:'document.body.innerText',returnByValue:true});
    text=r.result.value || '';
    if(text.includes('新建诊断') || text.includes('诊断工作台')) break;
  }
  const evaluate = async expression => (await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true})).result.value;
  await evaluate('Array.from(document.querySelectorAll("label")).find(e => e.textContent === "选择服务")?.click(); true');
  for (let i=0;i<40;i++) {
    if(await evaluate('Boolean(document.querySelector(".office-exercise button"))')) break;
    await pause(500);
  }
  if (!await evaluate('Boolean(document.querySelector(".office-exercise button"))')) throw new Error('Office exercise did not load');
  await evaluate('document.querySelector(".office-exercise button").click(); true');
  let completed=false;
  for (let i=0;i<160;i++) {
    await pause(500);
    completed=await evaluate('Boolean(document.querySelector(".office-exercise-results")?.innerText.includes("受控慢检索已定位并撤销"))');
    if(completed) break;
  }
  text=await evaluate('document.querySelector(".office-exercise")?.innerText || ""');
  const shot=await send('Page.captureScreenshot',{format:'png'});
  await writeFile(path.join(output,'exercise.png'),Buffer.from(shot.data,'base64'));
  const result={url:base+'/ai-diagnosis',errors,body:text,passed:completed && errors.length===0 && text.includes('准备请求完成') && text.includes('查看本次诊断与证据树')};
  await writeFile(path.join(output,'result.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify({passed:result.passed,errors,text_length:text.length,output}));
  if(!result.passed) process.exitCode=1;
} finally { socket?.close(); browser.kill(); }
