import { Button } from "antd";
import { ArrowRightOutlined, BranchesOutlined, CloudServerOutlined, FileSearchOutlined, LineChartOutlined } from "@ant-design/icons";
import "./DiagnosisWelcome.css";

const EXAMPLES = [
  ["CPU 升高", "订单服务最近 5 分钟 CPU 持续升高，请定位热点并排除同机争抢。"],
  ["内存增长", "Java 服务内存持续增长，请检查对象分配与 GC，并说明还需要哪些证据。"],
  ["请求变慢", "接口响应时间突然升高，请检查网络等待和下游依赖。"],
];

export default function DiagnosisWelcome({ onSelectService, onExample }) {
  return (
    <section className="diagnosis-welcome" aria-label="开始诊断">
      <div className="diagnosis-welcome-copy">
        <span className="diagnosis-welcome-kicker">从一次服务体检开始</span>
        <h3>先看状态，<br />再循证排查。</h3>
        <p>CPU、内存、业务耗时和运行进程，<br className="welcome-desktop-break" />在同一条诊断路径里看清楚。</p>
        <Button type="primary" size="large" onClick={onSelectService} icon={<ArrowRightOutlined />} iconPosition="end">选择已接入服务</Button>
        <div className="diagnosis-welcome-examples">
          <span>有明确异常？</span>
          {EXAMPLES.map(([label, query]) => (
            <button key={label} type="button" onClick={() => onExample(query)}>{label}<ArrowRightOutlined /></button>
          ))}
        </div>
      </div>
      <div className="diagnosis-blueprint" aria-label="服务体检流程示意">
        <div className="blueprint-title"><BranchesOutlined /><span>每一步，都有据可查</span><small>流程示意</small></div>
        <div className="blueprint-root"><CloudServerOutlined /><div><strong>你的应用</strong><small>服务 · 进程 · 业务请求</small></div></div>
        <div className="blueprint-connector" aria-hidden="true" />
        <div className="blueprint-branches">
          <div><LineChartOutlined /><strong>运行时指标</strong><small>CPU / 内存 / I/O</small></div>
          <div><FileSearchOutlined /><strong>业务阶段</strong><small>分块 / 检索 / 生成</small></div>
        </div>
        <div className="blueprint-connector is-merge" aria-hidden="true" />
        <div className="blueprint-result"><BranchesOutlined /><div><strong>沿排查树，形成体检报告</strong><small>状态、证据与下一步，逐层展开</small></div></div>
      </div>
    </section>
  );
}
