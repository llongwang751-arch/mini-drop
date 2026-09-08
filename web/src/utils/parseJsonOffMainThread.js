import { parseJsonPayload } from "./parseJsonPayload";

/** Parse a JSON string in a short-lived Worker so large artifacts do not stall React. */
export function parseJsonOffMainThread(source, options = {}) {
  if (typeof Worker === "undefined") {
    return Promise.resolve(parseJsonPayload(source, options));
  }

  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL("./parseJson.worker.js", import.meta.url), {
      type: "module",
    });

    const finish = () => worker.terminate();
    worker.onmessage = ({ data }) => {
      finish();
      if (data?.ok) {
        resolve({ value: data.value, limits: data.limits ?? null });
      } else {
        reject(new Error(data?.error || "JSON 解析失败"));
      }
    };
    worker.onerror = (event) => {
      finish();
      reject(new Error(event.message || "JSON Worker 执行失败"));
    };
    worker.postMessage({ source, options });
  });
}
