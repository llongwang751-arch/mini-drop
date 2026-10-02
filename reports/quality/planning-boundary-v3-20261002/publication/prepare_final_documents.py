"""Publish current facts from actual pinned receipts while preserving user prose."""
from pathlib import Path
import hashlib
import importlib.util
import json
import subprocess
ROOT=Path(__file__).resolve().parents[3]
STAGE=Path(__file__).resolve().parent
PREFIX='reports/quality/planning-boundary-v3-20261002'
OUT=STAGE/'final-document-candidates'
def git(*args):return subprocess.check_output(['git','-c','core.autocrlf=false',*args],cwd=ROOT)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def read(name):return json.loads((ROOT/name).read_bytes())
def pin(name):return {'path':name,'sha256':sha((ROOT/name).read_bytes())}
def main():
 head=git('rev-parse','HEAD').decode().strip()
 release=read(PREFIX+'/deployment/final/manifest.json')
 assert release['git_head']==head and not git('diff','--cached','--name-only').strip()
 assert not OUT.exists()
 ci_name=PREFIX+'/ci/final/main-37004140437/summary.json';core_name=PREFIX+'/ci/final/clean-37004140452/reports/clean-stack/report.json'
 runtime_name=PREFIX+'/deployment/v3-publication-verification.json';knowledge_name=PREFIX+'/deployment/v3-knowledge-verification.json'
 live_name=PREFIX+'/live/smoke/summary.json';readback_name=PREFIX+'/live/live-contract-readback.json';browser_name=PREFIX+'/live/browser/result.json';audit_name=PREFIX+'/evaluation/independent-audit.json'
 ci=read(ci_name);core=read(core_name);runtime=read(runtime_name);knowledge=read(knowledge_name);live=read(live_name);browser=read(browser_name);audit=read(audit_name)
 model=read(PREFIX+'/evaluation/first-run/report.json');hybrid=read(PREFIX+'/retrieval/hybrid/report.json')
 assert ci['status']=='VERIFIED' and ci['successful_jobs']==14 and ci['source_head']==head
 assert core['status']=='PASSED' and runtime['healthy_containers']==13 and runtime['untouched_containers']==11
 readback=read(readback_name)
 assert live['status']=='FAILED' and readback['status']=='VERIFIED' and browser['passed'] and audit['status']=='VERIFIED'
 assert browser['status']=='PASSED_AVAILABLE_CASES' and browser['rendered_live_cases']==2 and browser['total_live_cases']==3
 assert all(item['source_head']==head for item in [live,readback,browser,audit,knowledge])
 assert readback['verified_planning_result_cases']==2 and readback['total_original_cases']==3 and readback['all_three_planning_results_passed'] is False
 assert all(r['actual_persisted_tasks']==0 for r in live['cases'])
 normal=next(r for r in live['cases'] if r['name']=='normal')
 assert normal['model_invocations']==0 and normal['planner_kind']=='SERVER_REQUEST_INTENT' and normal['actual_disposition']=='NORMAL'
 assert live['source_head']==browser['source_head']==runtime['source_head']==model['source_head']==hybrid['source_head']==head
 m=model['metrics'];h=hybrid['metrics'];counts=ci['main_suite_counts'];tag=release['release_tag']
 current=f"线上已发布 `{tag}`，应用源码 `{head}`；Worker/Analyzer各222个源码SHA和19篇公共知识对应的文件SHA通过，13个容器健康、11个容器未替换，Web/native/API/办公服务状态及数据保留。新Chroma快照{knowledge['current']['chunks']}个chunk READY，旧{knowledge['retained_old']['chunks']}个chunk仍READY且未改。"
 quality=f"精确源码主CI14/14：Python {counts['python']['passed']}通过/{counts['python']['skipped']}登记跳过，Web {counts['web_vitest']['passed']}、真实PostgreSQL {counts['postgres_python']['passed']}零跳过、Chromium固定数据{counts['chromium']['passed']}；Chroma专项通过。干净Linux核心栈87/87和10个实际阶段通过。"
 info=f"原明确纯描述问题的新真实会话返回NORMAL，来源SERVER_REQUEST_INTENT、诊断chat model_invocations=0；信息分支不创建PG检查点，不生成Task/Tool/Evidence/Report或健康事实。上游服务仍进行HYBRID查询embedding，不能宣称全链路AI调用为0。拒绝会话实际返回REFUSED，但原smoke因读取旧模型事件没有承诺的planner_kind字段报KeyError；独立只读核验确认输出和来源，原FAILED不改。缺测会话一次OpenAITimeoutError未产生输出，0重试且无新增采集任务。浏览器实际呈现2/3张规划卡、{len(browser['layouts'])}次布局检查通过；这三次现场尝试独立于32题实评。NORMAL只描述当前请求范围，真实体检仍需观测。"
 metrics=f"32新题零重试真实chat：HTTP成功{m['http_successes']}/32、超时{m['timeouts']}，结构{m['structure_valid_count']}/32，四态分类{m['disposition_correct_count']}/32，下一工具{m['tool_choice_expected_count']}/32。NORMAL与INSUFFICIENT_EVIDENCE各8/8、REFUSED7/8、INVESTIGATE5/8；四个未通过项为2超时、1拒绝误判NORMAL、1调查计划含不可执行判据，被DTO门禁拒绝。BM25 Recall@3 {m['recall_at_3']:.5f}/MRR {m['mrr_at_3']:.5f}，无答案误召回{m['no_answer_false_positive_count']}/16；HYBRID Recall@3 {h['recall_at_3']:.5f}/MRR {h['mrr_at_3']:.5f}，无答案误召回{h['no_answer_false_positive_count']}/16，实际后端{hybrid['actual_backend_counts']}。原始错误/超时保留分母，服务器0诊断chat不计模型准确率；合成规划不是实际因果根因成绩。"
 boundary='公共请求意图规则不按题ID实现；双重否定、数字性能观测、明确症状、未完调查及真实动作请求不能走信息收束。流程描述和动作授权分别判断。Java线程CPU、同宿主块设备争用、同步写反证由public catalog与官方来源支持；未知主体、否定、运行时与候选主能力准入保持。旧模型题/真值/分数与失败CI不改，新语料不回填旧模型成绩。'
 next_text='剩余改进优先级：供应商超时的稳定收束、模型拒绝边界和可执行计划表达、知识漏召回及无答案误召回。按本次公开失败类别改进通用规则后，使用新未曝光题验收；不能重试原题或改真值宣称新成绩。继续扩大有官方能力声明、能取得真实观测的知识覆盖。检索命中不能当Evidence，无答案不能当健康；当前项目已具备测开、后端和Agent面试演示链路，用户本人仍须读源码并彩排。'
 deployment='本次只更换两个Python后端的源码覆盖层；预部署磁盘实际测量按1GiB保留空间、8倍两服务覆盖层/发布文件/压缩包、16MiB新索引上界及精确旧发布拷贝计算，不删除镜像或数据。评测只读发布来源接口使用manifest.json；新发布按旧兼容路径建立指向source-manifest-20260930.json的相对软链接，canonical原字节未改。容量、来源兼容与旧快照保留均有独立回执。'
 summary='\n\n'.join([current,quality,info,metrics,boundary,deployment,next_text,'[当前固定SHA交付](CURRENT_DELIVERY.md)，[本次交付报告](../reports/architecture/planning-boundary-v3-20261002.md)。'])
 rows=[]
 def change(name,transform):
  clean=git('show',head+':'+name).decode();dirty=(ROOT/name).read_text(encoding='utf-8')
  new=transform(clean);target=OUT/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(new,encoding='utf-8',newline='\n')
  (ROOT/name).write_text(transform(dirty),encoding='utf-8',newline='\n');rows.append({'path':name,'clean_candidate':str(target),'sha256':sha(target.read_bytes())})
 def context(text):
  heading='## 2026-10-02 信息描述分类边界与知识覆盖（待部署验收）'
  assert text.count(heading)==1
  return text.replace(heading,'## 2026-10-02 信息描述分类边界与知识覆盖已部署\n\n'+summary+'\n\n## 历史实施快照：分类边界与知识覆盖',1).replace('修复后的精确 CI/发布来源待验收。','修复后的精确 CI/发布来源见最新回执。',1)
 for name in ['docs/PROJECT_CONTEXT.md','docs/RESTART_HANDOFF.md','docs/AGENT_RUNTIME.md']:change(name,context)
 def domain(text):
  anchor='当前仅实现和本地验证，部署及实评成绩待实际原始回执确认；以PROJECT_CONTEXT最上方为当前状态。'
  assert text.count(anchor)==1
  return text.replace(anchor,'## 最终来源和实评\n\n'+summary+'\n\n历史v2核验命令使用 reports/quality/planning-retrieval-v2-20261002/evaluation/original-source/scripts/evaluate_planning_retrieval_v2.py --verify-report 对原报告复算；当前v2 --check-freeze 应拒绝新语料。',1).replace('修复后的精确 CI/发布来源待验收。','修复后的精确 CI/发布来源见下述最终回执。',1)
 change('docs/PLANNING_BOUNDARY_V3.md',domain)
 change('docs/README.md',lambda t:t.replace('服务器纯信息描述合同、模型0审计、新知识快照与独立32题评估。','服务器纯信息描述合同、诊断chat0审计、新知识快照与独立32题评估。',1))
 for name in ['docs/REMAINING_WORK_20260930.md','docs/INTERVIEW_DEMO_GUIDE.md','docs/TEST_ENGINEERING.md']:
  change(name,lambda t:t.split('\n\n',1)[0]+'\n\n## 2026-10-02 分类边界与公共知识最新交付\n\n'+summary+'\n\n'+t.split('\n\n',1)[1])
 contract_name='contracts/interview_delivery.json';contract=json.loads(git('show',head+':'+contract_name))
 assert json.loads((ROOT/contract_name).read_bytes())==contract
 for k,n in [('production_release',PREFIX+'/deployment/final/manifest.json'),('production_ci',ci_name),('production_verification',runtime_name),('production_browser',browser_name)]:contract['sources'][k]=pin(n)
 for item in contract['additional_acceptances']:
  if item['title']=='当前源码干净 Linux 核心复刻':item['title']='历史v2源码干净 Linux 核心复刻'
  elif item['title']=='最终提示后的真实NORMAL补验':item['title']='历史v2最终提示后的真实NORMAL补验（原失败保留）'
 contract['additional_acceptances'] += [
  {'title':'当前v3源码干净Linux核心栈','status':'PASSED','scope':quality,'report':pin(core_name),'required_result':{'field':'status','value':'PASSED'}},
  {'title':'明确纯描述服务器收束及实际输出只读核验','status':'COMPLETED','scope':info,'report':pin(readback_name),'required_result':{'field':'status','value':'VERIFIED'}},
  {'title':'v3新冻结32题模型与检索实评','status':'COMPLETED','scope':metrics+' 独立代码复算一致；题目作者为维护者，不能声称第三方盲测。','report':pin(audit_name),'required_result':{'field':'status','value':'VERIFIED'}},
  {'title':'新知识快照发布与旧快照保留','status':'PASSED','scope':current,'report':pin(knowledge_name),'required_result':{'field':'knowledge_sha256_verified','value':True}}]
 raw=(json.dumps(contract,ensure_ascii=False,indent=2)+'\n').encode();target=OUT/contract_name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw);(ROOT/contract_name).write_bytes(raw);rows.append({'path':contract_name,'clean_candidate':str(target),'sha256':sha(raw)})
 spec=importlib.util.spec_from_file_location('v3_delivery_generator',ROOT/'scripts/build_interview_delivery.py');generator=importlib.util.module_from_spec(spec);spec.loader.exec_module(generator)
 rendered=generator.generate(root=ROOT);name='docs/CURRENT_DELIVERY.md';target=OUT/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(rendered,encoding='utf-8',newline='\n');(ROOT/name).write_bytes(target.read_bytes());rows.append({'path':name,'clean_candidate':str(target),'sha256':sha(target.read_bytes())})
 report_summary=summary.replace('[当前固定SHA交付](CURRENT_DELIVERY.md)','[当前固定SHA交付](../../docs/CURRENT_DELIVERY.md)').replace('，[本次交付报告](../reports/architecture/planning-boundary-v3-20261002.md)。','。')
 name='reports/architecture/planning-boundary-v3-20261002.md';raw=('# 分类边界与知识覆盖交付\n\n'+report_summary+'\n\n证据包含首轮Linux换行保护失败和修复、原始模型/检索记录、实际服务器来源和PG检查点、CI及部署SHA；完整清单见[SHA清单](../quality/planning-boundary-v3-20261002/manifest.json)。\n').encode();target=OUT/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw);(ROOT/name).write_bytes(raw);rows.append({'path':name,'clean_candidate':str(target),'sha256':sha(raw)})
 (STAGE/'final-document-source-map.json').write_text(json.dumps({'base_head':head,'files':rows},indent=2)+'\n',encoding='utf-8')
 print(json.dumps({'status':'GENERATED_FROM_PINNED_ACTUAL_RECEIPTS','source_head':head,'focused_files':len(rows)}))
if __name__=='__main__':main()
