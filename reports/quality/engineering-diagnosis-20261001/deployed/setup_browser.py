from pathlib import Path
stage=Path(__file__).resolve().parent
old=stage.parent/'performance-localization-20261001'
script=(old/'browser_performance.mjs').read_text(encoding='utf-8')
needle="  const layouts=[];"
insert='''  await until(`document.body.innerText.includes('工程诊断判断通过 3/3') && document.body.innerText.includes('异常路径定位 2/3') && document.body.innerText.includes('待验收 18 类')`);
  const current=await evaluate(`fetch('/report-assets/engineering-diagnosis/index.json',{cache:'no-store'}).then(r=>r.json())`);
  if(current.profile!=='engineering-diagnosis.v1'||current.diagnosis_accepted!==3||current.localization_accepted!==2||current.refuted!==1||current.fresh_live_run!==false||current.not_evaluated.length!==18||current.cases.some(c=>c.causal_root_cause_verified!==false))throw Error('Wrong engineering score or scope');
  const cardGrades=await evaluate(`Array.from(document.querySelectorAll('.fault-scenario')).map(x=>x.innerText).filter(x=>x.includes('工程定位通过')||x.includes('判断通过 · 异常假设被反驳'))`);
  if(cardGrades.length!==3||cardGrades.filter(x=>x.includes('工程定位通过')).length!==2)throw Error('Current card grade hidden by old historical failures');
  const currentBody=await evaluate('document.body.innerText');
  await shot('current-engineering-acceptance');
'''
assert needle in script
script=script.replace(needle,insert+needle)
needle="  const history=await send('Page.getNavigationHistory');"
# Keep existing backward/forward regression contiguous; button check follows it.
needle2="  await pause(300);\n  const pendingStreams="
insert2='''  await send('Page.navigate',{url:base+'/ai-diagnosis'});
  await until(`(()=>{const n=Array.from(document.querySelectorAll('.ant-segmented-item-label')).find(x=>x.textContent==='案例验证');if(n){n.click();return true;}return false;})()`);
  await until(`document.querySelectorAll('section[aria-label="当前工程诊断验收"] button').length===3`);
  await evaluate(`document.querySelector('section[aria-label="当前工程诊断验收"] button').click();true`);
  const cpuCase=current.cases.find(x=>x.scenario_id==='go-cpu-hotspot');
  await until(`document.body.innerText.includes('性能路径已定位，根因仍待确认') && document.body.innerText.includes('goCPUHotFunction')`);
  const buttonBody=await evaluate('document.body.innerText');
  if(!buttonBody.includes('goCPUHotFunction'))throw Error('Engineering diagnosis button opened the wrong report');
  await shot('engineering-cpu-button');
'''
assert needle2 in script
script=script.replace(needle2,insert2+needle2)
script=script.replace('history_navigation_checked:true,','history_navigation_checked:true,engineering_diagnosis:current,current_card_grades:cardGrades,current_overview_body:currentBody,engineering_button_checked:true,')
(stage/'browser_engineering.mjs').write_text(script,encoding='utf-8')
script=(old/'run_browser.py').read_text(encoding='utf-8')
script=script.replace("stage=Path(__file__).resolve().parent", "stage=Path(__file__).resolve().parent\nold=stage.parent/'performance-localization-20261001'")
script=script.replace("(stage/'health-diagnosis-id.txt')", "(old/'health-diagnosis-id.txt')")
script=script.replace("(stage/'final-grade/localization-acceptance.json')", "(old/'final-grade/localization-acceptance.json')")
script=script.replace('browser-r10','browser').replace('browser_performance.mjs','browser_engineering.mjs')
(stage/'run_browser.py').write_text(script,encoding='utf-8')
script=(old/'verify_runtime.py').read_text(encoding='utf-8')
script=script.replace("stage=Path(__file__).resolve().parent", "stage=Path(__file__).resolve().parent\nold=stage.parent/'performance-localization-20261001'")
script=script.replace("(stage/'release-r5/manifest.json')", "(old/'release-r5/manifest.json')")
script=script.replace("(stage/'release-r8/manifest.json')", "(stage/'release/manifest.json')")
script=script.replace("(stage/'runtime-before.json')", "(old/'runtime-before.json')")
(stage/'verify_runtime.py').write_text(script,encoding='utf-8')
print('Created independent browser and runtime receipts; old evidence retained.')
