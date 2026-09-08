import { Tag } from "antd";
import { diagnosticStatusLabel } from "../utils/diagnosisDisplay";
import { statusColor } from "../utils/status";

/**
 * 可复用的状态标签组件。
 *
 * 根据 status 值自动匹配颜色，消除在多个页面中重复 <Tag color={statusColor(x)}> 的模式。
 *
 * @param {{ status: string, style?: object }} props
 */
export default function StatusTag({ status, style }) {
  return (
    <Tag color={statusColor(status)} style={style}>
      {diagnosticStatusLabel(status, "状态未知")}
    </Tag>
  );
}
