import { Link } from "react-router-dom";
import {
  ArrowUpRight,
  Cpu,
  Fingerprint,
  Stack,
  Waveform,
} from "@phosphor-icons/react";
import { PageHeading } from "../components/ui";
import { useCanWrite } from "../auth-context";
export default function Guide() {
  const canWrite = useCanWrite();
  return (
    <>
      <PageHeading
        eyebrow="RESOURCES / FIELD GUIDE"
        title="从采集到洞察"
        description="理解 Drop 的诊断工作流，以及每一份结果的边界。"
      />
      <div className="guide-intro">
        <span className="eyebrow">THE DIAGNOSTIC LOOP</span>
        <h2>
          先看见，
          <br />
          <span>再优化。</span>
        </h2>
        <p>
          Drop
          将远程调度、受控采集、制品存储与异步分析连接成一条可以追溯的数据链。每个状态都对应真实的执行记录。
        </p>
      </div>
      <div className="guide-grid">
        {[
          {
            icon: <Waveform size={26} />,
            title: "01 / 选择采样方式",
            text: "CPU、Java/Go/Python、堆内存、I/O、原生分配器与资源快照各有专用采样和分析路径。节点只公布自检通过的能力，也可导入对应原始文件。",
          },
          {
            icon: <Cpu size={26} />,
            title: "02 / 确认受控目标",
            text: "选择已授权的业务进程或容器。Agent 校验目标 UID、进程启动时间与 namespace；权限组决定成员可以访问和操作哪些节点。",
          },
          {
            icon: <Stack size={26} />,
            title: "03 / 阅读结果",
            text: "火焰图的宽度表示采样权重，纵向表示调用层级。自身开销不包含子调用；总计开销包含子调用。堆报告提供类实例、支配对象与引用路径，I/O 和内存报告展示实际观测统计。",
          },
          {
            icon: <Fingerprint size={26} />,
            title: "04 / 追溯证据",
            text: "时间线记录各阶段事件，原始与结果制品保留 SHA256。规则建议附带证据和版本，不能替代针对业务环境的性能验证。",
          },
        ].map((x) => (
          <article className="panel guide-card" key={x.title}>
            {x.icon}
            <h3>{x.title}</h3>
            <p>{x.text}</p>
          </article>
        ))}
      </div>
      <section className="panel form-section guide-details">
        <h2>Agent 接入与当前部署</h2>
        <p>
          Agent 通过 mTLS 主动连接内部 gRPC 控制面，默认心跳 2
          秒。数据库、对象存储和 gRPC 端口仅在 Docker 网络内开放。
        </p>
        <p>
          新增节点时，需要为稳定 Agent ID 签发专用证书，部署 C++ Agent，并配置
          Server
          地址、工作目录、采集权限与资源预算。证书私钥及长期对象存储凭据不应出现在浏览器或普通日志中。
        </p>
        <div className="info-banner">
          复合诊断按依赖执行多个步骤；Cron
          计划按所选时区触发。每种工具仍需满足目标运行时和内核权限要求。
        </div>
        <div className="guide-links">
          {canWrite && (
            <Link className="button primary" to="/tasks/new">
              创建诊断
              <ArrowUpRight size={16} />
            </Link>
          )}
          <Link className="button secondary" to="/agents">
            查看节点
          </Link>
        </div>
      </section>
    </>
  );
}
