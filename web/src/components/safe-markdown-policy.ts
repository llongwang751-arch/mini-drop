/**
 * 富文本共用的长度和链接策略，供 AST 转换与最终 HTML 清理两层共同使用。
 * 限制资源请求与导航协议，避免诊断文本成为脚本执行或凭据外送通道。
 */
export const MAX_MARKDOWN_CHARACTERS = 32_768;

/** 链接采用保守的导航白名单；解析 Markdown 本身不能发起任何资源请求。 */
export function safeMarkdownUrl(value: string | undefined): string | undefined {
  if (
    !value ||
    value.trim() !== value ||
    /[\\\u0000-\u0020\u007f-\u009f]/.test(value)
  )
    return undefined;
  let inspected = value;
  try {
    for (let round = 0; round < 2; round++) {
      inspected = decodeURIComponent(inspected);
      if (/[\\\u0000-\u001f\u007f-\u009f]/.test(inspected)) return undefined;
    }
    if (inspected.startsWith("//")) return undefined;
    if (/^https?:\/\/[^/]/i.test(value)) {
      const url = new URL(value);
      return url.hostname &&
        !url.username &&
        !url.password &&
        ["http:", "https:"].includes(url.protocol)
        ? value
        : undefined;
    }
    // 拒绝其他协议、编码后的协议、无协议域名和反斜杠歧义，避免相对链接绕出同源边界。
    if (/^[^/?#]*:/.test(inspected)) return undefined;
    const origin = "https://markdown.invalid";
    return new URL(value, origin).origin === origin ? value : undefined;
  } catch {
    return undefined;
  }
}
