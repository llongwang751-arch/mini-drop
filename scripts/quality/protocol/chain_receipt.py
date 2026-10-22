"""严格提取一次真实采样门禁的安全收据；绝不回显原始 stdout 或解析异常正文。"""

import json
from pathlib import Path
import re
import sys

PREFIX = b'DROP_QUALITY_CHAIN_RECEIPT='
MAX_STDOUT_BYTES = 256 * 1024
FIXED_FIELDS = {
    'provenance': 'fresh real C++ PROC_CPU Runner capture; synthetic orchestration rows',
    'scope': 'Runner -> MinIO -> Analyzer child -> PostgreSQL atomic result -> result download',
    'excluded_scope': ['Go HTTP task creation', 'Agent gRPC delivery', 'browser', 'paid model'],
    'database': 'fresh owned PostgreSQL schema, all authoritative migrations',
    'storage': 'fresh owned MinIO bucket',
}
EXPECTED_FIELDS = set(FIXED_FIELDS) | {
    'schema_version', 'passed', 'raw_sha256', 'raw_size', 'total_cpu_ticks',
    'producer_binary_sha256', 'result_artifacts_verified', 'analysis_success_events', 'production_mutations',
}


class ReceiptError(ValueError):
    """只携带本模块定义的固定错误码，不携带日志、字段值或 JSON 解码器信息。"""


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReceiptError('CHAIN_RECEIPT_DUPLICATE_KEY')
        result[key] = value
    return result


def reject_constant(_):
    raise ReceiptError('CHAIN_RECEIPT_INVALID_JSON')


def parse_stdout(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_STDOUT_BYTES:
        raise ReceiptError('CHAIN_STDOUT_BOUND_EXCEEDED')
    lines = [line for line in raw.split(b'\n') if line]
    if len(lines) != 1 or not lines[0].startswith(PREFIX):
        raise ReceiptError('CHAIN_RECEIPT_MISSING_DUPLICATED_OR_POLLUTED')
    try:
        receipt = json.loads(lines[0][len(PREFIX):].decode('utf-8'), object_pairs_hook=unique_object, parse_constant=reject_constant)
    except ReceiptError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise ReceiptError('CHAIN_RECEIPT_INVALID_JSON') from None
    if not isinstance(receipt, dict) or set(receipt) != EXPECTED_FIELDS:
        raise ReceiptError('CHAIN_RECEIPT_INVALID_FIELDS')
    if type(receipt['schema_version']) is not int or receipt['schema_version'] != 1 or receipt['passed'] is not True or receipt['production_mutations'] is not False:
        raise ReceiptError('CHAIN_RECEIPT_INVALID_STATUS')
    if any(receipt[key] != expected for key, expected in FIXED_FIELDS.items()):
        raise ReceiptError('CHAIN_RECEIPT_INVALID_BOUNDARY')
    for key, lower, upper in (('raw_size', 1, 1024 * 1024), ('total_cpu_ticks', 1, 2**53 - 1),
                              ('result_artifacts_verified', 3, 16), ('analysis_success_events', 1, 1)):
        if type(receipt[key]) is not int or not lower <= receipt[key] <= upper:
            raise ReceiptError('CHAIN_RECEIPT_INVALID_COUNT')
    binaries = receipt['producer_binary_sha256']
    if not isinstance(binaries, dict) or set(binaries) != {'drop_runner_probe', 'drop_workload'}:
        raise ReceiptError('CHAIN_RECEIPT_INVALID_BINARY_DIGESTS')
    for digest in [receipt['raw_sha256'], *binaries.values()]:
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ReceiptError('CHAIN_RECEIPT_INVALID_DIGEST')
    return receipt


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 2:
        print('CHAIN_RECEIPT_ARGUMENTS_REQUIRED', file=sys.stderr)
        return 2
    source, destination = map(Path, arguments)
    try:
        if destination.exists():
            raise ReceiptError('CHAIN_RECEIPT_OUTPUT_EXISTS')
        with source.open('rb') as stream:
            receipt = parse_stdout(stream.read(MAX_STDOUT_BYTES + 1))
        destination.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    except ReceiptError as error:
        print(str(error), file=sys.stderr)
        return 1
    except OSError:
        print('CHAIN_RECEIPT_IO_FAILED', file=sys.stderr)
        return 1
    print(json.dumps(receipt))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
