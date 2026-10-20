export const MAX_MARKDOWN_CHARACTERS = 32_768;

/** Conservative navigation policy. Markdown never creates a resource request. */
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
    // No other scheme, encoded scheme, protocol-relative URL, or backslash
    // ambiguity can escape the same-origin base used for relative navigation.
    if (/^[^/?#]*:/.test(inspected)) return undefined;
    const origin = "https://markdown.invalid";
    return new URL(value, origin).origin === origin ? value : undefined;
  } catch {
    return undefined;
  }
}
