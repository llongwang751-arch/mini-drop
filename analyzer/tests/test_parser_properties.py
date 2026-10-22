"""固定种子的合成 parser 性质测试；不依赖模型或第三方 property 框架。"""

from collections import Counter
import random
import unittest

from drop_analyzer.errors import AnalysisError
from drop_analyzer.folded import Limits, analyze_folded


class ParserPropertyTests(unittest.TestCase):
    def assert_tree_conservation(self, node):
        self.assertEqual(node["value"], node["self"] + sum(c["value"] for c in node["children"]))
        self.assertGreaterEqual(node["self"], 0)
        for child in node["children"]:
            self.assert_tree_conservation(child)

    def test_seeded_stacks_preserve_weights_recursion_and_permutation(self):
        for seed in (7, 1984, 20261023, 922337):
            rng = random.Random(seed)
            for case in range(32):
                with self.subTest(seed=seed, case=case):
                    rows, inclusive, own = [], Counter(), Counter()
                    for _ in range(rng.randint(1, 24)):
                        frames = [rng.choice(("synthetic-root", "递归", "parse value", "leaf")) for _ in range(rng.randint(1, 8))]
                        weight = rng.randint(2, 100)
                        rows.append((frames, weight))
                        own[frames[-1]] += weight
                        for frame in set(frames):
                            inclusive[frame] += weight
                    raw = "\n".join(f"{';'.join(frames)} {weight}" for frames, weight in rows).encode()
                    result = analyze_folded(raw)
                    self.assertEqual(result["metadata"]["sample_count"], sum(weight for _, weight in rows))
                    self.assert_tree_conservation(result["flamegraph"])
                    self.assertEqual({r["name"]: r["inclusive"] for r in result["top"]["inclusive"]}, dict(inclusive))
                    self.assertEqual({r["name"]: r["self"] for r in result["top"]["self"]}, {name: own[name] for name in inclusive})
                    rng.shuffle(rows)
                    shuffled = "\n".join(f"{';'.join(frames)} {weight}" for frames, weight in rows).encode()
                    self.assertEqual(result, analyze_folded(shuffled))
                    # 同一栈拆成两个正权重行，样本总量和火焰树不能改变；行数元数据允许不同。
                    split = "\n".join(f"{';'.join(frames)} 1\n{';'.join(frames)} {weight-1}" for frames, weight in rows).encode()
                    divided = analyze_folded(split)
                    self.assertEqual(result["flamegraph"], divided["flamegraph"])
                    self.assertEqual(result["top"], divided["top"])

    def test_bounded_arbitrary_bytes_fail_closed_or_preserve_tree(self):
        limits = Limits(max_input_bytes=2048, max_line_bytes=512, max_frame_bytes=128, max_depth=12, max_nodes=128, max_stacks=128, max_total_samples=10000)
        for seed in (7, 1984, 20261023, 922337):
            rng = random.Random(seed)
            for case in range(256):
                raw = rng.randbytes(rng.randint(0, 2048))
                with self.subTest(seed=seed, case=case):
                    try:
                        result = analyze_folded(raw, limits=limits)
                    except AnalysisError as error:
                        self.assertFalse(error.retryable)
                        self.assertIn(error.code, ("INVALID_INPUT", "INPUT_LIMIT_EXCEEDED"))
                    else:
                        self.assert_tree_conservation(result["flamegraph"])
                        self.assertGreater(result["metadata"]["sample_count"], 0)
                        self.assertLessEqual(result["metadata"]["node_count"], limits.max_nodes)

    def test_generated_malformed_tail_never_publishes_valid_prefix(self):
        for weight in (1, 17, 999, 2**53 - 1):
            for malformed in (b"root;;leaf 1", b"root -1", b"root nan", b"root 0", b"root \xff", b"root +1", b"root 1e3", b"root 1\x00"):
                with self.subTest(weight=weight, malformed=malformed):
                    with self.assertRaises(AnalysisError):
                        analyze_folded(f"synthetic;valid {weight}\n".encode() + malformed)


if __name__ == "__main__":
    unittest.main()
