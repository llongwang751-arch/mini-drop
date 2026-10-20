/**
 * 轮询与重试的统一策略：暂时性故障可退避，权限错误和终态应停止无意义请求。
 * 把单次请求重试与下一轮轮询间隔分开，避免服务异常时请求量反而增加。
 */
import { APIError } from "./api";

interface PollingQuery {
  state: { status: string; errorUpdateCount: number; error: unknown };
}
const consecutiveFailures = new WeakMap<
  object,
  { observedErrors: number; failures: number }
>();

// 主动取消不应重试；网络、限流与服务端错误可恢复，权限/参数错误应交给用户处理。
function transient(error: unknown): boolean {
  if (error instanceof Error && error.name === "AbortError") return false;
  if (!(error instanceof APIError)) return true;
  return (
    error.status === 0 ||
    error.status === 408 ||
    error.status === 429 ||
    error.status >= 500
  );
}

export function retryQuery(failureCount: number, error: unknown): boolean {
  return failureCount < 1 && transient(error);
}

/**
 * 统计连续失败的请求轮次，而不是单轮内部重试次数或组件重新渲染次数。
 * WeakMap 跟随 Query 缓存对象释放，不修改 TanStack 自己维护的状态。
 * 页面隐藏时暂停和同一查询在途去重仍交给 TanStack，避免再造一套冲突的定时器。
 */
export function pollingInterval(
  query: PollingQuery,
  baseMs: number,
  terminal = false,
): number | false {
  if (
    terminal ||
    (query.state.status === "error" && !transient(query.state.error))
  )
    return false;
  // 观察服务端请求失败计数的增量，而非每次 render 都增加退避级别。
  const count = query.state.errorUpdateCount;
  let previous = consecutiveFailures.get(query);
  if (!previous || count < previous.observedErrors) {
    previous = {
      observedErrors: count,
      failures: query.state.status === "error" ? 1 : 0,
    };
    consecutiveFailures.set(query, previous);
  } else if (query.state.status === "success") {
    previous.failures = 0;
    previous.observedErrors = count;
  } else if (count > previous.observedErrors) {
    previous.failures = Math.min(
      16,
      previous.failures + count - previous.observedErrors,
    );
    previous.observedErrors = count;
  }
  return Math.min(60000, baseMs * 2 ** Math.min(previous.failures, 16));
}
