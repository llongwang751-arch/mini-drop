/**
 * 导入的 gzip/JSON/调用树数据用于验证限额、错误分类和短期 URL 边界，不连接对象存储。
 */
import { describe, expect, it } from "vitest";
import { gzipSync } from "node:zlib";
import {
  decodeFlameGzip,
  readBounded,
  signedURL,
  expiredSignature,
  selectFlameArtifact,
  parseAnalysisEnvelope,
} from "./profile-data";
import type { Task } from "./types";

const tree = {
  name: "<img onerror=unsafe>",
  value: 3,
  self: 0,
  children: [{ name: "safe", value: 3, self: 3, children: [] }],
};
describe("bounded imported flame artifact data", () => {
  it.each([
    [401, "PROFILE_UNAUTHORIZED"],
    [403, "PROFILE_FORBIDDEN"],
    [410, "PROFILE_GONE"],
  ])(
    "retains specific HTTP %i even when the error body is not JSON",
    (status, code) => {
      const bytes = new TextEncoder().encode(
        "<html>redacted upstream error</html>",
      ).buffer;
      expect(() =>
        parseAnalysisEnvelope(bytes, status as number, "fixture-request"),
      ).toThrow(
        expect.objectContaining({ code, requestId: "fixture-request", status }),
      );
    },
  );
  it("decodes gzip and validates weights without interpreting node HTML", async () => {
    const bytes = gzipSync(JSON.stringify(tree));
    expect(
      await decodeFlameGzip(
        bytes.buffer.slice(
          bytes.byteOffset,
          bytes.byteOffset + bytes.byteLength,
        ) as ArrayBuffer,
      ),
    ).toEqual(tree);
  });
  it("rejects malformed gzip, UTF8, JSON and inconsistent trees", async () => {
    for (const bytes of [
      new Uint8Array([1, 2, 3]),
      gzipSync(Buffer.from([255])),
      gzipSync("{"),
      gzipSync(JSON.stringify({ ...tree, value: 4 })),
    ]) {
      await expect(
        decodeFlameGzip(
          bytes.buffer.slice(
            bytes.byteOffset,
            bytes.byteOffset + bytes.byteLength,
          ) as ArrayBuffer,
        ),
      ).rejects.toThrow();
    }
  });
  it("bounds expanded bytes while reading, including gzip bombs", async () => {
    const data = gzipSync("x".repeat(10000));
    await expect(
      decodeFlameGzip(
        data.buffer.slice(
          data.byteOffset,
          data.byteOffset + data.byteLength,
        ) as ArrayBuffer,
        128,
      ),
    ).rejects.toMatchObject({ code: "PROFILE_TOO_LARGE" });
  });
  it("cancels an over-limit body without buffering the rest", async () => {
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new Uint8Array(9));
      },
      cancel() {
        cancelled = true;
      },
    });
    await expect(readBounded(body, 8)).rejects.toMatchObject({
      code: "PROFILE_TOO_LARGE",
    });
    expect(cancelled).toBe(true);
  });
  it("permits only same-origin object gateway URLs without userinfo", () => {
    const base = "http://127.0.0.1:4178";
    expect(
      signedURL(base + "/objects/drop/a.gz?X-Amz-Signature=fixture", base),
    ).toContain("/objects/");
    for (const value of [
      "https://external.test/objects/x",
      "//external.test/objects/x",
      base + "/api/v1/auth/logout",
      "http://user:secret@127.0.0.1:4178/objects/x",
      base + "/objects/x#token",
      base + "/objects/../api/x",
    ])
      expect(() => signedURL(value, base)).toThrow();
  });
  it("only refreshes explicit bounded S3 signature expiry", () => {
    expect(
      expiredSignature(403, "<Error><Code>RequestExpired</Code></Error>"),
    ).toBe(true);
    expect(
      expiredSignature(
        403,
        "<Error><Code>AccessDenied</Code><Message>Request has expired</Message></Error>",
      ),
    ).toBe(true);
    for (const [status, body] of [
      [403, "<Code>AccessDenied</Code>"],
      [401, "<Code>ExpiredToken</Code>"],
      [410, "<Code>RequestExpired</Code>"],
      [503, "expired"],
      [403, "<Message>Request has expired</Message>"],
    ] as const)
      expect(expiredSignature(status, body)).toBe(false);
  });
  it("chooses only the current collected attempt RESULT, never older RAW or retained results", () => {
    const task = {
      analysis_status: "SUCCESS",
      collection_status: "COLLECTED",
      attempts: [
        { id: "9007199254740993", attempt: 2, status: "COLLECTED" },
        { id: "1", attempt: 1, status: "COLLECTED" },
      ],
      artifacts: [
        {
          id: "5",
          attempt_id: "1",
          kind: "RESULT",
          name: "flamegraph.json.gz",
          status: "ready",
        },
        {
          id: "6",
          attempt_id: "9007199254740993",
          kind: "RAW",
          name: "flamegraph.json.gz",
          status: "ready",
        },
        {
          id: "7",
          attempt_id: "9007199254740993",
          kind: "RESULT",
          name: "flamegraph.json.gz",
          status: "ready",
        },
      ],
    } as Task;
    expect(selectFlameArtifact(task)?.id).toBe("7");
    expect(
      selectFlameArtifact({
        ...task,
        attempts: [
          { id: "8", attempt: 3, status: "FAILED" },
          ...task.attempts!,
        ],
      }),
    ).toBeUndefined();
    expect(
      selectFlameArtifact({
        ...task,
        artifacts: task.artifacts!.map((a) => ({
          ...a,
          download_allowed: false,
        })),
      }),
    ).toBeUndefined();
  });
});
