import { parseJsonPayload } from "./parseJsonPayload";

self.onmessage = ({ data }) => {
  try {
    self.postMessage({ ok: true, ...parseJsonPayload(data.source, data.options) });
  } catch (error) {
    self.postMessage({ ok: false, error: error?.message || "JSON 解析失败" });
  }
};
