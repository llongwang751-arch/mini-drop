"""失败语料导出的合成边界；不启动Go/Docker/模型或读取业务资料。"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import capture_fuzz_corpus as corpus

RAW = b'go test fuzz v1\n[]byte("explicit-synthetic")\n'


class FuzzCorpusBoundaryTests(unittest.TestCase):
    def test_untrusted_outcome_is_rejected_before_creating_public_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with self.assertRaisesRegex(ValueError, 'INVALID_NATIVE_FUZZ_OUTCOME'):
                corpus.capture(root, root / 'output', 'synthetic-private-body')
            self.assertFalse((root / 'output').exists())

    def source(self, root, target=None, name='a' * 16, raw=RAW):
        target = target or next(iter(corpus.TARGETS))
        path = root / corpus.TARGETS[target] / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def test_fixed_targets_empty_capture_does_not_claim_no_crash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            row = corpus.capture(root, root / 'output', 'failure')
            self.assertTrue(row['capture_passed'])
            self.assertEqual(row['native_go_fuzz_outcome'], 'failure')
            self.assertEqual(row['files'], [])
            self.assertIn('not proof', row['limitation'])

    def test_all_three_synthetic_targets_preserve_exact_replay_bytes_and_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for target in corpus.TARGETS:
                self.source(root, target)
            self.source(root, name='b' * 16, raw=RAW)
            foreign = root / 'private-profile'
            foreign.write_text('must not read')
            row = corpus.capture(root, root / 'output', 'failure')
            self.assertTrue(row['capture_passed'])
            self.assertEqual(len(row['files']), 4)
            for item in row['files']:
                path = root / 'output/corpus' / item['target'] / item['name']
                self.assertEqual(path.read_bytes(), RAW)
                self.assertEqual(item['sha256'], hashlib.sha256(RAW).hexdigest())

    def test_leaf_symlink_rejected_before_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / ('a' * 16)
            path.write_bytes(RAW)
            with patch.object(Path, 'is_symlink', return_value=True), self.assertRaisesRegex(ValueError, 'UNSAFE_CORPUS_PATH'):
                corpus.read_regular(path)

    def test_ancestor_symlink_projection_rejected_before_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / ('a' * 16)
            path.write_bytes(RAW)
            with patch.object(Path, 'resolve', return_value=path.parent / 'foreign'), self.assertRaisesRegex(ValueError, 'UNSAFE_CORPUS_PATH'):
                corpus.read_regular(path)

    def test_directory_and_invalid_names_are_not_copied_or_echoed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / next(iter(corpus.TARGETS.values()))
            source.mkdir(parents=True)
            (source / ('a' * 16)).mkdir()
            (source / 'private-untrusted-name').write_bytes(RAW)
            row = corpus.capture(root, root / 'output', 'failure')
            self.assertFalse(row['capture_passed'])
            self.assertEqual(row['files'], [])
            self.assertNotIn('private-untrusted-name', json.dumps(row))
            self.assertEqual({x['code'] for x in row['rejections']}, {'CORPUS_NOT_SINGLE_REGULAR_FILE', 'CORPUS_NAME_INVALID'})

    def test_file_count_is_bounded_and_overflow_is_a_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for index in range(corpus.MAX_FILES_PER_TARGET + 3):
                self.source(root, name=f'{index:016x}')
            row = corpus.capture(root, root / 'output', 'failure')
            self.assertFalse(row['capture_passed'])
            self.assertEqual(len(row['files']), corpus.MAX_FILES_PER_TARGET)
            self.assertIn('CORPUS_FILE_COUNT_LIMIT', [x['code'] for x in row['rejections']])

    def test_single_file_total_and_format_limits_are_enforced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self.source(root, name='a' * 16, raw=b'x' * (corpus.MAX_FILE_BYTES + 1))
            self.source(root, name='b' * 16, raw=b'not Go corpus')
            self.source(root, name='c' * 16)
            self.source(root, name='d' * 16)
            with patch.object(corpus, 'MAX_TOTAL_BYTES', len(RAW)):
                row = corpus.capture(root, root / 'output', 'failure')
            self.assertFalse(row['capture_passed'])
            self.assertEqual(row['captured_total_bytes'], len(RAW))
            self.assertEqual({x['code'] for x in row['rejections']},
                             {'CORPUS_FILE_BYTE_LIMIT', 'CORPUS_FORMAT_INVALID', 'CORPUS_TOTAL_BYTE_LIMIT'})

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            output = root / 'output'
            output.mkdir()
            retained = output / 'manifest.json'
            retained.write_text('original failed evidence')
            with self.assertRaises(ValueError):
                corpus.capture(root, output, 'success')
            self.assertEqual(retained.read_text(), 'original failed evidence')

    def test_unexpected_reader_error_never_echoes_private_body(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self.source(root)
            with patch.object(corpus, 'read_regular', side_effect=ValueError('synthetic-private-path-or-body')):
                row = corpus.capture(root, root / 'output', 'failure')
            self.assertFalse(row['capture_passed'])
            self.assertEqual(row['rejections'][0]['code'], 'CORPUS_READ_FAILED')
            self.assertNotIn('synthetic-private-path-or-body', json.dumps(row))

    def test_hardlink_is_rejected_without_copying(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            original = self.source(root)
            os.link(original, original.with_name('b' * 16))
            row = corpus.capture(root, root / 'output', 'failure')
            self.assertFalse(row['capture_passed'])
            self.assertEqual(row['files'], [])
            self.assertEqual({x['code'] for x in row['rejections']}, {'CORPUS_NOT_SINGLE_REGULAR_FILE'})

    def test_mutation_during_read_is_not_published_as_consistent_corpus(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = self.source(root)
            original_read = os.read
            mutated = False
            def changed(descriptor, length):
                nonlocal mutated
                if not mutated:
                    source.write_bytes(RAW + b'// explicit synthetic concurrent change\n')
                    mutated = True
                return original_read(descriptor, length)
            with patch.object(corpus.os, 'read', side_effect=changed):
                row = corpus.capture(root, root / 'output', 'failure')
            self.assertFalse(row['capture_passed'])
            self.assertEqual(row['files'], [])
            self.assertEqual(row['rejections'][0]['code'], 'CORPUS_CHANGED_DURING_READ')


if __name__ == '__main__':
    unittest.main()
