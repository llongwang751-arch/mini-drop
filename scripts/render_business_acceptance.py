"""Render measured business results without changing their verdicts."""
import argparse
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.build_business_acceptance_view import build


def render(source,output):
    value=build(source)
    lines=['# 业务测量与修复比较','',
        '本轮运行本地 HTTP 样例；未调用真实模型，未认证 AI 根因，未接入用户原 RAG 仓库。','',
        f"运行版本：`{value['revision']}`；结束时间：`{value['finished_at']}`。",'',
        '| 案例 | 正常 P95 | 故障 P95 | 变更后 P95 | 结果 |','| --- | --- | --- | --- | --- |']
    labels={'IMPROVEMENT_VERIFIED':'业务指标改善已验证','DEGRADED_AVAILABLE':'仅降级可用',
            'REJECTED':'未通过','INCOMPARABLE':'不可比较'}
    for case in value['cases']:
        s=case['comparison']['summaries']
        lines.append('| '+case['title']+' | '+' | '.join(f"{s[k]['p95_ms']:.2f} ms" for k in ['baseline','fault','after'])+' | '+labels[case['comparison']['outcome']]+' |')
    lines+=['','每条案例三个窗口各 30 个请求，6 次/秒。所有成功率和引用质量均可在逐场原始记录核对；不报告 P99 或生产准确率。','',
        f'[机器汇总]({source.name})。协议见 [业务验收](../../docs/BUSINESS_ACCEPTANCE.md)。']
    for case in value['cases']:
        lines+=['',f"## {case['scenario_id']} {case['title']}",'',case['comparison']['change_summary'],'',
            f"[逐场证据]({source.stem}-cases/{case['scenario_id']}.json)。"]
    output.write_text('\n'.join(lines)+'\n',encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();render(args.source,args.output)
