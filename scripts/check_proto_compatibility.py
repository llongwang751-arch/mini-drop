"""以已部署 V1 协议为基线，阻止无意的 wire/API 不兼容修改。"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def definitions(text, keyword):
    text = re.sub(r'/\*.*?\*/|//[^\n]*', '', text, flags=re.S)
    result = {}
    for match in re.finditer(r'\b'+keyword+r'\s+(\w+)\s*\{', text):
        depth, pos = 1, match.end()
        while depth and pos < len(text):
            depth += (text[pos] == '{') - (text[pos] == '}'); pos += 1
        if depth: raise ValueError('Unbalanced protocol definition')
        result[match[1]] = text[match.end():pos-1]
    return result


def compare(old, new):
    field = re.compile(r'\b(?:(repeated|optional)\s+)?([\w.]+)\s+(\w+)\s*=\s*(\d+)\s*(?:\[[^\]]*\])?\s*;')
    before, after = definitions(old,'message'), definitions(new,'message')
    for name, body in before.items():
        assert name in after, f'Message removed: {name}'
        fields = {m[4]:(m[1],m[2],m[3]) for m in field.finditer(after[name])}
        for m in field.finditer(body):
            assert fields.get(m[4]) == (m[1],m[2],m[3]), f'Wire field changed: {name} #{m[4]}'
    before, after = definitions(old,'service'), definitions(new,'service')
    rpc = re.compile(r'\brpc\s+(\w+)\s*\(\s*(stream\s+)?([\w.]+)\s*\)\s*returns\s*\(\s*(stream\s+)?([\w.]+)\s*\)')
    for name, body in before.items():
        assert name in after, f'Service removed: {name}'
        signatures = {m[1]:m.groups()[1:] for m in rpc.finditer(after[name])}
        for m in rpc.finditer(body): assert signatures.get(m[1]) == m.groups()[1:], f'RPC changed: {name}.{m[1]}'


if __name__ == '__main__':
    compare((ROOT/'contracts/compat/v1.proto').read_text(encoding='utf-8-sig'),(ROOT/'contracts/drop.proto').read_text(encoding='utf8'))
    # 通过字段重编号/类型复用的负对照，证明检查器能识别不兼容，而非只完成语法解析。
    try: compare('message A {string name=1;}','message A {int32 count=1;}')
    except AssertionError: pass
    else: raise AssertionError('Compatibility checker failed its negative control')
    print('Protocol V1 fields and RPC signatures preserved; negative control rejected')
