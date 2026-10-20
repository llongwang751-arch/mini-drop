const key = "drop-navigation-compact-v1";

// Browser privacy settings may deny storage. Navigation must still work.
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
    // This remains a usable session preference when persistence is unavailable.
  }
}
