from pathlib import Path
import difflib
import os
import subprocess

root = Path.cwd()
stage = Path(__file__).resolve().parent
for name in ('useSSE.js', 'useSSE.test.jsx'):
    path = Path('web/src/hooks') / name
    base = subprocess.check_output(['git', 'show', 'HEAD:' + path.as_posix()]).decode('utf-8')
    before = (stage / ('lifecycle-before-' + name)).read_text(encoding='utf-8')
    current = path.read_text(encoding='utf-8')
    a = base.splitlines(keepends=True)
    b = before.splitlines(keepends=True)
    user = []
    for kind, _, __, start, end in difflib.SequenceMatcher(a=a, b=b).get_opcodes():
        if kind == 'equal': continue
        assert kind == 'insert'
        assert all(not line.strip() or line.lstrip().startswith('//') for line in b[start:end])
        user.extend(b[start:end])
    for line in user:
        assert line in current
        current = current.replace(line, '', 1)
    blob = subprocess.check_output(['git', 'hash-object', '-w', '--stdin'], input=current.encode()).decode().strip()
    subprocess.run(['git', 'update-index', '--cacheinfo', '100644,' + blob + ',' + path.as_posix()], check=True)
    print(name, 'user comment lines preserved:', len(user))

paragraph = '''
真实浏览器连续导航后列表超时：默认浏览器记录前序页面6条未结束SSE连接，新列表请求无响应；关闭前进后退缓存的同输入对照完成全部检查。React组件卸载关闭连接不足以覆盖缓存页面生命周期。新增pagehide关闭连接/清除退避计时器、pageshow按原游标恢复，暂停时不因凭据事件或旧连接错误重连；不改变普通后台标签页行为。旧源码13项生命周期测试中2失败，修复候选完整Web294项通过；待精确CI、Web发布与默认缓存浏览器最终复核。对照实验不冒充默认浏览器验收通过。
'''
for name in ('PROJECT_CONTEXT.md', 'PERFORMANCE_DIAGNOSIS.md'):
    path = root / 'docs' / name
    text = path.read_text(encoding='utf-8')
    marker = '\n## 2026-10-01 性能诊断判定与入口已部署' if name == 'PROJECT_CONTEXT.md' else '\n`observation_verifier.py` 输出'
    assert marker in text
    text = text.replace(marker, '\n' + paragraph + marker, 1)
    tmp = stage / 'lifecycle-doc-work.tmp'
    tmp.write_text(text, encoding='utf-8')
    os.replace(tmp, path)
subprocess.run(['git', 'add', '--', 'docs/PROJECT_CONTEXT.md', 'docs/PERFORMANCE_DIAGNOSIS.md'], check=True)
