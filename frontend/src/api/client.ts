export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    headers: { "content-type": "application/json", ...(options?.headers ?? {}) },
    ...options,
  });
  const data = (await response.json()) as T | { error?: string; detail?: string };
  if (!response.ok) {
    const errorData = data as { error?: string; detail?: string };
    const message = errorData.error ?? errorData.detail ?? String(response.status);
    throw new Error(message || String(response.status));
  }
  return data as T;
}
