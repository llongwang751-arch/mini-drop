/**
 * 侧栏展开/收起只是浏览器本地偏好，不是权限或业务数据。
 * 存储访问失败时回退到可用默认值，保证导航仍能操作。
 */
const key = "drop-navigation-compact-v1";

// 隐私模式或浏览器策略可能禁止 localStorage；偏好保存失败不能阻断导航。
export function readCompactNavigation(): boolean {
  try {
    return localStorage.getItem(key) === "compact";
  } catch {
    return false;
  }
}

export function saveCompactNavigation(compact: boolean): void {
  try {
    localStorage.setItem(key, compact ? "compact" : "expanded");
  } catch {
    // 持久化不可用时仍保留本次会话的可用体验，不把存储故障抛到页面。
  }
}
