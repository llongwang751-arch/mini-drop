import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { syncBrowserSession } from "../api/client";
import useSSE from "./useSSE";

/**
 * 控制面 SSE 单连接共享。
 *
 * AppLayout 通过 <SSEProvider> 持有整个应用唯一的 control 连接：
 * 先完成浏览器会话同步（API Key -> HttpOnly cookie），再启用连接。
 * 页面通过 useControlEvents 订阅事件；没有 Provider 时它退化为
 * 自开连接（与旧行为一致），保证独立使用与测试的兼容。
 */

const SSEContext = createContext(null);

const CONTROL_EVENTS = ["task_changed", "agent_status", "diagnosis_complete"];

export function SSEProvider({ children }) {
  const [connected, setConnected] = useState(false);
  const [sessionReady, setSessionReady] = useState(false);
  const subscribersRef = useRef({});

  useEffect(() => {
    let cancelled = false;
    const sync = async () => {
      try {
        await syncBrowserSession();
      } catch {
        // REST 错误由页面展示；SSE 连接仍可依赖既有 cookie 重试。
      }
      if (!cancelled) setSessionReady(true);
    };
    sync();
    window.addEventListener("mini-drop:credentials-changed", sync);
    return () => {
      cancelled = true;
      window.removeEventListener("mini-drop:credentials-changed", sync);
    };
  }, []);

  const emit = useCallback((event, data) => {
    const handlers = subscribersRef.current[event];
    if (!handlers) return;
    for (const handler of [...handlers]) {
      try {
        handler(data);
      } catch {
        // 单个订阅者异常不阻断其他订阅者。
      }
    }
  }, []);

  useSSE({
    channel: "control",
    enabled: sessionReady,
    onConnectionChange: setConnected,
    onTaskChanged: (data) => emit("task_changed", data),
    onAgentStatus: (data) => emit("agent_status", data),
    onDiagnosisComplete: (data) => emit("diagnosis_complete", data),
  });

  const subscribe = useCallback((event, handler) => {
    if (!CONTROL_EVENTS.includes(event)) {
      throw new Error(`unsupported control event: ${event}`);
    }
    const map = subscribersRef.current;
    if (!map[event]) map[event] = new Set();
    map[event].add(handler);
    return () => map[event]?.delete(handler);
  }, []);

  const value = useMemo(() => ({ connected, subscribe }), [connected, subscribe]);
  return <SSEContext.Provider value={value}>{children}</SSEContext.Provider>;
}

/** 读取共享 control 连接状态；不在 Provider 内时返回 null。 */
export function useControlSSE() {
  return useContext(SSEContext);
}

/**
 * 页面级事件订阅：有 Provider 时共享 shell 的唯一 control 连接；
 * 没有时自开一条连接（等价于直接使用 useSSE 的旧行为）。
 */
export function useControlEvents({ onTaskChanged, onAgentStatus, onDiagnosisComplete } = {}) {
  const shared = useContext(SSEContext);
  const handlersRef = useRef({ onTaskChanged, onAgentStatus, onDiagnosisComplete });
  handlersRef.current = { onTaskChanged, onAgentStatus, onDiagnosisComplete };

  useEffect(() => {
    if (!shared) return undefined;
    const unsubscribe = CONTROL_EVENTS.map((event) =>
      shared.subscribe(event, (data) => handlersRef.current?.[HANDLER_FOR_EVENT[event]]?.(data)),
    );
    return () => unsubscribe.forEach((fn) => fn());
  }, [shared]);

  useSSE(
    shared
      ? { channel: "control", enabled: false }
      : { channel: "control", onTaskChanged, onAgentStatus, onDiagnosisComplete },
  );
}

const HANDLER_FOR_EVENT = {
  task_changed: "onTaskChanged",
  agent_status: "onAgentStatus",
  diagnosis_complete: "onDiagnosisComplete",
};
