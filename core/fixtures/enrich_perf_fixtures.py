#!/usr/bin/env python3
"""在资源上限内统计自采集原生夹具的真实记录数，补充来源元数据。

此脚本记录夹具来源，不为导入或不可信 RAW 提供绕过解析的捷径。
Analyzer 在报告任何丢失信息前，仍须独立检查原生文件。
"""
import hashlib
import json
import pathlib
import struct

root = pathlib.Path(__file__).resolve().parents[2] / 'core/evidence/perf-collected'
script = json.loads((root/'perf_script/manifest.json').read_text())
path = root/'perf_data/manifest.json'
manifest = json.loads(path.read_text())
raw = (path.parent/manifest['name']).read_bytes()
if len(raw) != manifest['size'] or hashlib.sha256(raw).hexdigest() != manifest['sha256']:
    raise RuntimeError('Self-collected native fixture differs from its manifest')
if len(raw) < 56 or len(raw) > 512*1024*1024 or raw[:8] not in (b'PERFILE2',b'2ELIFREP'):
    raise RuntimeError('Native fixture header is invalid')
order = '<' if raw[:8] == b'PERFILE2' else '>'
offset, length = struct.unpack_from(order+'QQ',raw,40)
if offset < 56 or offset > len(raw) or length > len(raw)-offset:
    raise RuntimeError('Native fixture data section is outside its file')
at, samples, records = offset, 0, 0
while at < offset+length:
    if at+8 > offset+length or records >= 2000000:
        raise RuntimeError('Native fixture record header is truncated')
    kind, _, size = struct.unpack_from(order+'IHH',raw,at)
    if size < 8 or size > offset+length-at or kind == 81:
        raise RuntimeError('Native fixture cannot prove a complete uncompressed record count')
    samples += kind == 9
    records += 1
    at += size
pid = script['metadata'].get('target_pid') or script['metadata']['perf']['filters']['target_pid']
manifest['metadata'] = {'target_pid':pid,'perf':{'clock':'CLOCK_MONOTONIC','sample_count':samples,'filters':{'target_pid':pid}},'fixture_metadata_evidence':'recorder PID and --clockid mono argv; independently counted complete uncompressed native PERF_RECORD_SAMPLE records; no loss claim'}
path.write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({'format':'perf_data','size':manifest['size'],'actual_sample_records':samples,'target_pid':pid,'clock':'CLOCK_MONOTONIC','loss_not_inferred':True}))
