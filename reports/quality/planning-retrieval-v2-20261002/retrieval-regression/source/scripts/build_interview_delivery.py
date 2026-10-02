"""Render current interview facts from pinned evidence, without rewriting history."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = Path('contracts/interview_delivery.json')
DESTINATION = Path('docs/CURRENT_DELIVERY.md')


def pinned(root: Path, descriptor: dict) -> dict:
    name = descriptor['path']
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or Path(name).is_absolute():
        raise ValueError('Evidence path must stay inside the repository')
    raw = path.read_bytes()
    normalization = descriptor.get('normalization')
    if normalization == 'LF_TEXT':
        raw = raw.replace(b'\r\n', b'\n')
    elif normalization is not None:
        raise ValueError('Unknown evidence normalization policy')
    if hashlib.sha256(raw).hexdigest() != descriptor['sha256']:
        raise ValueError('Evidence SHA mismatch: ' + name)
    return json.loads(raw)


def generate(root: Path = ROOT) -> str:
    contract = json.loads((root / CONTRACT).read_text(encoding='utf-8'))
    if contract['schema'] != 'mini-drop.interview-delivery.v1':
        raise ValueError('Unsupported delivery contract')
    sources = contract['sources']
    engineering = pinned(root, sources['engineering'])
    cases = pinned(root, sources['engineering_cases'])
    release = pinned(root, sources['production_release'])
    ci = pinned(root, sources['production_ci'])
    runtime = pinned(root, sources['production_verification'])
    browser = pinned(root, sources['production_browser'])
    outcomes = Counter(row['outcome'] for row in engineering['cases'])
    if len(engineering['cases']) != engineering['evaluated_scenarios']:
        raise ValueError('Incomplete engineering denominator')
    if sum(outcomes.values()) != engineering['registered_scenarios']:
        raise ValueError('Registered and evaluated scenarios diverge')
    expected = {'LOCALIZED_ANOMALY', 'REFUTED', 'SUPPORTED_OBSERVATION'}
    if set(outcomes) != expected or engineering['diagnosis_accepted'] != sum(outcomes.values()):
        raise ValueError('Do not present incomplete judgments as accepted')
    if engineering['localization_accepted'] != outcomes['LOCALIZED_ANOMALY'] or engineering['refuted'] != outcomes['REFUTED']:
        raise ValueError('Outcome counters disagree')
    if any(row['causal_root_cause_verified'] or row['same_load_fix_verified'] for row in engineering['cases']):
        raise ValueError('Current interview summary must preserve observation-only scope')
    head = release['git_head']
    if ci['status'] != 'VERIFIED' or ci['source_head'] != head or runtime['source_head'] != head:
        raise ValueError('CI, publication and deployed source must match')
    if ci['successful_jobs'] != ci['job_count'] or not ci['source_equivalence']['same_git_tree']:
        raise ValueError('Incomplete exact-source CI evidence')
    if not browser['passed'] or not browser['legacy_score_ui_absent'] or not runtime['public_fault_catalog_score_fields_omitted']:
        raise ValueError('Retired score UI/API verification is incomplete')
    if release['release_tag'] not in runtime['release']:
        raise ValueError('Publication release identity mismatch')
    counts = ci['main_suite_counts']
    registered = engineering['registered_scenarios']
    lines = [
        '# 当前面试交付事实', '',
        '本页由 `contracts/interview_delivery.json` 和 `scripts/build_interview_delivery.py` 生成。',
        '成绩分别说明工程判断、具体定位和反证；既有原始实验报告保留原结论。', '',
        '| 项目 | 已验证的事实 |', '|---|---|',
        f"| 工程诊断判断 | {engineering['diagnosis_accepted']}/{registered} |",
        f"| 具体异常路径 | {engineering['localization_accepted']}/{registered} |",
        f"| 有效反证 | {engineering['refuted']} 条 |",
        f"| 有证据支持的观测 | {outcomes['SUPPORTED_OBSERVATION']} 类，尚未认定具体异常路径 |",
        f"| 实验来源 | 最新 {engineering['fresh_live_scenarios']} 个窗口及此前 {engineering['regraded_prior_scenarios']} 条真实记录 |",
        f"| 工程缺陷修复回归 | {len(cases['cases'])} 个独立案例 |",
        f"| 发布版 CI | [{ci['successful_jobs']}/{ci['job_count']} 作业]({ci['run_url']}) |",
        f"| 发布版 Python | {counts['python']['passed']} 通过、{counts['python']['skipped']} 登记跳过；真实 PG 与 Chroma 由独立作业执行 |",
        f"| 发布版 Web / Chromium | {counts['web_vitest']['passed']} 项 / {counts['chromium']['passed']} 项；Chromium 为固定数据回归 |",
        f"| 真实 PostgreSQL | {counts['postgres_python']['passed']} 项、{counts['postgres_python']['skipped']} 跳过 |",
        f"| 线上浏览器 | {len(browser['verified_downloads'])} 份下载 SHA 一致、{len(browser['layouts'])} 次布局检查通过 |",
        f"| 线上版本 | `{release['release_tag']}`，源码 `{head}` |", '',
        '性能实验中的工程判断包含支持观测和有效反证，不表示每类都定位了根因。',
        '真实业务同负载修复案例是独立 HTTP/SQLite 样例；模型规划、知识检索与真实采集分别验收。', '',
        '## 本轮交付收尾', '',
    ]
    for item in contract['additional_acceptances']:
        status = item['status']
        if status not in {'IN_PROGRESS', 'COMPLETED', 'PASSED', 'FAILED'}:
            raise ValueError('Unknown acceptance status')
        if status in {'COMPLETED', 'PASSED'}:
            report = pinned(root, item['report'])
            field, value = item['required_result']['field'], item['required_result']['value']
            if report.get(field) != value:
                raise ValueError('Report does not support completion: ' + item['title'])
        lines.append(f"- **{item['title']}：{status}**。{item['scope']}")
        if 'report' in item:
            lines.append(f"  [原始记录](../{item['report']['path']})。")
    lines.extend(['', '## 面试材料与能力边界', '',
        '测试开发重点是预先声明判据、旧缺陷负向复现、真实数据库竞争、原始数据复算及失败保留。',
        '后端开发重点是异步状态机、幂等、事务与行锁、取消竞争、授权和可恢复事件。',
        'Agent 开发重点是结构化规划、工具白名单、预算、检索与证据门禁，说明实际评估范围。',
        '正式讲稿见 [INTERVIEW_DEMO_GUIDE](INTERVIEW_DEMO_GUIDE.md)，剩余扩展见 [剩余工作](REMAINING_WORK_20260930.md)。', '',
        '每份源码 CI 与部署回执对应自己的提交，不能将后续工具或文档提交冒充当前线上应用来源。', '',
    ])
    return '\n'.join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    rendered = generate()
    target = ROOT / DESTINATION
    if args.check:
        if not target.is_file() or target.read_text(encoding='utf-8') != rendered:
            raise SystemExit('Current delivery facts drifted; regenerate from the pinned contract')
        print('Current interview delivery facts match pinned evidence')
    else:
        target.write_text(rendered, encoding='utf-8', newline='\n')
        print(str(DESTINATION))


if __name__ == '__main__':
    main()
