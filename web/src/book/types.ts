export interface BookChapter {
  id: number;
  title: string;
  subtitle: string;
  part: string;
  readingMinutes: number;
  lead: string;
  prerequisites: string;
  content?: string;
  loadContent?: () => Promise<string>;
  sources: { path: string; label: string; local?: boolean }[];
}
