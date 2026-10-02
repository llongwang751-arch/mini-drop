from pathlib import Path
import json
import sys
sys.path.insert(0,str(Path.cwd()))
from scripts.run_distributed_endurance import verify_distributed
stage=Path(__file__).resolve().parent
r=json.loads((stage/'after-hour-isolated/report.json').read_text(encoding='utf-8'))
assert r['status'] in {'PASSED','FAILED','INVALID'}
verified=verify_distributed(stage/'after-hour-isolated/report.json')
interrupted=json.loads((stage/'after-multi-hour/interruption.json').read_text(encoding='utf-8'))
assert interrupted['status']=='INTERRUPTED' and interrupted['owned_remote_pid_exited']
soak=next(s for s in r['stages'] if s['name']=='soak');step=next(s for s in r['stages'] if s.get('rate')==60)
failed=sum(b['summary']['status']!='PASSED' for b in soak['buckets'])
assert len(soak['buckets'])==120 and r['remote_cleanup_confirmed']
summary=(f"单隧道独立新双机小时实验{r['status']}：3600秒持续，{sum(s['summary']['offered'] for s in r['stages']):,}次计划请求，"
 f"60RPS P95 {step['summary']['p95_ms']:.3f}ms，持续总体P95 {soak['summary']['p95_ms']:.3f}ms，"
 f"120窗中{failed}窗不达标；资源{r['resources']['status']}、独立原始重算VERIFIED、远端清理确认。")
sent=sum(s['summary']['sent'] for s in r['stages'])
unsent=sum(s['summary']['unsent'] for s in r['stages'])
summary+=f"实发{sent:,}、未发{unsent}；持续成功率{soak['summary']['success_rate']:.4%}，质量率{soak['summary']['quality_rate']:.4%}。"
qa=[json.loads((stage/'cache-empty'/f'office-three-phase-cache-{i}.json').read_text(encoding='utf-8')) for i in (1,2)]
assert all(q['passed'] for q in qa)
browser=json.loads((stage/'cache-empty/browser-exercise-session/result.json').read_text(encoding='utf-8'));assert browser['passed']
case=json.loads((stage/'cache-empty/browser-diagnosis-evidence.json').read_text(encoding='utf-8'));assert case['case']['status']=='INSUFFICIENT_EVIDENCE'
web=json.loads((stage/'web-security/platform-deployment.json').read_text(encoding='utf-8'))
runtime=json.loads((stage/'final-runtime-verification.json').read_text(encoding='utf-8'))
ci=json.loads((stage/'web-security/ci-run.json').read_text(encoding='utf-8'))
assert web['status']=='HEALTHY' and web['source_head'].startswith('7403815')
assert ci['conclusion']=='success' and ci['head_sha']==web['source_head']
assert runtime['web_files_verified']==45 and runtime['healthy_containers']==13 and runtime['active_faults']==0
assert json.loads((stage/'web-security/browser-report/result.json').read_text(encoding='utf-8'))['passed']
common=f"""
## 2026-10-01 性能修复已部署与最终复验

平台Worker/Analyzer为`20260930T144209Z` / `dc50c47`，各184文件一致；Web为`20260930T164946Z` / `7403815`，45文件一致；最后只替换Web，另外12容器未重建。API仍为`f9b143a`及已核对二进制。Office为`observer-20260930T145902Z` / `f37f44e`，只更新3个集成模块，原业务源码和数据保持。Office源码f37f44e的CI [36733279485](https://github.com/llongwang751-arch/mini-drop/actions/runs/36733279485)成功13/13；当时Python1241通过/10登记跳过、真实PG8通过零跳过、Web228通过/44文件。

最新Web/兼容默认值源码7403815的CI [36746799170](https://github.com/llongwang751-arch/mini-drop/actions/runs/36746799170)成功13/13，Python1249通过/10登记跳过、真实PG8通过零跳过、Web228通过/44文件。前一b05ec31的CI36745550176为12/13，测试/构建通过但生产审计拦截Axios高危公告；升级1.20.0及生成lock后生产audit为0，门禁不关闭。新Web已发布并经真实报告/树浏览器复验，无JS/HTTP错误；13容器及API3依赖健康，21故障inactive。依赖扫描结论有时间范围，不代表全部语言/镜像或未来无漏洞。

测量脚本源码52881ec的CI [36742160900](https://github.com/llongwang751-arch/mini-drop/actions/runs/36742160900)成功13/13，Python1249通过/10登记跳过；可控真实socket阻塞测试确认慢请求等待时另一路仍完成5请求，原慢请求保留且不重发。4隧道短测2250次成功/质量通过，60RPS P95 112.464ms。

4路实验在1102.2秒停止，记录5512次持续请求、9次超时，36个完整30秒窗中6个超限；本地进程消失且远端已退出，具体退出原因不明，原RUNNING报告和INTERRUPTED侧证均保留，不能算作小时完成。4路没有解决该时段全部传输的共同等待，因此源码b05ec31恢复默认1条，仅显式`--ssh-tunnels 4`启用多路实验；59项有关回归通过。冻结52881ec的实验源码与原始结果没有修改，200ms/120窗判据不变，不重复寻找最好成绩。

{summary}此前单隧道完整小时19950请求全部成功/质量通过，但末窗3570秒P95 810.518ms，1/120窗失败，原始FAILED保留。不同传输池是不同实验条件，新成绩不重写旧单路成绩；不能断言旧失败是丢包、SSH重协商或已找到云端传输因果根因。首次新小时在3578秒中断，保留INTERRUPTED和原RUNNING记录，不拼成小时或覆盖历史失败。

Office预先固定两组三段均通过原+2000ms门槛：检索46.070→2531.257→30.292ms、37.024→2551.019→30.395ms；答案、向量调用、注入真实等待和同PID/版本有效。公共浏览器67.139→2553.504→66.651ms通过、无JS/HTTP错误，准备请求总19.007秒单独公开，不计入三段。原第一版FAIL/PASS及缺平台Cookie的浏览器401保留。合法空JSON与失败空结果区分缓存，2项旧源码负向失败、41项相关回归及全量通过；模型首次请求仍可慢，缓存不是冷启动SLO或生产容量证明。

浏览器诊断`{case['case']['diagnosis_id']}`已证据不足收束：3工具完成、1门禁拒绝、9份下载SHA验证、报告/树实浏览器通过。采集是之后的窗口，没有还原故障请求调用栈；报告不把宿主I/O或Python采样当根因。完整历史21根因仍0/21、观测4/21、撤销恢复/清理21/21，不重写原成绩。详情和可分享报告见[本轮性能闭环](../reports/architecture/performance-fix-20260930.md)。
"""
for name in ['PROJECT_CONTEXT.md','RESTART_HANDOFF.md','DISTRIBUTED_LOAD.md','FULL_CHAIN_ACCEPTANCE.md','INTERVIEW_DEMO_GUIDE.md','TEST_ENGINEERING.md','REMAINING_WORK_20260930.md','ENGINEERING_DELIVERY.md']:
 p=Path('docs')/name;s=p.read_text(encoding='utf-8');assert '性能修复已部署与最终复验' not in s
 first,tail=s.split('\n',1)
 chosen=common if name=='PROJECT_CONTEXT.md' else ('\n## 2026-09-30 最终交付状态\n\n平台20260930T144209Z/dc50c47，Office observer-20260930T145902Z/f37f44e，API继续f9b143a。Office源码CI36733279485及测量源码52881ec的CI36742160900均成功13/13；后者Python1249/10登记跳过、PG8/0、Web228/44。'+summary+'\n\n单隧道完整小时1/120窗失败、首次3578秒中断均原样保留；4路短测通过但新长测1102.2秒中断并有9次超时及6/36窗超限，不能算小时完成。默认恢复1路、4路显式选择；未改变200ms门槛或原失败成绩。\n\nOffice两组原+2000ms三段均通过，公开浏览器通过；一次明确准备请求不计入三段，首次模型调用仍可19秒。浏览器诊断3工具完成、1门禁拒绝，9份下载SHA通过，结果证据不足。历史21因果0/21保持；当前步骤、完整证据与边界见[性能闭环](../reports/architecture/performance-fix-20260930.md)及[项目上下文](PROJECT_CONTEXT.md)。\n')
 chosen=chosen.replace('2026-09-30 最终交付状态','2026-10-01 最终交付状态').replace('平台20260930T144209Z/dc50c47，Office','Worker/Analyzer 20260930T144209Z/dc50c47，Web 20260930T164946Z/7403815（Axios1.20.0），Office')
 if name!='PROJECT_CONTEXT.md':
  chosen+='\n最新Web/兼容源码CI36746799170成功13/13，Python1249/10登记跳过、PG8/0、Web228/44；前一b05ec31的CI因生产Axios审计失败保留，升级未绕过门禁。最后仅更新Web，其余12容器不变；逐文件核对、13容器健康及真实报告/树页面通过。\n'
 s=first+'\n'+chosen+'\n以下带时间与版本的内容是此前过程记录，当前状态以上述最终复验为准。\n'+tail
 s=s.replace('图谱及chunk数据库每次重新读取','不缓存图谱或chunk候选；需要数据库候选时仍重新读取')
 p.write_text(s,encoding='utf-8')
p=Path('docs/SERVICE_INTEGRATION.md');s=p.read_text(encoding='utf-8').replace('图谱、chunk和用户过滤每次执行','不缓存图谱或chunk候选，仍执行原搜索与用户过滤；有实体时重新读取数据库，合法空实体保持原空结果').replace('搜索阶段扣除向量化与重排，避免在页面上重复加总','搜索阶段按向量化与重排窗口的并集及父窗口交集扣除，避免并行重复扣减；6项细分计时为包含关系，不能相加');p.write_text(s,encoding='utf-8')
p=Path('docs/REMAINING_WORK_20260930.md');s=p.read_text(encoding='utf-8');key='## 功能与验证边界';s=s.replace(key,'## 本轮新增确认的边界\n\n准备请求仍可能受模型波动影响，公共实测19秒；只复用查询实体规划，不缓存文档/数据库或最终答案，300秒后自然失效。缓存对同用户/配置的模型提取结果复用，不能承诺不同问题的冷延迟或盲测检索质量不变。此次Agent报告的source_context.scanned_files=0、mappings为空：办公助手源码尚未进入Analyzer的只读映射。3种采集已执行，但缺请求同窗的业务函数/因果对照，仍不能自动定位故障时的根因。无平台会话Cookie的Office外层返回HTML401，现有页面提示较生硬，正常登录流程已通过。\n\n'+key);p.write_text(s,encoding='utf-8')
p=Path('docs/README.md');s=p.read_text(encoding='utf-8');s=s.replace('\n','\n\n- [性能修复与最终验收](../reports/architecture/performance-fix-20260930.md)：双机完整小时、HTTP复用/排序缓存、并行窗口计时、查询实体缓存、真实两组三段与浏览器、原始失败保留及根因能力边界\n',1);p.write_text(s,encoding='utf-8')
p=Path('reports/architecture/performance-fix-20260930.md');s=p.read_text(encoding='utf-8');first,tail=s.split('\n',1);leading=common.replace('../reports/architecture/performance-fix-20260930.md','performance-fix-20260930.md');s='# 性能修复、独立小时复验与真实业务演示（2026-09-30）\n'+leading+'\n原始材料：[`reports/quality/performance-fix-20260930/`](../quality/performance-fix-20260930/)，逐字节清单与复核器见该目录。\n\n[完整小时交互报告](../quality/performance-fix-20260930/load-single-tunnel-hour/report.html)、[报告及原始请求/资源/冻结源码ZIP](../quality/performance-fix-20260930/load-single-tunnel-hour/evidence.zip)。这是双机一次性fixture经公网SSH的测量，固定每请求10ms依赖、质量判定与200ms/120窗门槛保留；此前单路发压端执行过有界单worker测试/构建和浏览器；本次4路停止前只进行文档、归档及轻量状态读取，未进行并行云端负载/故障注入；停止后进行了Axios的单worker回归，发压端并非独占主机，不是完全独占负载生成机。缓存/连接优化整体前后比较不能单独估计每项优化的因果收益；未测生产容量上界或实时LLM容量。\n\n## 过程记录（包括当时未完成状态）\n'+tail;s=s.replace('图谱数据库每次读','不缓存图谱/数据库候选，保持原搜索和读取逻辑');p.write_text(s,encoding='utf-8')
print(summary)
