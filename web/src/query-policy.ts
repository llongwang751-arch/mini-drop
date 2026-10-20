import { APIError } from "./api";

interface PollingQuery {
  state: { status: string; errorUpdateCount: number; error: unknown };
}
const consecutiveFailures = new WeakMap<
  object,
  { observedErrors: number; failures: number }
>();

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

/** Count failed fetch cycles, rather than retry attempts or observer renders.
 * The WeakMap follows each cached Query's lifetime without modifying its state.
 * Hidden-page suspension and in-flight deduplication remain TanStack's policy.
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
