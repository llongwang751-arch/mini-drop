/**
 * 趋势图只消费服务端统计序列，不在浏览器伪造采样点或成功率。
 * 图形提供快速阅读入口，具体数量与时间口径仍以页面文字和接口数据为准。
 */
import { useRef, useState, type KeyboardEvent } from "react";
import type { Overview } from "../types";

const hourFormat = new Intl.DateTimeFormat("zh-CN", {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "Asia/Shanghai",
});
const dateFormat = new Intl.DateTimeFormat("zh-CN", {
  month: "numeric",
  day: "numeric",
  timeZone: "Asia/Shanghai",
});

export default function TrendChart({ data }: { data: Overview["trend"] }) {
  const [focusedIndex, setFocusedIndex] = useState(23);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const bars = useRef<Array<HTMLButtonElement | null>>([]);
  const lastHour = Math.floor(Date.now() / 3600000) * 3600000;
  const values = Array.from({ length: 24 }, (_, i) => {
    const time = lastHour - (23 - i) * 3600000;
    return (
      data.find(
        (d) =>
          Math.floor(new Date(d.time).getTime() / 3600000) * 3600000 === time,
      ) || { time: new Date(time).toISOString(), total: 0, success: 0 }
    );
  });
  const max = Math.max(1, ...values.map((d) => d.total));
  const total = values.reduce((sum, d) => sum + d.total, 0);
  const activeIndex = hoverIndex ?? focusedIndex;
  const active = values[activeIndex];

  function moveFocus(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next: number;
    switch (event.key) {
      case "ArrowLeft":
        next = Math.max(0, index - 1);
        break;
      case "ArrowRight":
        next = Math.min(23, index + 1);
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = 23;
        break;
      default:
        return;
    }
    event.preventDefault();
    setHoverIndex(null);
    setFocusedIndex(next);
    bars.current[next]?.focus();
  }

  return (
    <div className="ov-chart">
      <div className="ov-chart-summary">
        <p className="ov-chart-total">
          <strong>{total.toLocaleString("zh-CN")}</strong>
          <span>次诊断</span>
        </p>
        <div className="ov-chart-readout" aria-hidden="true">
          <span>
            {dateFormat.format(new Date(active.time))}{" "}
            {hourFormat.format(new Date(active.time))}
          </span>
          <strong>{active.total.toLocaleString("zh-CN")} 次</strong>
        </div>
      </div>
      <div className="ov-chart-plot">
        <div className="ov-chart-scale" aria-hidden="true">
          <span>{max}</span>
          <span>0</span>
        </div>
        <div
          className="ov-chart-bars"
          role="group"
          aria-label={`近 24 小时诊断活动，共 ${total} 次。使用左右方向键查看每小时数量。`}
          onMouseLeave={() => setHoverIndex(null)}
        >
          {values.map((d, i) => (
            <button
              key={d.time}
              ref={(element) => {
                bars.current[i] = element;
              }}
              type="button"
              className="ov-chart-column"
              tabIndex={i === focusedIndex ? 0 : -1}
              data-active={i === activeIndex || undefined}
              data-empty={d.total === 0 || undefined}
              aria-label={`${dateFormat.format(new Date(d.time))} ${hourFormat.format(new Date(d.time))}，创建 ${d.total} 次诊断`}
              title={`${dateFormat.format(new Date(d.time))} ${hourFormat.format(new Date(d.time))} · ${d.total} 次诊断`}
              onMouseEnter={() => setHoverIndex(i)}
              onFocus={() => {
                setHoverIndex(null);
                setFocusedIndex(i);
              }}
              onKeyDown={(event) => moveFocus(event, i)}
            >
              <span
                className="ov-chart-bar"
                style={{ height: `${(d.total / max) * 100}%` }}
                aria-hidden="true"
              />
            </button>
          ))}
        </div>
      </div>
      <div className="ov-chart-times" aria-hidden="true">
        {[0, 12, 23].map((i) => (
          <span key={i}>{hourFormat.format(new Date(values[i].time))}</span>
        ))}
      </div>
      {total === 0 && <p className="ov-chart-empty">近 24 小时暂无新诊断</p>}
    </div>
  );
}
