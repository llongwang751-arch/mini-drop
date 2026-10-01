import { readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";

const projectRoot = path.resolve(import.meta.dirname, "..");
const distDir = path.resolve(projectRoot, process.argv[2] || "web/dist");
const indexPath = path.join(distDir, "index.html");
const indexHtml = await readFile(indexPath, "utf8");

const entryAssets = [
  ...indexHtml.matchAll(/(?:src|href)="\/?(assets\/[^"]+\.js)"/g),
].map((match) => match[1]);
const uniqueEntryAssets = [...new Set(entryAssets)];

if (uniqueEntryAssets.length === 0) {
  throw new Error("web bundle check: index.html does not reference any JavaScript assets");
}

const forbiddenEntryPatterns = [/echarts/i, /flamegraph/i, /(?:^|\/)d3-/i];
const forbiddenEntries = uniqueEntryAssets.filter((asset) =>
  forbiddenEntryPatterns.some((pattern) => pattern.test(asset)),
);
if (forbiddenEntries.length > 0) {
  throw new Error(
    `web bundle check: heavy visualization code is eagerly loaded: ${forbiddenEntries.join(", ")}`,
  );
}

const entrySizes = await Promise.all(
  uniqueEntryAssets.map(async (asset) => ({
    asset,
    bytes: (await stat(path.join(distDir, asset))).size,
  })),
);
const entryBytes = entrySizes.reduce((total, item) => total + item.bytes, 0);
const entryBudgetBytes = 800 * 1024;
if (entryBytes > entryBudgetBytes) {
  throw new Error(
    `web bundle check: entry JavaScript is ${(entryBytes / 1024).toFixed(1)} KiB, budget is ${entryBudgetBytes / 1024} KiB`,
  );
}

const assetDir = path.join(distDir, "assets");
const chunks = (await readdir(assetDir)).filter((name) => name.endsWith(".js"));
const chunkSizes = await Promise.all(
  chunks.map(async (name) => ({ name, bytes: (await stat(path.join(assetDir, name))).size })),
);
const largestChunk = chunkSizes.sort((a, b) => b.bytes - a.bytes)[0];
const chunkBudgetBytes = 600 * 1024;
if (largestChunk?.bytes > chunkBudgetBytes) {
  throw new Error(
    `web bundle check: largest chunk ${largestChunk.name} is ${(largestChunk.bytes / 1024).toFixed(1)} KiB, budget is ${chunkBudgetBytes / 1024} KiB`,
  );
}

process.stdout.write(
  `web bundle check passed: entry ${(entryBytes / 1024).toFixed(1)} KiB across ${entrySizes.length} files; ` +
    `largest chunk ${largestChunk.name} ${(largestChunk.bytes / 1024).toFixed(1)} KiB\n`,
);
