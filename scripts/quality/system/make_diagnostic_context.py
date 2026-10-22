"""仅为可销毁QA构建生成注册状态插桩；不改仓库C++或生产运行目录。"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
BEFORE = b'      log("drop-agent", "Registration unavailable", "", "REGISTER_FAILED");'
AFTER = b'''      LogContext registration_context;
      registration_context.stage =
          "register_grpc_" + std::to_string(static_cast<int>(status.error_code()));
      log("drop-agent", "Registration unavailable", "", "REGISTER_FAILED",
          registration_context);'''
DOCKERFILE = '''ARG CORE_BUILD_IMAGE
ARG CORE_RUNTIME_IMAGE
FROM ${CORE_BUILD_IMAGE} AS diagnostic-build
COPY agent.diagnostic.cpp /src/core/src/agent.cpp
RUN cmake --build /src/build --parallel 1 --target drop_agent
FROM ${CORE_RUNTIME_IMAGE} AS diagnostic-runtime
COPY --from=diagnostic-build /src/build/drop_agent /usr/local/bin/drop_agent
'''


def patched(source):
    if source.count(BEFORE) != 1:
        raise ValueError('QA_REGISTRATION_PATCH_SOURCE_MISMATCH')
    return source.replace(BEFORE, AFTER, 1)


def image_id(reference):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9./:_@-]{1,199}', reference):
        raise ValueError('QA_DIAGNOSTIC_IMAGE_REFERENCE_INVALID')
    result = subprocess.run(['docker', 'image', 'inspect', reference, '--format', '{{.Id}}'],
                            capture_output=True, timeout=20)
    identifier = result.stdout.decode().strip()
    if result.returncode or not re.fullmatch(r'sha256:[a-f0-9]{64}', identifier):
        raise ValueError('QA_DIAGNOSTIC_PINNED_IMAGE_REQUIRED')
    return identifier


def prepare(output, build_image, runtime_image):
    if not output.is_absolute() or output.exists() or output.resolve() != output:
        raise ValueError('QA_DIAGNOSTIC_NEW_ABSOLUTE_OUTPUT_REQUIRED')
    original = (ROOT / 'core/src/agent.cpp').read_bytes()
    instrumented = patched(original)
    patch = ''.join(difflib.unified_diff(original.decode().splitlines(True), instrumented.decode().splitlines(True),
                                       fromfile='a/core/src/agent.cpp', tofile='b/core/src/agent.cpp')).encode()
    ids = {'build': image_id(build_image), 'runtime': image_id(runtime_image)}
    output.mkdir(parents=True, mode=0o700)
    files = {'agent.diagnostic.cpp': instrumented, 'registration-status.patch': patch, 'Dockerfile': DOCKERFILE.encode()}
    for name, body in files.items():
        (output / name).write_bytes(body)
    receipt = {'schema_version': 1, 'diagnostic_instrumentation': True, 'acceptance_eligible': False,
               'production_or_repository_cpp_modified': False, 'source': 'core/src/agent.cpp',
               'original_source_sha256': hashlib.sha256(original).hexdigest(),
               'instrumented_source_sha256': hashlib.sha256(instrumented).hexdigest(),
               'patch_sha256': hashlib.sha256(patch).hexdigest(), 'base_image_ids': ids,
               'scope': 'single registration log gets fixed numeric gRPC status in stage; no error message or details exported',
               'files': {name: hashlib.sha256(body).hexdigest() for name, body in files.items()}}
    (output / 'manifest.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-image', required=True)
    parser.add_argument('--runtime-image', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        row = prepare(args.output, args.build_image, args.runtime_image)
    except Exception:
        print(json.dumps({'passed': False, 'error_code': 'QA_DIAGNOSTIC_CONTEXT_REJECTED'}))
        return 1
    print(json.dumps({'passed': True, 'acceptance_eligible': False, 'original_source_sha256': row['original_source_sha256'],
                      'instrumented_source_sha256': row['instrumented_source_sha256'], 'patch_sha256': row['patch_sha256']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
