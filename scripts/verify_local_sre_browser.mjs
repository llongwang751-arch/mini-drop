// Read-only browser smoke against the real loopback stack; no API fixtures.
import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
const output = path.resolve('output/local-sre-20260919/browser-' + Date.now());
await mkdir(output, { recursive: true });
const chrome = process.env.MINI_DROP_ACCEPTANCE_CHROME || path.join(process.env.LOCALAPPDATA, 'ms-playwright/chromium-1161/chrome-win/chrome.exe');
const browser = spawn(chrome, ['--headless=new', '--remote-debugging-port=9337', `--user-data-dir=${path.join(os.tmpdir(), `mini-drop-local-${process.pid}`)}`, '--no-first-run', 'about:blank'], { windowsHide: true, stdio: 'ignore' });
const pause = ms => new Promise(r => setTimeout(r, ms));
const errors = [];
const base = process.env.MINI_DROP_BROWSER_BASE_URL || 'http://127.0.0.1:18080';
if (!['http://127.0.0.1:18080', 'https://120.24.187.205'].includes(base)) throw new Error('Unsupported verification host');
let socket;
try {
  let target;
  for (let i = 0; i < 40; i++) {
    try { target = await (await fetch('http://127.0.0.1:9337/json/new?about:blank', {method:'PUT'})).json(); break; } catch { await pause(250); }
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
  const id = process.argv[2];
  await send('Page.navigate',{url:base+'/ai-diagnosis'+(id?'?case='+encodeURIComponent('drop_insight_v2:'+id):'')});
  let text='';
  for(let i=0;i<40;i++) {
    await pause(500);
    const r=await send('Runtime.evaluate',{expression:'document.body.innerText',returnByValue:true});
    text=r.result.value || '';
    if(text.includes('新建诊断') || text.includes('诊断工作台')) break;
  }
  await pause(4000);
  const shot=await send('Page.captureScreenshot',{format:'png'});
  await writeFile(path.join(output,'real-local.png'),Buffer.from(shot.data,'base64'));
  const r=await send('Runtime.evaluate',{expression:'document.body.innerText',returnByValue:true});
  text=r.result.value || '';
  const result={url:base+'/ai-diagnosis',diagnosis_id:id,errors,body:text,passed:errors.length===0 && text.includes('诊断') && text.length>200};
  await writeFile(path.join(output,'result.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify({passed:result.passed,errors,text_length:text.length,output}));
  if(!result.passed) process.exitCode=1;
} finally { socket?.close(); browser.kill(); }
