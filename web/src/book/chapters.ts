import { foundationChapters } from "./chapters-foundations";
import { advancedChapters } from "./chapters-advanced";
import { practiceChapters } from "./chapters-practice";

export const chapters = [
  ...foundationChapters,
  ...advancedChapters,
  ...practiceChapters,
];

export function chapterUrl(id: number) {
  return `/book/chapter${id}/`;
}
