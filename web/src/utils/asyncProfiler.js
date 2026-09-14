// Decode async-profiler's data grammar. Never execute HTML or JavaScript from an artifact.
const MAX_INPUT_CHARS = 8 * 1024 * 1024;
const MAX_FRAMES = 50_000;
const MAX_DEPTH = 256;

function decodeString(value) {
  const escapes = { n: "\n", r: "\r", t: "\t", b: "\b", f: "\f", v: "\v", "0": "\0", "\\": "\\", "'": "'", '"': '"', "/": "/" };
  return value.replace(/\\(u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|[\s\S])/g, (_, escaped) => {
    if (escaped[0] === "u" && escaped.length === 5) return String.fromCharCode(parseInt(escaped.slice(1), 16));
    if (escaped[0] === "x" && escaped.length === 3) return String.fromCharCode(parseInt(escaped.slice(1), 16));
    if (Object.prototype.hasOwnProperty.call(escapes, escaped)) return escapes[escaped];
    throw new Error("Java 火焰图包含不支持的字符串转义");
  });
}

export function parseAsyncProfilerHtml(text) {
  if (typeof text !== "string" || !text || text.length > MAX_INPUT_CHARS) {
    throw new Error("Java 火焰图为空或超过安全解析上限");
  }
  const poolMatch = /const\s+cpool\s*=\s*\[([\s\S]*?)\];\s*unpack\(cpool\);/.exec(text);
  if (!poolMatch) throw new Error("无法识别 Java async-profiler 数据格式");
  const tokens = /'((?:\\.|[^'\\])*)'/g;
  const encoded = [...poolMatch[1].matchAll(tokens)].map((match) => decodeString(match[1]));
  if (!encoded.length || encoded.length > MAX_FRAMES || poolMatch[1].replace(tokens, "").replace(/[\s,]/g, "")) {
    throw new Error("Java 火焰图函数名表无效或过大");
  }
  const pool = [encoded[0]];
  for (const value of encoded.slice(1)) {
    const prefix = value.charCodeAt(0) - 32;
    if (!value || prefix < 0 || prefix > pool.at(-1).length) throw new Error("Java 火焰图函数名前缀无效");
    pool.push(pool.at(-1).slice(0, prefix) + value.slice(1));
  }

  let level = 0;
  let left = 0;
  let width = 0;
  let count = 0;
  let root = null;
  const stack = [];
  const frameCalls = text.slice(poolMatch.index + poolMatch[0].length).matchAll(/^\s*([fun])\(([^)]*)\)/gm);
  for (const match of frameCalls) {
    if (++count > MAX_FRAMES) throw new Error("Java 火焰图超过安全帧数上限，请下载原始产物分析");
    const parts = match[2].split(",").map((part) => part.trim());
    if (parts.some((part) => !/^-?\d+$/.test(part))) throw new Error("Java 火焰图采样帧参数无效");
    const values = parts.map(Number);
    if (!values.every(Number.isSafeInteger)) throw new Error("Java 火焰图采样值超出安全范围");
    const [key] = values;
    if (match[1] === "f") {
      if (values.length < 3) throw new Error("Java 火焰图缺少帧坐标");
      level = values[1];
      left += values[2];
      width = values[3] || width;
    } else {
      if (match[1] === "u") level += 1;
      else left += width;
      width = values[1] || width;
    }
    const nameIndex = Math.floor(key / 8);
    if (key < 0 || nameIndex >= pool.length || level < 0 || level >= MAX_DEPTH || left < 0 || width <= 0 || !Number.isSafeInteger(left + width)) {
      throw new Error("Java 火焰图包含无效或超限的采样帧");
    }
    const node = { name: pool[nameIndex], value: width, children: [] };
    const parent = stack[level - 1];
    if (level === 0) {
      if (root || left !== 0) throw new Error("Java 火焰图根节点不一致");
      root = node;
    } else {
      if (!parent || left < parent.left || left + width > parent.left + parent.node.value || left < parent.childEnd) {
        throw new Error("Java 火焰图父子采样范围不一致");
      }
      parent.node.children.push(node);
      parent.childEnd = left + width;
    }
    stack.length = level;
    stack.push({ node, left, childEnd: left });
  }
  if (!root || !root.children.length) throw new Error("Java 火焰图没有可显示的函数样本");
  return root;
}
