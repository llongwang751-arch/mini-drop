import { describe, expect, it } from "vitest";
import { parseAsyncProfilerHtml } from "./asyncProfiler";

const artifact = (frames, names = "'all', ' example.Root', ',Child', ' other'") =>
  `<script>const cpool = [${names}]; unpack(cpool);\n${frames}\n</script>`;

describe("async-profiler declarative import", () => {
  it("decodes prefix names and inclusive sample counts across siblings", () => {
    const tree = parseAsyncProfilerHtml(artifact("n(3,5)\nu(8)\nu(16,2)\nn(24,3)"));
    expect(tree.value).toBe(5);
    expect(tree.children[0].name).toBe("example.Root");
    expect(tree.children[0].children.map(x => [x.name, x.value])).toEqual([
      ["example.RootChild", 2], ["other", 3],
    ]);
  });

  it("ignores executable HTML and decodes escaped labels as text", () => {
    globalThis.profileExecuted = false;
    const html = artifact("n(3,1)\nu(8)", "'all', ' <img onerror=alert(1)>\\u4e2d\\\'文'") +
      "<script>globalThis.profileExecuted=true</script>";
    expect(parseAsyncProfilerHtml(html).children[0].name).toBe("<img onerror=alert(1)>中'文");
    expect(globalThis.profileExecuted).toBe(false);
    delete globalThis.profileExecuted;
  });

  it.each([
    "n(3,5)\nu(8,6)",
    "n(3,5)\nf(8,2,0,1)",
    "n(3,5)\nu(800,1)",
    "n(3,5)\nu(alert(1))",
    "n(3,5)\nu(8,-1)",
  ])("rejects invalid coordinates or executable arguments: %s", frames => {
    expect(() => parseAsyncProfilerHtml(artifact(frames))).toThrow();
  });

  it("bounds file size and rejects unrecognized formats", () => {
    expect(() => parseAsyncProfilerHtml("x".repeat(8 * 1024 * 1024 + 1))).toThrow();
    expect(() => parseAsyncProfilerHtml("<html>no data</html>")).toThrow();
  });
});
