"""Derive final focused document candidates from actual archived receipts."""
from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).parent
HEAD = '73b4b18ad83553a5012a0025dfb78bb21ff8dc7f'
REPORT = 'reports/quality/planning-retrieval-v2-20261002'
OUT = STAGE / 'final-document-candidates'

def git(*args):
    return subprocess.check_output(['git', '-c', 'core.autocrlf=false', *args], cwd=ROOT)

def read(name):
    return json.loads((ROOT / name).read_bytes())

def pin(name):
    return {'path': name, 'sha256': hashlib.sha256((ROOT / name).read_bytes()).hexdigest()}

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

def main():
    assert git('rev-parse', 'HEAD').decode().strip() == HEAD
    assert not git('diff', '--cached', '--name-only').strip()
    assert not OUT.exists(), 'preserve previous candidates'
    release_path = REPORT + '/deployment/prompt-final/final-release-r7/manifest.json'
    ci_path = REPORT + '/ci/prompt-boundaries/main-final-36996499159/summary.json'
    runtime_path = REPORT + '/deployment/prompt-final/r7-publication-verification.json'
    browser_path = REPORT + '/live/post-prompt/browser/result.json'
    core_path = REPORT + '/ci/prompt-boundaries/clean-final-36996499257/reports/clean-stack/report.json'
    core_summary_path = REPORT + '/ci/prompt-boundaries/clean-final-36996499257/summary.json'
    live_path = REPORT + '/live/post-prompt/normal-smoke/summary.json'
    release, ci, runtime, browser = map(read, [release_path, ci_path, runtime_path, browser_path])
    live, core, core_summary = map(read, [live_path, core_path, core_summary_path])
    assert release['git_head'] == ci['source_head'] == runtime['source_head'] == browser['source_head'] == live['source_head'] == HEAD
    assert ci['status'] == core_summary['status'] == 'VERIFIED' and ci['successful_jobs'] == 14
    assert core['status'] == 'PASSED' and core_summary['gate_counts'] == {'executed': 87, 'passed': 87, 'failed': 0, 'skipped': 0}
    assert runtime['healthy_containers'] == 13 and runtime['untouched_containers'] == 11
    assert browser['passed'] and browser['legacy_score_ui_absent'] and not browser['verified_downloads']
    assert len(live['cases']) == 1 and live['selected_cases'] == ['normal'] and live['planner_invocations'] <= 1
    assert not live['health_check_performed'] and not live['causal_root_cause_verified']
    assert sum(row['persisted_tasks'] for row in live['persisted_task_counts']) == 0
    tag = release['release_tag']
    counts = ci['main_suite_counts']
    count_text = (f"Python {counts['python']['passed']} 通过/{counts['python']['skipped']} 登记跳过，"
                  f"Web {counts['web_vitest']['passed']}、真实 PostgreSQL {counts['postgres_python']['passed']} 零跳过、"
                  f"Chromium 固定数据 {counts['chromium']['passed']}；Chroma 独立作业通过")
    normal = '通过' if live['status'] == 'PASSED' else '未通过'
    normal_text = (f"v7 提示部署后的同一 NORMAL 问题新会话补验 1 次，结果{normal}；原始状态 `{live['status']}`，"
                   f"预期 NORMAL，实际 `{live['cases'][0].get('actual_disposition', 'UNKNOWN')}`，实际持久采集任务 0。"
                   "本次返回合法 INSUFFICIENT_EVIDENCE 并直接 finish：只有进程身份、没有性能基线或时间窗口，且用户要求不采集。"
                   "没有供应商超时、检索循环、非法计划或语义重试；合法停止合同已实证，但该问题的 NORMAL 标签未通过。"
                   f"当前浏览器实际呈现 {browser['rendered_live_cases']}/3 张规划结果卡、"
                   f"{len(browser['no_answer_checks'])} 个无答案通知，{len(browser['layouts'])} 次四视口布局检查通过，"
                   "TLS 证书校验开启，无 console/network 错误，本次没有证据下载。缺测与拒绝卡的产生源码是 df0d3ef0；"
                   "新 NORMAL 会话的产生源码是 73b4b18a，逐例来源分开保存，不宣称三个状态都在最终版本重新实跑。")
    current = (f"当前已部署 `{tag}`，应用源码 `{HEAD}`。"
               "本次仅更换 Diagnosis Worker 和 Analyzer；Web 保留 a6a36260/20261002T095001Z 的运行镜像，"
               "完整 Web Git tree 与最终源码相等，58 个公网资源 SHA 一致；Worker/Analyzer 各 219 个源码文件逐一核对，"
               "13 服务健康，其余 11 个容器及紧邻部署前 Office/API/Native/CPP、环境和挂载保持。"
               "故障广场 21 场景均 inactive，展示工程判断 21/21、路径 6/21、反证 8 条；没有重跑故障或一小时实验。")
    quality = (f"精确源码 [主 CI 36996499159](https://github.com/llongwang751-arch/mini-drop/actions/runs/36996499159) "
               f"实际 14/14，{count_text}。[干净核心 CI 36996499257](https://github.com/llongwang751-arch/mini-drop/actions/runs/36996499257) "
               "87 项门禁零跳过、10 阶段通过。原失败和原断言保持，专项数量不重复加到主套件。")
    regression = ("新冻结 24 题首轮仍为 df0d3ef0 上的 21 响应/3 超时，结构、判断和下一工具均 21/24，零重试；"
                  "首轮 BM25 Recall@3 0.96875、无答案误召回 5/8；HYBRID Recall@3 0.90625、误召回 1/8。"
                  "a6a36260 的同题已曝光检索回归中，两路无答案误召回均 0/8，两路 Recall@3 均 0.875，"
                  "BM25 MRR@3 0.90625、HYBRID MRR@3 0.875；主体准入减少误召回也损失相关内容覆盖。"
                  "这不是新的盲测。最终 73b4b18a 的知识语料与三个检索实现文件与 a6a36260 Git tree 相同，"
                  "由发布 manifest 证明来源等价，没有重复消耗 chat 或检索实评预算。")
    scope = ("规划输出已统一为 INVESTIGATE / NORMAL / INSUFFICIENT_EVIDENCE / REFUSED。合法非调查结果"
             "空假设、空工具，持久化 planner.output_recorded，不新建采集任务；NORMAL 只是输入或规划范围内未提出异常，"
             "真实体检仍必须使用采集后的健康判据。NO_RELEVANT_KNOWLEDGE 表示未找到相关知识，不表示业务正常。"
             "提示先选四态，只有仍有可验证异常与可执行动作的 INVESTIGATE 才必须扩展假设和数值证伪；"
             "目标、权限、预算、数值、采集失败与证据门禁不降低。检查点使用 diagnosis-agent-v7-four-state-prompts:<diagnosis_id>，"
             "scope-agent-v1:<diagnosis_id>；旧 v6 和原 ID 检查点保留，SQL 业务 ID 不改变。")
    next_text = ("后续最有价值的是补公共知识能力与同义表达的覆盖，以新的未曝光问题重新冻结评估；"
                 "供应商超时单列为可用性问题，不能把超时强制变成 NORMAL 或刷重试成功率。"
                 "求职准备继续阅读真实源码、讲清测试判据和失败取舍，并完成本人五分钟彩排。")
    candidates = {}

    def change(name, transform):
        clean = git('show', HEAD + ':' + name).decode('utf-8')
        dirty = (ROOT / name).read_text(encoding='utf-8')
        clean_new, dirty_new = transform(clean), transform(dirty)
        candidates[name] = clean_new.encode('utf-8')
        (ROOT / name).write_text(dirty_new, encoding='utf-8', newline='\n')

    def context(text):
        text = text.replace('## 2026-10-02 规划输出与无答案检索 v2（待部署验收）', '## 2026-10-02 规划输出与无答案检索 v2（已部署）', 1)
        anchor = '本轮优化复用现有 LangGraph/legacy 规划入口。'
        assert text.count(anchor) == 1
        text = text.replace(anchor, current + '\n\n' + quality + '\n\n' + regression + '\n\n' + normal_text + '\n\n' + anchor, 1)
        text = text.replace('原始失败完整归档，正在按公共主体/能力范围修复检索，并补真实检查点版本隔离；后续同题检索只称已曝光问题回归，不称新的盲测。', '原始失败完整归档；后续主体准入修复、真实检查点隔离及同题检索回归已完成，结果以本节最新事实为准。', 1)
        text = text.replace('后续正常分支在新的独立批次补验。', '正常分支的新批次补验结果见本节顶部与原始归档。', 1)
        text = text.replace('## 2026-10-02 面试交付收尾（已验证）', '## 历史快照：2026-10-02 首次面试交付收尾（已验证）', 1)
        return text

    def handoff(text):
        heading = '## 2026-10-02 规划输出／无答案检索优化进行中'
        assert text.count(heading) == 1
        text = text.replace(heading, '## 2026-10-02 规划输出／无答案检索优化已发布', 1)
        start = text.index('用户已授权优化并更新线上；')
        stop = text.index('\n\n', start)
        body = '\n\n'.join([current, quality, regression, normal_text, scope, next_text,
            '完整记录见 [本轮交付](../reports/architecture/planning-retrieval-v2-20261002.md)、[评估合同](PLANNING_RETRIEVAL_V2.md) 和 [CURRENT_DELIVERY](CURRENT_DELIVERY.md)。后续文档/证据提交不能冒称部署应用来源。'])
        text = text[:start] + body + text[stop:]
        return text.replace('## 2026-10-02 面试交付收尾（已验证）', '## 历史快照：2026-10-02 首次面试交付收尾（已验证）', 1)

    def planning(text):
        begin = text.index('```powershell')
        end = text.index('```', begin + 3) + 3
        text = text[:begin] + '''```powershell
# 只核验冻结，不调用模型
python -B scripts/evaluate_planning_retrieval_v2.py --check-freeze

# 已完成首轮必须从归档的原始源码复核；不重新请求供应商
python -B reports/quality/planning-retrieval-v2-20261002/evaluation/original-source/scripts/evaluate_planning_retrieval_v2.py --verify-report reports/quality/planning-retrieval-v2-20261002/evaluation/first-run/report.json
```''' + text[end:]
        text = text.replace('当前出题与代码门禁完成；真实执行状态必须以新目录原始报告为准，不能从单元测试虚构供应商成绩。正式发布报告与项目上下文登记实际结果。', '首轮实评已经完成，原始目录不可覆盖。新的模型实评必须建立新的题目、冻结合同和调用批次；下面的结果不由单元测试推断。', 1)
        anchor = '## 旧报告的历史复算'
        assert text.count(anchor) == 1
        section = '\n\n'.join(['## 已曝光问题回归与最终部署', regression, current, quality,
            'Java 正常 CPU 缺少明确进程语境、同主机块设备竞争同义表达，以及缺少主能力 I/O 锚点的低延迟同步写反证，仍存在覆盖缺口。没有按私有真值添加 case ID 或品牌特例；后续新增公共能力后要用另一批未曝光问题验证。',
            '实际 NORMAL 首批超时、第二批非法 INVESTIGATE 后纠正超时均保留。旧提示存在无条件取证的冲突已修复；这不能证明任一次供应商超时的具体原因。', normal_text,
            '补验使用 ASSISTED R0、最多一轮，规划预算 60 秒，单次模型调用上限 min(45 秒, 剩余预算)、SDK 零重试；会话总预算 120 秒不代表规划预算。一个 planner invocation 可能有多个模型节点，底层 HTTP 调用数未取得时保持 null。',
            '[检索独立复算](../' + REPORT + '/retrieval-regression/independent-audit.json)，[真实规划与浏览器](../' + REPORT + '/live/post-prompt/manifest.json)。']) + '\n\n'
        return text.replace(anchor, section + anchor, 1)

    def material(text):
        heading = '## 2026-10-02 求职材料的当前口径'
        assert text.count(heading) == 1
        addition = '\n\n'.join(['## 2026-10-02 四态规划与无答案检索已发布', current, quality, scope, regression, normal_text, next_text,
            '本次线上浏览器与旧 41 份下载分别计数；本次下载数为 0。详细事实由 [CURRENT_DELIVERY](CURRENT_DELIVERY.md) 的固定 SHA 合同生成，见 [本轮交付](../reports/architecture/planning-retrieval-v2-20261002.md)。'])
        return text.replace(heading, addition + '\n\n## 历史快照：2026-10-02 首次求职材料口径', 1)

    change('docs/PROJECT_CONTEXT.md', context)
    change('docs/RESTART_HANDOFF.md', handoff)
    change('docs/PLANNING_RETRIEVAL_V2.md', planning)
    for name in ['docs/REMAINING_WORK_20260930.md', 'docs/INTERVIEW_DEMO_GUIDE.md', 'docs/TEST_ENGINEERING.md']:
        change(name, material)
    def index(text):
        anchor = '# 文档入口\n\n'
        assert text.count(anchor) == 1
        return text.replace(anchor, anchor + '- [四态规划与无答案检索已部署](../reports/architecture/planning-retrieval-v2-20261002.md)：最终源码、实际停止分支、无答案回归与保留的覆盖/分类缺口。\n\n', 1)
    change('docs/README.md', index)

    contract_name = 'contracts/interview_delivery.json'
    contract = json.loads(git('show', HEAD + ':' + contract_name))
    assert (ROOT / contract_name).read_text(encoding='utf-8').strip() == git('show', HEAD + ':' + contract_name).decode().strip()
    for key, name in [('production_release', release_path), ('production_ci', ci_path), ('production_verification', runtime_path), ('production_browser', browser_path)]:
        contract['sources'][key] = pin(name)
    for old in contract['additional_acceptances']:
        old['title'] = '历史 RC1：' + old['title']
        if old['title'].endswith('本轮源码主 CI'):
            old['scope'] = old['scope'].replace('与上方现有线上 de094fff 发布 CI 分开。', '此记录属于历史 RC1，不是当前线上应用源码的 CI。')

    def acceptance(title, status, scope_text, path, field='status', expected='VERIFIED'):
        return {'title': title, 'status': status, 'scope': scope_text, 'report': pin(path), 'required_result': {'field': field, 'value': expected}}

    contract['additional_acceptances'] += [
        acceptance('当前源码干净 Linux 核心复刻', 'PASSED', '最终源码73b4b18a；87门禁/0跳过及10阶段通过，真实 Agent→Task→S3→Analyzer 与身份、mTLS和清理验证；范围限核心平台/sys_metrics，不含外部模型或Office。', core_path, expected='PASSED'),
        acceptance('v2新冻结24题首轮模型与检索', 'COMPLETED', '首轮源码df0d3ef0；21响应/3超时，结构/判断/下一工具均21/24，零重试；BM25 Recall0.96875/无答案5/8，HYBRID Recall0.90625/无答案1/8。仅规划DTO与检索，不是完整Agent或因果准确率。', REPORT + '/evaluation/receipts/model-independent-review-r3.json'),
        acceptance('已曝光v2问题无答案检索回归', 'COMPLETED', regression, REPORT + '/retrieval-regression/independent-audit.json'),
        acceptance('最终提示后的真实NORMAL补验', 'PASSED' if live['status'] == 'PASSED' else 'FAILED', normal_text + ' 单列新会话，原两次NORMAL失败保持，无新采集/Evidence，不执行健康检查。', live_path, expected='PASSED'),
    ]
    raw = (json.dumps(contract, ensure_ascii=False, indent=2) + '\n').encode()
    candidates[contract_name] = raw
    (ROOT / contract_name).write_bytes(raw)
    generator = module('final_delivery_generator', 'scripts/build_interview_delivery.py')
    generated = generator.generate().encode()
    candidates['docs/CURRENT_DELIVERY.md'] = generated
    (ROOT / 'docs/CURRENT_DELIVERY.md').write_bytes(generated)

    architecture = '# 四态规划与无答案检索优化交付（2026-10-02）\n\n' + '\n\n'.join([
        current, quality, '## 生产行为', scope,
        '四态共享生产 validator，LangGraph finish_diagnosis_plan 与 legacy 都保持兼容。planner.output_recorded 明确 is_evidence=false、health_check_performed=false、causal_root_cause_verified=false。已有证据、任务、取消状态与终态不因非调查输出被清空。',
        '检索按实际问题主体与当前候选公开主能力准入；分类器猜测和其他catalog条目不能授权候选。title/keywords/applies_to 声明能力，summary/body 用于排序。ACL、来源与chunk SHA仍执行，降级路线复用准入；NO_RELEVANT_KNOWLEDGE 和 RETRIEVAL_ONLY 可用性分别显示。',
        '## 效果与限制', regression, normal_text,
        '首轮模型使用真实生产提示/schema/parser的JSON适配，不执行平台LangGraph/Task/Evidence。真实Agent补验单列；第一次NORMAL超时，第二次错误计划被门禁拒绝后纠正超时，最终一次结果由原始摘要决定。旧提示冲突的修复不等于证明超时根因。',
        '## 失败与来源保留',
        '首次Python3.11历史Recall末位差异、旧页面5秒超时、一次原文案连续短语断言失败均保留。历史浮点仅在隔离旧源码固定原求和口径，所有逐题/报告严格比较；AST跨解释器摘要保留全部非空语法字段。Vitest并发降至2而原超时和断言不改。最终132项提示回归与Ruff通过。',
        '应用源码73b4b18a已推送，后续提交仅固定文档/合同/原始证据。正式旧RC1标签和release不重写；旧评分文件、冻结题目/真值/规则、数据库卷、对象数据和用户教学注释保持。',
        '## 证据入口',
        '- [固定SHA当前事实](../../docs/CURRENT_DELIVERY.md)\n- [主CI](../quality/planning-retrieval-v2-20261002/ci/prompt-boundaries/main-final-36996499159/summary.json)\n- [干净核心](../quality/planning-retrieval-v2-20261002/ci/prompt-boundaries/clean-final-36996499257/summary.json)\n- [最终发布](../quality/planning-retrieval-v2-20261002/deployment/prompt-final/archive-manifest.json)\n- [首轮评估](../quality/planning-retrieval-v2-20261002/evaluation/archive-manifest.json)\n- [已曝光检索回归](../quality/planning-retrieval-v2-20261002/retrieval-regression/archive-manifest.json)\n- [真实规划与浏览器](../quality/planning-retrieval-v2-20261002/live/post-prompt/manifest.json)\n- [本轮总清单](../quality/planning-retrieval-v2-20261002/manifest.json)',
        '## 后续优先级', next_text]) + '\n'
    name = 'reports/architecture/planning-retrieval-v2-20261002.md'
    assert not (ROOT / name).exists()
    (ROOT / name).write_text(architecture, encoding='utf-8', newline='\n')
    candidates[name] = architecture.encode()

    rows = []
    for name, raw in candidates.items():
        target = OUT / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        rows.append({'path': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'clean_candidate': str(target)})
    (STAGE / 'final-document-source-map.json').write_text(json.dumps({'base_head': HEAD, 'release_tag': tag, 'files': rows}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': 'PREPARED_FOCUSED_DOCUMENTS', 'files': len(rows), 'normal_status': live['status'], 'source_head': HEAD}))

if __name__ == '__main__':
    main()
