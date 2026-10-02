import { spawn } from 'node:child_process';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';

const output = path.resolve(process.env.MINI_DROP_BROWSER_OUTPUT);
const expected = JSON.parse(await readFile(process.env.MINI_DROP_BROWSER_EXPECTATIONS, 'utf8'));
const base = process.env.MINI_DROP_BROWSER_BASE_URL;
if (base !== 'https://120.24.187.205') throw Error('Unexpected verification host');
await mkdir(output, { recursive: true });
const profile = path.join(os.tmpdir(), 'mini-drop-retired-score-browser-' + process.pid);
const browser = spawn(process.env.MINI_DROP_ACCEPTANCE_CHROME, [
  '--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`,
  '--no-first-run', '--disk-cache-size=0', 'about:blank',
], { windowsHide: true, stdio: 'ignore' });
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const redact = value => String(value).replaceAll(process.env.MINI_DROP_API_KEY, '<REDACTED_API_KEY>');
const errors = [], warnings = [], network = new Map(), checks = [], layouts = [], hashes = [];
const retiredAssetRequests = [];
let explicitRetiredAssetValidation = false;
let socket, send, evaluate, shot;
let result = { passed: false, scope: expected.scope };

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
    const timer = setTimeout(() => { pending.delete(id); reject(Error('CDP timeout: ' + method)); }, 90000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params }));
  });
  socket.addEventListener('close', () => {
    for (const callback of pending.values()) {
      clearTimeout(callback.timer); callback.reject(Error('Browser connection closed'));
    }
    pending.clear();
  });
  socket.addEventListener('message', event => {
    const message = JSON.parse(String(event.data));
    const p = message.params;
    // Inject credentials only into this host's read-only API requests. Never
    // write headers to evidence, storage, screenshots or local browser config.
    if (message.method === 'Fetch.requestPaused') {
      const url = new URL(p.request.url);
      const headers = Object.entries(p.request.headers)
        .filter(([name]) => name.toLowerCase() !== 'x-api-key')
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
    if (message.method === 'Network.requestWillBeSent') {
      const row = { url: p.request.url, method: p.request.method, start: p.timestamp };
      const url = new URL(p.request.url);
      if (url.origin === base && url.pathname === '/report-assets/performance-audit/index.json') {
        row.read_origin = explicitRetiredAssetValidation ? 'EXPLICIT_SCRIPT_MARKER_VALIDATION' : 'UI_AUTOMATIC';
        retiredAssetRequests.push({ url: row.url, method: row.method, read_origin: row.read_origin });
      }
      network.set(p.requestId, row);
    }
    if (message.method === 'Network.responseReceived') {
      Object.assign(network.get(p.requestId) || {}, { status: p.response.status, protocol: p.response.protocol, response: p.timestamp });
      if (p.response.status >= 400) errors.push({ type: 'HTTP', url: p.response.url, status: p.response.status });
    }
    if (message.method === 'Network.loadingFinished' && network.has(p.requestId)) network.get(p.requestId).end = p.timestamp;
    if (message.method === 'Network.loadingFailed') {
      Object.assign(network.get(p.requestId) || {}, { failed: p.errorText, canceled: p.canceled });
      if (!p.canceled) errors.push({ type: 'NETWORK', url: network.get(p.requestId)?.url, message: p.errorText });
    }
    if (message.method === 'Runtime.exceptionThrown') errors.push({ type: 'JS', message: redact(p.exceptionDetails.exception?.description || p.exceptionDetails.text) });
    if (message.method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(p.type)) {
      const item = { type: 'CONSOLE_' + p.type.toUpperCase(), message: redact(p.args.map(x => x.description ?? x.value ?? '').join(' ')) };
      (p.type === 'error' ? errors : warnings).push(item);
    }
    const callback = pending.get(message.id);
    if (callback) {
      clearTimeout(callback.timer); pending.delete(message.id);
      message.error ? callback.reject(Error(message.error.message)) : callback.resolve(message.result);
    }
  });
  evaluate = async expression => {
    const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw Error('Page evaluation: ' + redact(r.exceptionDetails.exception?.description || r.exceptionDetails.text));
    return r.result.value;
  };
  shot = async name => {
    const r = await send('Page.captureScreenshot', { format: 'png' });
    await writeFile(path.join(output, name + '.png'), Buffer.from(r.data, 'base64'));
  };
  const until = async (expression, label = 'expected page state') => {
    for (let i = 0; i < 140; i++) {
      if (await evaluate(expression)) return;
      await pause(300);
    }
    throw Error('Timed out waiting for ' + label);
  };
  const clickLabel = async (selector, label) => until(`(() => {
    const item = [...document.querySelectorAll(${JSON.stringify(selector)})].find(x => x.textContent.includes(${JSON.stringify(label)}));
    if (!item) return false; item.click(); return true;
  })()`, label);
  const width = async value => {
    await send('Emulation.setDeviceMetricsOverride', { width: value, height: 1000, deviceScaleFactor: 1, mobile: value === 375 });
    await pause(180);
  };
  const captureLayouts = async (name, expectCards = null) => {
    for (const value of [1440, 1024, 768, 375]) {
      await width(value);
      const item = await evaluate(`({width:innerWidth,body_width:document.documentElement.clientWidth,body_scroll:document.documentElement.scrollWidth,cards:document.querySelectorAll('.fault-scenario').length})`);
      layouts.push({ page: name, ...item });
      if (item.body_scroll > item.body_width + 1 || (expectCards !== null && item.cards !== expectCards)) throw Error('Layout overflow or missing cards: ' + name + '/' + value);
      await shot(name + '-' + value);
    }
    await width(1440);
  };
  const fetchJSON = url => evaluate(`fetch(${JSON.stringify(url)}, {cache:'no-store'}).then(async r => {if(!r.ok)throw Error('HTTP '+r.status);return r.json();})`);
  const checkBytes = async (url, expectedSHA, metadata = {}) => {
    const actual = await evaluate(`fetch(${JSON.stringify(url)}, {cache:'no-store'}).then(async r => {
      if (!r.ok) throw Error('Artifact HTTP ' + r.status);
      const bytes = await r.arrayBuffer();
      const sha256 = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(x => x.toString(16).padStart(2,'0')).join('');
      return {sha256, bytes:bytes.byteLength};
    })`);
    if (actual.sha256 !== expectedSHA || (metadata.bytes !== undefined && actual.bytes !== metadata.bytes)) throw Error('Downloaded artifact SHA/length mismatch: ' + url);
    hashes.push({ ...metadata, url, ...actual });
  };
  const openEvaluation = async () => {
    await send('Page.navigate', { url: base + '/ai-diagnosis' });
    await clickLabel('.ant-segmented-item-label', '案例验证');
    await until(`document.querySelectorAll('.fault-scenario').length === ${expected.current.registered_scenarios}`, 'registered scenario cards');
    const served = await fetchJSON('/report-assets/engineering-diagnosis/index.json');
    if (JSON.stringify(served) !== JSON.stringify(expected.current)) throw Error('Browser index differs from generated source contract');
    for (const text of [
      `工程诊断判断通过 ${served.diagnosis_accepted}/${served.evaluated_scenarios}`,
      `异常路径定位 ${served.localization_accepted}/${served.evaluated_scenarios}`,
      `有效反证 ${served.refuted}`, `待验收 ${served.not_evaluated.length} 类`,
      `当前包含 ${served.fresh_live_scenarios} 类新真机实验、${served.regraded_prior_scenarios} 类此前真实记录`,
    ]) await until(`document.body.innerText.includes(${JSON.stringify(text)})`, text);
    const cards = await evaluate(`[...document.querySelectorAll('.fault-scenario')].map(x=>x.innerText)`);
    for (const item of served.cases) {
      const card = cards.find(text => text.includes(item.title));
      if (!card) throw Error('Scenario card missing: ' + item.scenario_id);
      const label = item.localization_accepted ? '工程定位通过' : item.outcome === 'REFUTED' ? '判断通过 · 异常假设被反驳' : null;
      if (label && !card.includes(label)) throw Error('Card grade disagrees with indexed outcome: ' + item.scenario_id);
    }
    const retiredTerms = ['0/21', '原始因果根因', '为什么旧 21', '为什么旧21', '历史严格复验', '根因未通过', '查看复验诊断', '默认复验'];
    await evaluate(`(() => { document.querySelectorAll('.fault-scenario-details').forEach(x => { x.open = true; }); return true; })()`);
    const overviewBody = await evaluate('document.body.innerText');
    if (retiredTerms.some(text => overviewBody.includes(text))) throw Error('Retired strict score remains in current overview or expanded scenario details');
    const oldHeaders = await evaluate(`[...document.querySelectorAll('.ant-collapse-header')].some(x => /为什么旧.?21|历史.*根因/.test(x.textContent))`);
    if (oldHeaders) throw Error('Retired historical root-score fold remains in current overview');
    await evaluate(`(() => { document.querySelectorAll('.fault-scenario-details').forEach(x => { x.open = false; }); return true; })()`);
    const faultResponse = await fetchJSON('/api/v2/showcases/fault-plaza');
    // The public API uses a code/data envelope; static report assets do not.
    if (faultResponse.code !== 0 || !Array.isArray(faultResponse.data?.scenarios)) throw Error('Fault catalog API returned an invalid success envelope');
    const faultCatalog = faultResponse.data;
    const retiredFields = ['latest_acceptance', 'acceptance_level', 'root_cause_accepted', 'passed', 'fix_verified'];
    if (faultCatalog.scenarios.length !== 21 || faultCatalog.scenarios.some(row => row.active || retiredFields.some(key => Object.hasOwn(row, key)))) throw Error('Fault API changed catalog size/state or still publishes retired strict grades');
    if (JSON.stringify(faultCatalog.scenarios.map(x => x.scenario_id).sort()) !== JSON.stringify(served.cases.map(x => x.scenario_id).sort())) throw Error('Fault catalog and current engineering registry differ');
    return served;
  };
  const openDiagnosis = async item => {
    await send('Page.navigate', { url: base + '/ai-diagnosis?case=' + encodeURIComponent('drop_insight_v2:' + item.diagnosis_id) });
    await until(`location.search.includes(${JSON.stringify(item.diagnosis_id)}) && !!document.querySelector('.observation-assessment')`, 'diagnosis ' + item.scenario_id);
    await clickLabel('.ant-segmented-item-label', '调查记录');
    if (item.reports?.length) {
      await until(`!!document.querySelector('.diagnosis-conclusion-card')`, 'persisted conclusion ' + item.scenario_id);
    } else {
      await until(`document.body.innerText.includes('未形成报告') || document.body.innerText.includes('本轮证据不足')`, 'honest absent-report state ' + item.scenario_id);
    }
    if (item.localization_accepted) await until(`document.body.innerText.includes('性能路径已定位，根因仍待确认')`, 'localized boundary');
    if (item.outcome === 'REFUTED') await until(`document.querySelector('.diagnosis-finding')?.innerText.includes('已测量，异常假设被反驳')`, 'measured refutation');
    const body = await evaluate('document.body.innerText');
    if (body.includes('因果根因已验证')) throw Error('Engineering observation promoted to causal root: ' + item.scenario_id);
    await evaluate(`(() => {
      const header = [...document.querySelectorAll('.ant-collapse-header')].find(x => x.textContent.includes('完整调查过程'));
      if (header && header.getAttribute('aria-expanded') !== 'true') header.click();
      return true;
    })()`);
    if (item.reports?.length || item.evidence_ids?.length)
      await until(`document.querySelectorAll('.diagnosis-evidence-id').length > 0`, 'evidence cards');
    // Evidence cards and task previews load independently. The preview's result
    // link appears only after its task, artifact and content requests finish.
    // Wait for every pinned completed task; a real missing link still times out.
    const requiredTaskLinks = (item.completed_task_ids || [])
      .filter(tid => item.downloads?.some(download => download.task_id === tid))
      .map(tid => '/task/' + tid);
    if (requiredTaskLinks.length)
      await until(`(() => {
        const links = [...document.querySelectorAll('a[href^="/task/"]')].map(x => x.getAttribute('href'));
        return ${JSON.stringify(requiredTaskLinks)}.every(link => links.includes(link));
      })()`, 'completed task result links ' + item.scenario_id);
    const links = await evaluate(`[...document.querySelectorAll('a[href^="/task/"]')].map(x => x.getAttribute('href'))`);
    const evidenceIDs = await evaluate(`[...document.querySelectorAll('.diagnosis-evidence-id')].map(x => x.textContent)`);
    checks.push({ scenario_id: item.scenario_id, diagnosis_id: item.diagnosis_id, outcome: item.outcome,
      summary: await evaluate(`document.querySelector('.diagnosis-finding')?.innerText || ''`), evidenceIDs, links, body });
    return { links, evidenceIDs };
  };

  await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
  await send('Fetch.enable', { patterns: [{ urlPattern: base + '/api/*', requestStage: 'Request' }] });
  await width(1440);
  const served = await openEvaluation();
  await captureLayouts('current-engineering', served.registered_scenarios);
  if (retiredAssetRequests.length) throw Error('Current UI still automatically requests the retired performance asset');
  explicitRetiredAssetValidation = true;
  let retiredMarker;
  try {
    retiredMarker = await fetchJSON('/report-assets/performance-audit/index.json');
  } finally {
    explicitRetiredAssetValidation = false;
  }
  if (JSON.stringify(retiredMarker) !== JSON.stringify(expected.retired_marker) || Object.keys(retiredMarker).length !== 4) throw Error('Old public asset still exposes historical grades instead of the exact replacement marker');
  await shot('retired-score-current-overview');
  await clickLabel('.eval-center-nav .ant-segmented-item-label', '工程修复回归');
  await until(`document.body.innerText.includes(${JSON.stringify(expected.engineering.cases.length + ' 个缺陷闭环已验证')})`, 'engineering defect regressions');
  const engineering = await fetchJSON('/report-assets/engineering-cases/index.json');
  if (JSON.stringify(engineering) !== JSON.stringify(expected.engineering)) throw Error('Engineering regression index changed');
  for (const item of engineering.cases) for (const file of item.evidence) {
    const url = '/report-assets/engineering-cases/' + file.filename;
    if (!await evaluate(`[...document.querySelectorAll('a')].some(x => x.getAttribute('href') === ${JSON.stringify(url)})`)) throw Error('Engineering raw evidence link missing: ' + file.filename);
    await checkBytes(url, file.sha256, { scope: 'ENGINEERING_DEFECT_REGRESSION', filename: file.filename });
  }
  await captureLayouts('four-engineering-regressions');

  for (const item of expected.health_cases) {
    await send('Page.navigate', { url: base + '/ai-diagnosis?case=' + encodeURIComponent('drop_insight_v2:' + item.diagnosis_id) });
    await until(`document.body.innerText.includes(${JSON.stringify(item.label)}) && document.body.innerText.includes('检查结果与下一步已生成')`, 'health state ' + item.name);
    if (await evaluate(`!!document.querySelector('.diagnosis-finding')`)) throw Error('Health status rendered as root cause');
    await shot('health-' + item.name);
    checks.push({ ...item, body: await evaluate('document.body.innerText') });
  }
  await send('Page.navigate', { url: base + '/ai-diagnosis?case=' + encodeURIComponent('drop_insight_v2:' + expected.business_diagnosis_id) });
  await until(`document.body.innerText.includes('find_longest_match')`, 'business source path');
  if (await evaluate(`document.body.innerText.includes('因果根因已验证')`)) throw Error('Business observation promoted to causal root');
  await shot('business-path');

  for (const item of expected.old_paths) {
    await openDiagnosis(item);
    await captureLayouts('old-' + item.scenario_id);
  }
  const history = await send('Page.getNavigationHistory');
  const prior = history.entries[history.currentIndex - 1], last = history.entries[history.currentIndex];
  const oldNetwork = expected.old_paths.find(x => x.scenario_id === 'go-network-latency');
  if (!prior?.url.includes(oldNetwork.diagnosis_id)) throw Error('Unexpected old-path navigation history');
  await send('Page.navigateToHistoryEntry', { entryId: prior.id });
  await until(`location.search.includes(${JSON.stringify(oldNetwork.diagnosis_id)}) && !!document.querySelector('.observation-assessment')`, 'history back HTTP');
  await shot('history-back-http');
  await send('Page.navigateToHistoryEntry', { entryId: last.id });
  await until(`document.querySelector('.diagnosis-finding')?.innerText.includes('已测量，异常假设被反驳')`, 'history forward I/O');
  await shot('history-forward-io');

  for (const item of expected.new_cases) {
    const observed = await openDiagnosis(item);
    const present = item.evidence_ids.filter(id => observed.evidenceIDs.some(text => text.includes(id)));
    if (item.evidence_ids.length && !present.length) throw Error('New case exposes no pinned evidence reference: ' + item.scenario_id);
    for (const taskId of item.completed_task_ids.filter(tid => item.downloads.some(d => d.task_id === tid))) {
      if (!observed.links.includes('/task/' + taskId)) throw Error('New evidence result link missing: ' + item.scenario_id + '/' + taskId);
    }
    for (const download of item.downloads) {
      const url = '/api/tasks/' + encodeURIComponent(download.task_id) + '/artifacts/' + encodeURIComponent(download.artifact_type) + '/download';
      await checkBytes(url, download.sha256, { ...download, scenario_id: item.scenario_id, scope: 'NEW_LIVE_ENGINEERING_CAMPAIGN' });
    }
    await captureLayouts('new-' + item.scenario_id);
  }
  // Exercise the overview's actual navigation button after all direct routes.
  await openEvaluation();
  const cpu = served.cases.find(x => x.scenario_id === 'go-cpu-hotspot');
  await until(`document.querySelectorAll('section[aria-label="当前工程诊断验收"] button').length === ${served.cases.length}`, 'engineering report buttons');
  await evaluate(`document.querySelectorAll('section[aria-label="当前工程诊断验收"] button')[${served.cases.findIndex(x => x.scenario_id === cpu.scenario_id)}].click();true`);
  await until(`document.body.innerText.includes('goCPUHotFunction') && document.body.innerText.includes('性能路径已定位，根因仍待确认')`, 'CPU report button');
  await shot('engineering-cpu-button');
  if (hashes.filter(x => x.scope === 'NEW_LIVE_ENGINEERING_CAMPAIGN').length !== 32 || hashes.filter(x => x.scope === 'ENGINEERING_DEFECT_REGRESSION').length !== 9) throw Error('Pinned artifact download count changed');
  if (retiredAssetRequests.length !== 1 || retiredAssetRequests[0].read_origin !== 'EXPLICIT_SCRIPT_MARKER_VALIDATION' || retiredAssetRequests[0].method !== 'GET') throw Error('Retired asset requests must be exactly one explicit marker validation and no UI requests');
  result = { passed: errors.length === 0, scope: expected.scope,
    certificate_verification: 'Python private CA/hostname verified; Chrome default trust; no TLS bypass',
    current_campaign_id: served.current_campaign_id, engineering_diagnosis: served,
    retired_score_asset: retiredMarker, retired_asset_requests: retiredAssetRequests, legacy_score_ui_absent: true, fault_catalog_score_fields_retired: true, engineering_defect_cases: engineering.cases.length,
    new_case_count: expected.new_cases.length, history_navigation_checked: true,
    engineering_button_checked: true, layouts, checks, verified_downloads: hashes, errors, warnings };
  console.log(JSON.stringify({ passed: result.passed, campaign: served.current_campaign_id,
    grades: { accepted: served.diagnosis_accepted, localized: served.localization_accepted, refuted: served.refuted },
    new_cases: expected.new_cases.length, sha_downloads: hashes.length, layout_checks: layouts.length, retired_asset_requests: retiredAssetRequests, errors }));
  if (!result.passed) process.exitCode = 1;
} catch (error) {
  result = { ...result, error: redact(error.stack || error.message), errors, warnings, layouts, checks, verified_downloads: hashes, retired_asset_requests: retiredAssetRequests };
  try {
    result.failure_state = await evaluate(`({url:location.href,body:document.body.innerText})`);
    await shot('failure');
  } catch { /* Keep the original browser failure. */ }
  console.error(JSON.stringify({ passed: false, message: redact(error.message), errors }));
  process.exitCode = 1;
} finally {
  await writeFile(path.join(output, 'network.json'), JSON.stringify([...network.values()], null, 2));
  await writeFile(path.join(output, 'result.json'), redact(JSON.stringify(result, null, 2)));
  socket?.close(); browser.kill();
}
