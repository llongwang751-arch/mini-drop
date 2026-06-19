import { statusText } from "../constants";
import type { StatusValue } from "../types";

export function StatusBadge({ value }: { value: StatusValue }) {
  return <span className={`status ${value}`}>{statusText[value] ?? value}</span>;
}
