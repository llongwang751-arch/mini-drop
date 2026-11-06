export const BOOK_PREFERENCES_KEY = "drop-book:preferences:v1";

export interface BookPreferences {
  theme: "light" | "dark";
  fontSize: number;
}

export const defaultBookPreferences: BookPreferences = {
  theme: "light",
  fontSize: 18,
};

export function parseBookPreferences(value: string | null): BookPreferences {
  try {
    const data: unknown = value ? JSON.parse(value) : null;
    if (!data || typeof data !== "object") return defaultBookPreferences;
    const candidate = data as Record<string, unknown>;
    return {
      theme: candidate.theme === "dark" ? "dark" : "light",
      fontSize:
        typeof candidate.fontSize === "number" &&
        Number.isInteger(candidate.fontSize) &&
        candidate.fontSize >= 16 &&
        candidate.fontSize <= 22
          ? candidate.fontSize
          : 18,
    };
  } catch {
    return defaultBookPreferences;
  }
}

export function readBookPreferences(): BookPreferences {
  try {
    return parseBookPreferences(localStorage.getItem(BOOK_PREFERENCES_KEY));
  } catch {
    return defaultBookPreferences;
  }
}
