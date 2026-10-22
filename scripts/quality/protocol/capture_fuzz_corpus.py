"""保留三个固定Go目标的合成失败语料；不读取profile、模型输出或任意业务目录。"""
from __future__ import annotations

import argparse
import hashlib
from itertools import islice
import json
import os
from pathlib import Path
import re
import stat

ROOT = Path(__file__).resolve().parents[3]
TARGETS = {
    'FuzzTaskNormalizeAndValidate': 'api/internal/domain/testdata/fuzz/FuzzTaskNormalizeAndValidate',
    'FuzzDiagnosisObservationObject': 'api/internal/service/testdata/fuzz/FuzzDiagnosisObservationObject',
    'FuzzDiagnosisExactScalar': 'api/internal/service/testdata/fuzz/FuzzDiagnosisExactScalar',
}
MAX_FILES_PER_TARGET = 8
MAX_FILE_BYTES = 384 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024
NAME = re.compile(r'(?:[a-f0-9]{16}|[a-f0-9]{64})')
READ_CODES = {'UNSAFE_CORPUS_PATH', 'CORPUS_NOT_SINGLE_REGULAR_FILE', 'CORPUS_FILE_BYTE_LIMIT',
              'CORPUS_CHANGED_DURING_READ', 'CORPUS_FORMAT_INVALID', 'CORPUS_TOTAL_BYTE_LIMIT'}


def read_regular(path):
    if path.is_symlink() or path.resolve() != path.absolute():
        raise ValueError('UNSAFE_CORPUS_PATH')
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError('CORPUS_NOT_SINGLE_REGULAR_FILE')
    if before.st_size > MAX_FILE_BYTES:
        raise ValueError('CORPUS_FILE_BYTE_LIMIT')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) |
                         getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or (
                opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
                before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
            raise ValueError('CORPUS_CHANGED_DURING_READ')
        pieces, count = [], 0
        while True:
            piece = os.read(descriptor, min(65536, MAX_FILE_BYTES + 1 - count))
            if not piece:
                break
            pieces.append(piece)
            count += len(piece)
            if count > MAX_FILE_BYTES:
                raise ValueError('CORPUS_FILE_BYTE_LIMIT')
        after = os.fstat(descriptor)
        current = path.lstat()
        # Windows路径stat与fd stat的ctime含义可能不同；变化检测在同一fd的前后快照间比较ctime。
        if path.resolve() != path.absolute() or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or (
                current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != (
                opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns):
            raise ValueError('CORPUS_CHANGED_DURING_READ')
    finally:
        os.close(descriptor)
    raw = b''.join(pieces)
    if not raw.startswith(b'go test fuzz v1\n'):
        raise ValueError('CORPUS_FORMAT_INVALID')
    return raw


def capture(root, output, native_outcome):
    if native_outcome not in ('success', 'failure', 'cancelled', 'skipped', 'unknown'):
        raise ValueError('INVALID_NATIVE_FUZZ_OUTCOME')
    if not output.is_absolute() or output.exists() or output.resolve() != output:
        raise ValueError('NEW_ABSOLUTE_CORPUS_OUTPUT_REQUIRED')
    output.mkdir(parents=True, mode=0o700)
    rows, rejected, total = [], [], 0
    for target, relative in TARGETS.items():
        source = root / relative
        try:
            if source.resolve() != source.absolute() or source.is_symlink():
                rejected.append({'target': target, 'code': 'UNSAFE_TARGET_PATH'})
                continue
            if not source.exists():
                continue
            if not source.is_dir():
                rejected.append({'target': target, 'code': 'TARGET_NOT_DIRECTORY'})
                continue
            # 只枚举硬限+1，不用rglob或先全量排序，避免无限目录占用内存。
            entries = list(islice(source.iterdir(), MAX_FILES_PER_TARGET + 1))
        except (OSError, RuntimeError):
            rejected.append({'target': target, 'code': 'TARGET_DIRECTORY_UNAVAILABLE'})
            continue
        if len(entries) > MAX_FILES_PER_TARGET:
            rejected.append({'target': target, 'code': 'CORPUS_FILE_COUNT_LIMIT'})
        for entry in sorted(entries[:MAX_FILES_PER_TARGET], key=lambda value: value.name):
            if not NAME.fullmatch(entry.name):
                rejected.append({'target': target, 'code': 'CORPUS_NAME_INVALID'})
                continue
            try:
                raw = read_regular(entry)
                if total + len(raw) > MAX_TOTAL_BYTES:
                    raise ValueError('CORPUS_TOTAL_BYTE_LIMIT')
            except (ValueError, OSError, RuntimeError) as error:
                code = str(error) if isinstance(error, ValueError) and str(error) in READ_CODES else 'CORPUS_READ_FAILED'
                rejected.append({'target': target, 'code': code})
                continue
            destination = output / 'corpus' / target / entry.name
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with destination.open('xb') as stream:
                stream.write(raw)
            destination.chmod(0o600)
            rows.append({'target': target, 'name': entry.name, 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
            total += len(raw)
    report = {'schema_version': 1, 'capture_passed': not rejected, 'native_go_fuzz_outcome': native_outcome,
              'provenance': 'explicitly synthetic Go-generated testdata/fuzz counterexamples from three fixed protocol targets',
              'files': rows, 'rejections': rejected, 'captured_total_bytes': total,
              'limits': {'files_per_target': MAX_FILES_PER_TARGET, 'file_bytes': MAX_FILE_BYTES, 'total_bytes': MAX_TOTAL_BYTES},
              'limitation': 'Empty corpus is not proof of no crash; capture does not change or retry the original Go exit code.'}
    with (output / 'manifest.json').open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--native-outcome', choices=('success', 'failure', 'cancelled', 'skipped', 'unknown'), default='unknown')
    args = parser.parse_args()
    try:
        report = capture(ROOT, args.output, args.native_outcome)
    except Exception:
        print(json.dumps({'capture_passed': False, 'error_code': 'FUZZ_CORPUS_CAPTURE_FAILED'}))
        return 1
    print(json.dumps({'capture_passed': report['capture_passed'], 'files': len(report['files']),
                      'bytes': report['captured_total_bytes'], 'rejections': len(report['rejections'])}))
    return 0 if report['capture_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
