import { useEffect, useRef, useState, useCallback } from "react";
import { createDiagnosisEventSource, createEventSource } from "../api/client";

/**
 * Server-Sent Events 实时事件 Hook。
 *
 * 连接后台 /api/events/stream，接收任务状态变更、
 * Agent 上下线、诊断完成等事件，触发回调。
 *
 * 特性：
 * - 自动重连（指数退避，最多 30s 间隔）
 * - 页面隐藏时保持连接
 * - 组件卸载时自动关闭
 *
 * @param {object} handlers
 * @param {(data: object) => void} [handlers.onTaskChanged]
 * @param {(data: object) => void} [handlers.onAgentStatus]
 * @param {(data: object) => void} [handlers.onDiagnosisComplete]
 * @param {(data: object) => void} [handlers.onDiagnosisProgress]
 * @param {(connected: boolean) => void} [handlers.onConnectionChange]
 * @param {"control"|"diagnosis"} [handlers.channel]
 * @param {string} [handlers.resourceId] - diagnosis channel 的诊断 ID
 * @returns {{ connected: boolean, reconnect: () => void }}
 */
export default function useSSE({
  onTaskChanged,
  onAgentStatus,
  onDiagnosisComplete,
  onDiagnosisProgress,
  onConnectionChange,
  channel = "control",
  resourceId = "",
} = {}) {
  const [connected, setConnected] = useState(false);
  const reconnectTimer = useRef(null);
  const retryCount = useRef(0);
  const sourceRef = useRef(null);
  const cursorRef = useRef(0);
  const mountedRef = useRef(false);
  const maxRetryDelay = 30000;

  const handlersRef = useRef({ onTaskChanged, onAgentStatus, onDiagnosisComplete, onDiagnosisProgress, onConnectionChange });
  handlersRef.current = { onTaskChanged, onAgentStatus, onDiagnosisComplete, onDiagnosisProgress, onConnectionChange };

  const connect = useCallback(() => {
    if (!mountedRef.current) return null;
    if (reconnectTimer.current) {
      clearTimeout(reconnectTimer.current);
      reconnectTimer.current = null;
    }
    sourceRef.current?.close();
    const es = channel === "diagnosis"
      ? createDiagnosisEventSource(resourceId, cursorRef.current)
      : createEventSource();
    sourceRef.current = es;

    es.onopen = () => {
      setConnected(true);
      retryCount.current = 0;
      handlersRef.current.onConnectionChange?.(true);
    };

    es.addEventListener("task_changed", (e) => {
      try {
        const data = JSON.parse(e.data);
        handlersRef.current.onTaskChanged?.(data);
      } catch {
        // 忽略解析错误
      }
    });

    es.addEventListener("agent_status", (e) => {
      try {
        const data = JSON.parse(e.data);
        handlersRef.current.onAgentStatus?.(data);
      } catch {
        // 忽略
      }
    });

    es.addEventListener("diagnosis_complete", (e) => {
      try {
        const data = JSON.parse(e.data);
        handlersRef.current.onDiagnosisComplete?.(data);
      } catch {
        // 忽略
      }
    });

    es.addEventListener("diagnosis_progress", (e) => {
      try {
        const data = JSON.parse(e.data);
        cursorRef.current = Math.max(cursorRef.current, Number(data.sequence) || 0);
        handlersRef.current.onDiagnosisProgress?.(data);
      } catch {
        // 忽略解析错误
      }
    });

    // 默认 message 事件作为兼容
    es.onmessage = (e) => {
      try {
        const raw = JSON.parse(e.data);
        const eventType = raw.event || raw.type;
        const data = raw.data || raw;
        if (eventType === "task_changed") handlersRef.current.onTaskChanged?.(data);
        else if (eventType === "agent_status") handlersRef.current.onAgentStatus?.(data);
        else if (eventType === "diagnosis_complete") handlersRef.current.onDiagnosisComplete?.(data);
        else if (eventType === "diagnosis_progress") handlersRef.current.onDiagnosisProgress?.(data);
      } catch {
        // 忽略
      }
    };

    es.onerror = () => {
      if (sourceRef.current !== es || !mountedRef.current) return;
      setConnected(false);
      handlersRef.current.onConnectionChange?.(false);
      es.close();

      // 指数退避重连
      const delay = Math.min(1000 * Math.pow(2, retryCount.current), maxRetryDelay);
      retryCount.current += 1;
      reconnectTimer.current = setTimeout(() => {
        reconnectTimer.current = null;
        if (mountedRef.current) connect();
      }, delay);
    };

    return es;
  }, [channel, resourceId]);

  useEffect(() => {
    mountedRef.current = true;
    cursorRef.current = 0;
    connect();
    return () => {
      mountedRef.current = false;
      sourceRef.current?.close();
      sourceRef.current = null;
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
    };
  }, [connect]);

  const reconnect = useCallback(() => {
    retryCount.current = 0;
    if (reconnectTimer.current) {
      clearTimeout(reconnectTimer.current);
      reconnectTimer.current = null;
    }
    sourceRef.current?.close();
    setConnected(false);
    connect();
  }, [connect]);

  return { connected, reconnect };
}
