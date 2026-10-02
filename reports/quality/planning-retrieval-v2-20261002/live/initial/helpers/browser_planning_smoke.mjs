// Adapted from the trusted-CA retired-score browser helper; never mutate production.
import { spawn } from 'node:child_process';
import { mkdir, writeFile, readFile, rm } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';

const output = path.resolve(process.env.MINI_DROP_BROWSER_OUTPUT);
const expected = JSON.parse(await readFile(process.env.MINI_DROP_BROWSER_EXPECTATIONS, 'utf8'));
const base = process.env.MINI_DROP_BROWSER_BASE_URL;
if (base !== 'https://120.24.187.205' || !process.env.MINI_DROP_API_KEY) throw Error('Unexpected host or missing in-memory authentication');
const tempRoot = path.resolve(os.tmpdir());
const profile = path.join(tempRoot, 'mini-drop-planning-browser-' + process.pid);
await mkdir(profile, { recursive: false });
const browser = spawn(process.env.MINI_DROP_ACCEPTANCE_CHROME, [
  '--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`,
  '--no-first-run', '--disk-cache-size=0', 'about:blank',
], { windowsHide: true, stdio: 'ignore' });
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const redact = value => String(value).replaceAll(process.env.MINI_DROP_API_KEY, '<REDACTED_API_KEY>');
const labels = { NORMAL: '描述范围内未提出异常', INSUFFICIENT_EVIDENCE: '缺少必要观测', REFUSED: '拒绝此请求' };
const errors = [], warnings = [], layouts = [], checks = [], noAnswerChecks = [], screenshots = [], network = new Map();
let socket, send, evaluate;
let result = { passed: false, scope: expected.scope, source_head: expected.source_head, release: expected.release };

try {
  let target;
  for (let i = 0; i < 40; i++) {
    try {
      const port = (await readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0];
      target = await (await fetch('http://127.0.0.1:' + port + '/json/new?about:blank', { method: 'PUT' })).json();
      break;
    } catch { await pause(250); }
  }
  if (!target) throw Error('Browser unavailable');
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', reject, { once: true });
  });
  let sequence = 0;
  const pending = new Map();
  send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(Error('CDP timeout: ' + method)); }, 45000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params }));
  });
  socket.addEventListener('message', event => {
    const message = JSON.parse(String(event.data));
    const p = message.params;
    if (message.method === 'Fetch.requestPaused') {
      const url = new URL(p.request.url);
      const headers = Object.entries(p.request.headers).filter(([name]) => name.toLowerCase() !== 'x-api-key')
        .map(([name, value]) => ({ name, value: String(value) }));
      if (url.origin === base && url.pathname.startsWith('/api/')) {
        if (!['GET', 'HEAD', 'OPTIONS'].includes(p.request.method)) {
          errors.push({ type: 'UNEXPECTED_MUTATION', method: p.request.method, url: p.request.url });
          void send('Fetch.failRequest', { requestId: p.requestId, errorReason: 'BlockedByClient' }).catch(() => {});
          return;
        }
        headers.push({ name: 'X-API-Key', value: process.env.MINI_DROP_API_KEY });
      }
      void send('Fetch.continueRequest', { requestId: p.requestId, headers }).catch(error => errors.push({ type: 'AUTH_INTERCEPT', message: redact(error.message) }));
    }
    if (message.method === 'Runtime.exceptionThrown') errors.push({ type: 'JS_EXCEPTION', message: redact(p.exceptionDetails.text) });
    if (message.method === 'Network.requestWillBeSent') network.set(p.requestId, { url: p.request.url, method: p.request.method });
    if (message.method === 'Network.responseReceived' && p.response.status >= 400) errors.push({ type: 'HTTP_ERROR', status: p.response.status, url: p.response.url });
    if (message.method === 'Network.loadingFailed') {
      const row = { type: 'NETWORK_FAILURE', ...network.get(p.requestId), reason: redact(p.errorText) };
      (p.canceled ? warnings : errors).push(row);
    }
    if (message.id && pending.has(message.id)) {
      const callback = pending.get(message.id); pending.delete(message.id); clearTimeout(callback.timer);
      if (message.error) callback.reject(Error(redact(message.error.message))); else callback.resolve(message.result);
    }
  });
  evaluate = async expression => {
    const response = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (response.exceptionDetails) throw Error(redact(response.exceptionDetails.exception?.description || response.exceptionDetails.text));
    return response.result.value;
  };
  const until = async (expression, label) => {
    for (let i = 0; i < 120; i++) { if (await evaluate(`Boolean(${expression})`)) return; await pause(250); }
    throw Error('Timed out waiting for ' + label);
  };
  const clickLabel = async label => until(`(() => { const item=[...document.querySelectorAll('.ant-segmented-item-label')].find(x=>x.textContent.includes(${JSON.stringify(label)})); if(!item)return false;item.click();return true;})()`, label);
  const capture = async name => {
    for (const width of [1440, 1024, 768, 375]) {
      await send('Emulation.setDeviceMetricsOverride', { width, height: 1000, deviceScaleFactor: 1, mobile: width === 375 });
      await pause(200);
      const layout = await evaluate('({viewport:innerWidth,width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})');
      if (layout.scroll > layout.width + 1) throw Error('Layout overflow: ' + name + '/' + width);
      layouts.push({ page: name, ...layout });
      const shot = await send('Page.captureScreenshot', { format: 'png' });
      const file = name + '-' + width + '.png';
      await writeFile(path.join(output, file), Buffer.from(shot.data, 'base64')); screenshots.push(file);
    }
    await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  };
  await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
  await send('Network.setCacheDisabled', { cacheDisabled: true });
  await send('Fetch.enable', { patterns: [{ urlPattern: base + '/api/*', requestStage: 'Request' }] });
  for (const item of expected.cases) {
    await send('Page.navigate', { url: base + '/ai-diagnosis?case=' + encodeURIComponent('drop_insight_v2:' + item.diagnosis_id) });
    await until(`location.search.includes(${JSON.stringify(item.diagnosis_id)}) && document.querySelector('.ai-diagnosis-page')`, item.diagnosis_id);
    await clickLabel('调查记录');
    await until(`document.body.innerText.includes(${JSON.stringify(labels[item.actual_disposition])}) && document.body.innerText.includes('规划结果已记录')`, 'genuine persisted planning card');
    const records = await evaluate(`fetch('/api/v2/diagnoses/${encodeURIComponent(item.diagnosis_id)}/events',{cache:'no-store'}).then(r=>r.json())`);
    const rows = Array.isArray(records.data) ? records.data : records.data?.items;
    const event = rows?.find(row => row.event_type === 'planner.output_recorded');
    const payload = event?.payload || event?.payload_json;
    if (payload?.planning_output?.disposition !== item.actual_disposition || payload?.health_check_performed !== false || payload?.is_evidence !== false) throw Error('UI/API persisted planning lineage differs');
    const body = await evaluate('document.body.innerText');
    if (!body.includes('不代表业务健康') || !body.includes('未新增采集任务')) throw Error('Planning scope disclaimer is missing');
    if (body.includes('检查结果：正常') || body.includes('根因已验证')) throw Error('Planning card is being presented as verified health or cause');
    checks.push({ name: item.name, diagnosis_id: item.diagnosis_id, disposition: item.actual_disposition, persisted_event_id: event.event_id || event.id, passed: true });
    await capture(item.name);
  }
  for (const item of expected.no_answer_cases || []) {
    await send('Page.navigate', { url: base + '/ai-diagnosis?case=' + encodeURIComponent('drop_insight_v2:' + item.diagnosis_id) });
    await until(`location.search.includes(${JSON.stringify(item.diagnosis_id)}) && document.querySelector('.diagnosis-technical-details')`, 'persisted retrieval session');
    const response = await evaluate(`fetch('/api/v2/diagnoses/${encodeURIComponent(item.diagnosis_id)}/retrievals',{cache:'no-store'}).then(r=>r.json())`);
    const rows = Array.isArray(response.data) ? response.data : response.data?.items;
    const matches = rows?.filter(row => item.retrieval_event_ids.includes(row.event_id));
    if (!matches || matches.length !== item.retrieval_event_ids.length || matches.some(row => {
      const trace = row.retrieval_trace;
      return trace?.outcome !== 'NO_RELEVANT_KNOWLEDGE' || trace?.health_scope !== 'RETRIEVAL_ONLY'
        || trace.no_match_is_normal !== false || trace.matches?.length !== 0 || trace.evidence_contract?.is_evidence !== false;
    })) throw Error('Browser/API no-answer retrieval lineage differs');
    await evaluate("(() => { document.querySelector('.diagnosis-technical-details').open=true;document.querySelector('.agent-cockpit-card.is-rag').click();return true;})()");
    await until("document.querySelector('.agent-cockpit-modal .ant-collapse-header')", 'actual retrieval modal');
    await evaluate("(() => {document.querySelectorAll('.agent-cockpit-modal .ant-collapse-item:not(.ant-collapse-item-active) .ant-collapse-header').forEach(x=>x.click());return true;})()");
    await until("document.querySelector('.agent-cockpit-modal')?.innerText.includes('未找到相关知识') && document.querySelector('.agent-cockpit-modal')?.innerText.includes('这不表示业务正常，也不是故障证据')", 'genuine no-answer scope notice');
    noAnswerChecks.push({ ...item, passed: true, health_scope: 'RETRIEVAL_ONLY', no_match_is_normal: false });
    await capture('rag-' + item.name);
    await evaluate("(() => {document.querySelector('.agent-cockpit-modal .ant-modal-close').click();return true;})()");
  }
  await send('Page.navigate', { url: base + '/ai-diagnosis' }); await clickLabel('案例验证');
  await until("document.querySelectorAll('.fault-scenario').length === 21", 'current engineering cases');
  const current = await evaluate("fetch('/report-assets/engineering-diagnosis/index.json',{cache:'no-store'}).then(r=>r.json())");
  if (current.diagnosis_accepted !== 21 || current.localization_accepted !== 6 || current.refuted !== 8) throw Error('Current engineering grades drifted');
  await evaluate("(() => {document.querySelectorAll('.fault-scenario-details').forEach(x=>{x.open=true;});return true;})()");
  const body = await evaluate('document.body.innerText');
  if (['0/21', '原始因果根因', '为什么旧 21', '为什么旧21', '历史严格复验', '根因未通过', '查看复验诊断', '默认复验'].some(term => body.includes(term))) throw Error('Retired default score reappeared');
  await evaluate("(() => {document.querySelectorAll('.fault-scenario-details').forEach(x=>{x.open=false;});return true;})()");
  await capture('current-engineering');
  if (errors.length) throw Error('Browser errors were recorded');
  result = { ...result, passed: true, status: checks.length === expected.total_live_cases ? 'PASSED' : 'PASSED_AVAILABLE_CASES', rendered_live_cases: checks.length, total_live_cases: expected.total_live_cases, normal_certificate_verification: true, writes_performed: 0, checks, no_answer_checks: noAnswerChecks, layouts, screenshots, legacy_score_ui_absent: true, verified_downloads: [], engineering_summary: { judgments: 21, paths: 6, refutations: 8 } };
} catch (error) {
  result.error = redact(error.stack || error.message);
} finally {
  result.errors = errors; result.warnings = warnings;
  await writeFile(path.join(output, 'result.json'), JSON.stringify(result, null, 2) + '\n');
  socket?.close(); browser.kill();
  await new Promise(resolve => { if (browser.exitCode !== null) resolve(); else { browser.once('exit', resolve); setTimeout(resolve, 3000); } });
  if (path.dirname(profile) !== tempRoot || path.basename(profile) !== 'mini-drop-planning-browser-' + process.pid) throw Error('Owned browser profile cleanup escaped its boundary');
  await rm(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 200 });
}
process.stdout.write(JSON.stringify({ passed: result.passed, status: result.status, rendered_live_cases: result.rendered_live_cases, screenshots: screenshots.length, errors: errors.length }) + '\n');
if (!result.passed) process.exitCode = 1;
