import { api } from "../api";
import {
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  ArrowCounterClockwise,
  MagnifyingGlass,
  ArrowsOut,
  Info,
} from "@phosphor-icons/react";
import type { FlameNode } from "../types";
import type { FlameLayout, FlameRect } from "./flame.worker";
import {
  functionRelations,
  type FlameOptions,
  type FlameThread,
} from "./flame-analysis";
import { ErrorPanel, Skeleton } from "./ui";
import { number } from "../api";

const ROW_HEIGHT = 28;
const SCROLL_PADDING_TOP = 18;
const ALL_NODES_THRESHOLD = 2000;
type DepthRange = { start: number; end: number };
export function graphHeight(rows: number) {
  return Math.max(104, rows * ROW_HEIGHT + 20);
}
export function visibleDepthRange(
  heightPx: number,
  scrollTop: number,
  viewportHeight: number,
  rows: number,
  overscan = 3,
): DepthRange {
  const top = Math.max(0, scrollTop - SCROLL_PADDING_TOP);
  const bottom = Math.min(
    heightPx,
    scrollTop + viewportHeight - SCROLL_PADDING_TOP,
  );
  return {
    start: Math.max(0, Math.floor((heightPx - bottom) / ROW_HEIGHT) - overscan),
    end: Math.min(rows, Math.ceil((heightPx - top) / ROW_HEIGHT) + overscan),
  };
}
interface FrameProps {
  rect: FlameRect;
  y: number;
  total: number;
  unit: string;
  matched: boolean;
  tabbable: boolean;
  activate: (rect: FlameRect) => void;
  hover: (rect?: FlameRect) => void;
  focus: (rect: FlameRect) => void;
  navigate: (rect: FlameRect, key: string) => void;
}
const FlameFrame = memo(function FlameFrame({
  rect,
  y,
  total,
  unit,
  matched,
  tabbable,
  activate,
  hover,
  focus,
  navigate,
}: FrameProps) {
  const visibleLabel =
    rect.name.length > Math.floor(rect.width / 6)
      ? `${rect.name.slice(0, Math.max(1, Math.floor(rect.width / 6) - 2))}…`
      : rect.name;
  const accessibleLabel =
    visibleLabel === rect.name ? rect.name : `${visibleLabel}, ${rect.name}`;
  return (
    <g
      role="button"
      tabIndex={tabbable ? 0 : -1}
      data-flame-id={rect.id}
      data-match={matched || undefined}
      aria-label={`${accessibleLabel}, ${rect.value} ${unit}, 点击聚焦`}
      onClick={() => activate(rect)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          activate(rect);
        } else if (
          ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home"].includes(
            event.key,
          )
        ) {
          event.preventDefault();
          navigate(rect, event.key);
        }
      }}
      onMouseEnter={() => hover(rect)}
      onMouseLeave={() => hover()}
      onFocus={() => focus(rect)}
      onBlur={() => hover()}
    >
      <rect
        x={rect.x}
        y={y}
        width={Math.max(0.3, rect.width - 0.8)}
        height="25"
        rx="2"
        fill={rect.color}
        stroke={matched ? "#fff" : "transparent"}
        strokeWidth={matched ? 2 : 0}
        vectorEffect="non-scaling-stroke"
      />
      {rect.width > 28 && (
        <text
          x={rect.x + 6}
          y={y + 16}
          fill="#211c1a"
          fontSize="10.5"
          fontFamily="IBM Plex Mono,monospace"
          pointerEvents="none"
        >
          {visibleLabel}
        </text>
      )}
      <title>
        {rect.name}
        {"\n"}总计 {rect.value} · 自身 {rect.self} ·{" "}
        {((rect.value / (total || 1)) * 100).toFixed(2)}%
      </title>
    </g>
  );
});

export default function Flamegraph({
  data,
  unit = "samples",
  threads = [],
  focusRequest,
}: {
  data: FlameNode;
  unit?: string;
  threads?: FlameThread[];
  focusRequest?: { name: string; token: number };
}) {
  const [root, setRoot] = useState(data);
  const [search, setSearch] = useState("");
  const [layout, setLayout] = useState<FlameLayout>();
  const [error, setError] = useState<string>();
  const [hover, setHover] = useState<FlameRect>();
  const [expanded, setExpanded] = useState(false);
  const [activeId, setActiveId] = useState<string>();
  const [options, setOptions] = useState<FlameOptions>({
    metric: "total",
    sort: "weight",
    palette: "warm",
  });
  const [selectedName, setSelectedName] = useState<string>();
  const [windowRange, setWindowRange] = useState<{
    layout: FlameLayout;
    range: DepthRange;
  }>();
  const scrollRef = useRef<HTMLDivElement>(null);
  const preserveBottomRef = useRef(false);
  const previousRootRef = useRef<FlameNode | undefined>(undefined);
  const layoutScrollRef = useRef<
    { top: number; atBottom: boolean } | undefined
  >(undefined);
  const frameRef = useRef<number | undefined>(undefined);
  const layoutRef = useRef<FlameLayout | undefined>(undefined);
  const heightPx = graphHeight(layout?.renderableRows || 0);
  const isVirtual = !!layout && layout.rects.length > ALL_NODES_THRESHOLD;
  useEffect(() => {
    setRoot(data);
    setSelectedName(undefined);
    setOptions((previous) => ({ ...previous, threadRoot: undefined }));
  }, [data]);
  useEffect(() => {
    if (!focusRequest) return;
    try {
      const match = functionRelations(data, focusRequest.name).strongest;
      if (match) {
        setRoot(match);
        setSearch("");
        setSelectedName(match.name);
        setOptions((previous) => ({ ...previous, threadRoot: undefined }));
      }
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "调用树格式无效");
    }
  }, [data, focusRequest]);
  useEffect(() => {
    const scroll = scrollRef.current;
    layoutScrollRef.current =
      scroll && previousRootRef.current === root
        ? {
            top: scroll.scrollTop,
            atBottom:
              scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight <=
              ROW_HEIGHT,
          }
        : undefined;
    previousRootRef.current = root;
    setLayout(undefined);
    setError(undefined);
    setHover(undefined);
    setActiveId(undefined);
    const worker = new Worker(new URL("./flame.worker.ts", import.meta.url), {
      type: "module",
    });
    worker.onmessage = (event) => {
      if (event.data.error) {
        setError(event.data.error);
        void api("/client-errors", {
          method: "POST",
          body: JSON.stringify({ code: "FLAME_LAYOUT_FAILED" }),
        }).catch(() => {});
      } else setLayout(event.data);
    };
    worker.onerror = () => {
      setError("火焰图无法解析，请检查分析结果格式。");
      void api("/client-errors", {
        method: "POST",
        body: JSON.stringify({ code: "FLAME_LAYOUT_FAILED" }),
      }).catch(() => {});
    };
    worker.postMessage({
      root,
      options: {
        ...options,
        threadRoot: root === data ? options.threadRoot : undefined,
      },
    });
    return () => worker.terminate();
  }, [root, data, options]);
  const updateWindow = useCallback(() => {
    const scroll = scrollRef.current;
    const current = layoutRef.current;
    if (!scroll || !current) return;
    const range = visibleDepthRange(
      graphHeight(current.renderableRows),
      scroll.scrollTop,
      scroll.clientHeight,
      current.renderableRows,
    );
    setWindowRange((previous) =>
      previous?.layout === current &&
      previous.range.start === range.start &&
      previous.range.end === range.end
        ? previous
        : { layout: current, range },
    );
  }, []);
  const scheduleWindow = useCallback(() => {
    if (frameRef.current !== undefined) return;
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = undefined;
      setHover(undefined);
      updateWindow();
    });
  }, [updateWindow]);
  useLayoutEffect(() => {
    layoutRef.current = layout;
    const scroll = scrollRef.current;
    if (!layout || !scroll) return;
    const anchor = layoutScrollRef.current;
    scroll.scrollTop =
      anchor && !anchor.atBottom ? anchor.top : scroll.scrollHeight;
    updateWindow();
    const observer = new ResizeObserver(scheduleWindow);
    observer.observe(scroll);
    return () => {
      observer.disconnect();
      if (frameRef.current !== undefined)
        cancelAnimationFrame(frameRef.current);
      frameRef.current = undefined;
    };
  }, [layout, updateWindow, scheduleWindow]);
  useLayoutEffect(() => {
    const scroll = scrollRef.current;
    if (scroll && preserveBottomRef.current) {
      scroll.scrollTop = scroll.scrollHeight;
    }
    updateWindow();
  }, [expanded, updateWindow]);
  const range =
    windowRange && windowRange.layout === layout
      ? windowRange.range
      : visibleDepthRange(
          heightPx,
          Math.max(0, heightPx + 30 - 550),
          550,
          layout?.renderableRows || 0,
        );
  const visible = useMemo(() => {
    if (!layout) return [];
    const rows = isVirtual
      ? layout.rows.slice(range.start, range.end)
      : layout.rows;
    if (!rows.length) return [];
    return layout.rects
      .slice(rows[0].start, rows.at(-1)!.end)
      .filter((rect) => rect.renderable);
  }, [layout, isVirtual, range.start, range.end]);
  const searchTerm = search.trim().toLowerCase();
  const matches = useMemo(
    () =>
      searchTerm && layout
        ? layout.rects.filter((rect) => rect.nameLower.includes(searchTerm))
        : [],
    [layout, searchTerm],
  );
  const matchedIds = useMemo(
    () => new Set(matches.map((rect) => rect.id)),
    [matches],
  );
  const activeVisibleId = visible.some((rect) => rect.id === activeId)
    ? activeId
    : visible[0]?.id;
  const activate = useCallback((rect: FlameRect) => {
    setRoot(rect.node);
    setSelectedName(rect.name);
  }, []);
  const relations = useMemo(
    () => (selectedName ? functionRelations(data, selectedName) : undefined),
    [data, selectedName],
  );
  const changeView = (patch: Partial<FlameOptions>) => {
    if ("threadRoot" in patch || "hideSystem" in patch) {
      setRoot(data);
      setSelectedName(undefined);
    }
    setOptions((previous) => ({ ...previous, ...patch }));
  };
  const showHover = useCallback((rect?: FlameRect) => setHover(rect), []);
  const focus = useCallback((rect: FlameRect) => {
    setHover(rect);
    setActiveId(rect.id);
  }, []);
  const navigate = useCallback(
    (rect: FlameRect, key: string) => {
      const current = layoutRef.current;
      const scroll = scrollRef.current;
      if (!current || !scroll) return;
      let target: FlameRect | undefined;
      if (key === "Home") target = current.rects[0];
      else {
        const depth =
          rect.depth + (key === "ArrowUp" ? 1 : key === "ArrowDown" ? -1 : 0);
        const row = current.rows[depth];
        if (!row) return;
        const candidates = current.rects
          .slice(row.start, row.end)
          .filter((item) => item.renderable);
        if (key === "ArrowLeft" || key === "ArrowRight") {
          const index = candidates.findIndex((item) => item.id === rect.id);
          target = candidates[index + (key === "ArrowRight" ? 1 : -1)];
        } else {
          const center = rect.x + rect.width / 2;
          target = candidates.find(
            (item) => item.x <= center && item.x + item.width >= center,
          );
        }
      }
      if (!target) return;
      const id = target.id;
      const y =
        graphHeight(current.renderableRows) -
        (target.depth + 1) * ROW_HEIGHT +
        SCROLL_PADDING_TOP;
      if (y < scroll.scrollTop) scroll.scrollTop = y;
      else if (y + ROW_HEIGHT > scroll.scrollTop + scroll.clientHeight)
        scroll.scrollTop = y + ROW_HEIGHT - scroll.clientHeight;
      setActiveId(id);
      scheduleWindow();
      let attempts = 0;
      const focusTarget = () => {
        const element = scroll.querySelector<SVGGElement>(
          `[data-flame-id="${id}"]`,
        );
        if (element) element.focus({ preventScroll: true });
        else if (++attempts < 5) requestAnimationFrame(focusTarget);
      };
      requestAnimationFrame(focusTarget);
    },
    [scheduleWindow],
  );
  return (
    <div className={`flame-component ${expanded ? "expanded" : ""}`}>
      <div className="flame-toolbar">
        <label className="search-field">
          <MagnifyingGlass size={16} />
          <span className="sr-only">搜索函数</span>
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索函数名称…"
          />
        </label>
        <div className="flame-tools">
          {searchTerm && (
            <button
              className="button ghost small"
              disabled={!matches.length}
              onClick={() => activate(matches[0])}
              aria-label={`聚焦首个匹配函数，共${matches.length}处`}
            >
              定位 {number(matches.length)}
            </button>
          )}
          <button
            className="button ghost small"
            disabled={
              root === data &&
              !search &&
              !options.threadRoot &&
              !options.hideSystem &&
              options.metric === "total" &&
              options.sort === "weight" &&
              options.palette === "warm"
            }
            onClick={() => {
              setRoot(data);
              setSearch("");
              setSelectedName(undefined);
              setOptions({ metric: "total", sort: "weight", palette: "warm" });
              setActiveId(undefined);
              if (scrollRef.current)
                scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
              scheduleWindow();
            }}
          >
            <ArrowCounterClockwise size={15} />
            重置
          </button>
          <button
            className="icon-button"
            onClick={() => {
              const scroll = scrollRef.current;
              preserveBottomRef.current =
                !!scroll &&
                scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight <=
                  ROW_HEIGHT;
              setExpanded((value) => !value);
            }}
            aria-label={expanded ? "退出展开" : "展开火焰图"}
          >
            <ArrowsOut size={17} />
          </button>
        </div>
      </div>
      <details className="flame-settings">
        <summary>视图与筛选</summary>
        <div className="flame-settings-grid">
          <label className="field">
            <span>权重视图</span>
            <select
              value={options.metric}
              onChange={(event) =>
                changeView({
                  metric: event.target.value as FlameOptions["metric"],
                })
              }
            >
              <option value="total">总计 · 调用关系</option>
              <option value="self">自身 · 函数聚合</option>
            </select>
          </label>
          <label className="field">
            <span>排序</span>
            <select
              value={options.sort}
              onChange={(event) =>
                changeView({ sort: event.target.value as FlameOptions["sort"] })
              }
            >
              <option value="weight">权重降序</option>
              <option value="name">函数名称</option>
              <option value="input">原始顺序</option>
            </select>
          </label>
          <label className="field">
            <span>颜色方案</span>
            <select
              value={options.palette}
              onChange={(event) =>
                changeView({
                  palette: event.target.value as FlameOptions["palette"],
                })
              }
            >
              <option value="warm">暖色</option>
              <option value="ocean">海蓝</option>
              <option value="mono">蓝灰</option>
            </select>
          </label>
          <label className="field">
            <span>线程</span>
            <select
              disabled={!threads.length}
              value={options.threadRoot || ""}
              onChange={(event) =>
                changeView({ threadRoot: event.target.value || undefined })
              }
            >
              <option value="">
                {threads.length ? "全部线程" : "原始数据无独立线程记录"}
              </option>
              {threads.map((thread) => (
                <option key={thread.root_name} value={thread.root_name}>
                  {thread.comm} · PID {thread.pid} / TID {thread.tid}
                </option>
              ))}
            </select>
          </label>
          <label className="flame-system-filter">
            <input
              type="checkbox"
              checked={!!options.hideSystem}
              onChange={(event) =>
                changeView({ hideSystem: event.target.checked })
              }
            />
            隐藏系统栈
          </label>
        </div>
        <p className="flame-view-note">
          系统栈规则：kernel、vdso、libc 及内核系统调用入口；未知地址保留。
          {options.metric === "self"
            ? "自身视图按函数聚合，调用关系从原始树查看。"
            : "区块宽度采用总计权重，保留调用层级。"}
        </p>
      </details>
      <div className="flame-context">
        <span className="mono">
          {root === data ? options.threadRoot || "全部调用栈" : root.name}
        </span>
        <span>
          {layout ? number(layout.total) : "—"} {unit}
        </span>
      </div>
      {!!layout?.hiddenFrames && (
        <p className="flame-view-note">
          已隐藏 {number(layout.hiddenFrames)} 个系统帧；其自身权重{" "}
          {number(layout.hiddenSelf)} {unit} 仍计入根节点，未转移给业务函数。
        </p>
      )}
      {!!layout?.tinyFrames && layout.renderableRows === 1 && (
        <p className="flame-view-note flame-thin-notice">
          <Info size={14} aria-hidden="true" />
          <span>
            {number(layout.tinyFrames)}{" "}
            个细小帧暂不绘制。搜索函数并点击“定位”可放大查看，完整数据仍保留。
          </span>
        </p>
      )}
      {error ? (
        <ErrorPanel error={new Error(error)} />
      ) : !layout ? (
        <Skeleton rows={5} />
      ) : (
        <div
          className="flame-scroll"
          ref={scrollRef}
          onScroll={scheduleWindow}
          tabIndex={isVirtual ? 0 : undefined}
          role={isVirtual ? "region" : undefined}
          aria-label={isVirtual ? "火焰图滚动区域，方向键浏览节点" : undefined}
        >
          <svg
            className="flame-svg"
            viewBox={`0 0 1000 ${heightPx}`}
            height={heightPx}
            preserveAspectRatio="none"
            style={{ minWidth: 1000, height: heightPx }}
            role="group"
            aria-label="可交互火焰图，点击函数可聚焦子树"
            data-searching={!!searchTerm}
            data-total-nodes={layout.rects.length}
            data-visible-nodes={visible.length}
          >
            <title>调用栈火焰图</title>
            <style>{`.flame-svg[data-searching="true"] > g:not([data-match="true"]) > rect { fill: #343236; } .flame-svg[data-searching="true"] > g:not([data-match="true"]) > text { fill: #c4bfc4; }`}</style>
            {visible.map((rect) => (
              <FlameFrame
                key={rect.id}
                rect={rect}
                y={heightPx - (rect.depth + 1) * ROW_HEIGHT}
                total={layout.total}
                unit={unit}
                matched={matchedIds.has(rect.id)}
                tabbable={!isVirtual || activeVisibleId === rect.id}
                activate={activate}
                hover={showHover}
                focus={focus}
                navigate={navigate}
              />
            ))}
          </svg>
        </div>
      )}
      <div className="flame-footer">
        <span>
          <Info size={13} />
          {hover ? (
            <>
              <strong className="mono">{hover.name}</strong> 总计{" "}
              {number(hover.value)} · 自身 {number(hover.self)}
            </>
          ) : (
            "宽度代表开销占比 · 点击聚焦 · 方向键浏览 · Enter 聚焦"
          )}
        </span>
        <span className="mono" role="status" aria-live="polite">
          {searchTerm ? `${number(matches.length)} matches · ` : ""}
          {number(layout?.rects.length || 0)} nodes
          {isVirtual ? ` · ${number(visible.length)} visible` : ""}
        </span>
      </div>
      {selectedName && relations && (
        <details className="flame-relations" open>
          <summary>调用关系 · {selectedName}</summary>
          <p className="flame-view-note">
            基于原始调用树的相邻边权重，递归可能在多条边出现；不表示调用次数。TopN
            同名函数跳转到总计权重最高的位置。
          </p>
          <div className="flame-relation-columns">
            {[
              ["调用方 Callers", relations.callers],
              ["被调用方 Callees", relations.callees],
            ].map(([label, entries]) => (
              <section key={String(label)} aria-label={String(label)}>
                <h3>{String(label)}</h3>
                {(entries as { name: string; weight: number }[]).length ? (
                  <ul>
                    {(entries as { name: string; weight: number }[])
                      .slice(0, 12)
                      .map((entry) => (
                        <li key={entry.name}>
                          <button
                            type="button"
                            className="text-button mono"
                            onClick={() => {
                              const node = functionRelations(
                                data,
                                entry.name,
                              ).strongest;
                              if (node) {
                                setRoot(node);
                                setSelectedName(node.name);
                              }
                            }}
                          >
                            {entry.name}
                          </button>
                          <span className="mono">
                            {number(entry.weight)} {unit}
                          </span>
                        </li>
                      ))}
                  </ul>
                ) : (
                  <p className="dim">没有记录到相邻调用。</p>
                )}
              </section>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}
