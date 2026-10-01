from pathlib import Path
import json
import os

root = Path.cwd()
stage = Path(__file__).resolve().parent
release = stage / 'release-r8'
manifest = json.loads((release / 'manifest.json').read_text())
ci = json.loads((release / 'ci-run.json').read_text())
browser = json.loads((stage / 'browser-r10/result.json').read_text(encoding='utf-8'))
assert ci['conclusion'] == 'success' and ci['head_sha'] == manifest['git_head']
assert browser['passed'] is True and browser['errors'] == []
assert len(browser['paths']) == 3 and sum(p['localized'] is True for p in browser['paths']) == 2
assert sum(p.get('refutation_displayed') is True for p in browser['paths']) == 1
head = manifest['git_head']
tag = manifest['release_tag']
url = ci['html_url']

def save(path, content):
    tmp = stage / 'documentation-work.tmp'
    tmp.write_text(content, encoding='utf-8')
    os.replace(tmp, path)

current = f'''## 2026-10-01 性能路径、反证与案例链接最终交付

最新Web发布`{tag}` / `{head}`；[精确CI{ci['id']}]({url})成功13/13，Python1358通过/10登记跳过、Web294通过、真实PostgreSQL8通过零跳过，Chroma与Go race/真实镜像/连续I/O专项通过。仅Web更换，其余12容器保持；Worker/Analyzer/Go demo仍为20261001T094553Z / c59aac566cedef6a239eb1d640ee8fb796524ae3。Worker/Analyzer各193文件、Web56文件SHA复核，13容器健康，API3依赖健康，21故障inactive；Office PID1650962、NRestarts=0，API与Office源码及数据保持。

新独立三案例：CPU具体热函数与HTTP耗时路径已定位，路径定位2/3；I/O同一窗口684次操作平均0.081ms，3/3数值判据已检查，REFUTED反驳慢操作假设。37份原始下载SHA一致，取证/撤销恢复/清理/收束3/3，严格因果0/3；旧21类因果0/21与原证据保留。停止前I/O成功10232次、失败0，8MiB轮转修复采样提前停止，默认64MiB tmpfs不能称真实磁盘瓶颈。

真实Chrome验证三个案例链接、CPU/HTTP路径、I/O反证与数值、健康检查正常（仅已检查范围）、默认21项/工程4项/9份下载SHA、1440/1024/768/375宽度，无JS/HTTP异常。反证展示旧代码5项失败，案例链接状态重放/列表失败重试旧代码2项失败；修复后完整Web294项通过。保留有效反证、ACCEPT_COUNTER准入和最新合格窗口；React函数状态更新保持纯函数，不再提前清空请求链接。

剩余重点是可归属的真实磁盘等待、HTTP与TCP传输拆分，以及固定输入/负载的原因干预。没有完成全部21类因果验收，不用本轮路径与反证改写旧分数。一小时按用户已接受的1/120窗超限不重跑，原严格FAILED保持。以下同日期段落为此前过程快照，以本段为当前状态。全部失败、成功、原始下载、CI与发布回执见[本轮交付](../reports/architecture/performance-localization-20261001.md)及[归档SHA清单](../reports/quality/performance-localization-20261001/manifest.json)。

'''
for name in ('PROJECT_CONTEXT.md', 'RESTART_HANDOFF.md', 'FAULT_PLAZA_ACCEPTANCE.md'):
    path = root / 'docs' / name
    text = path.read_text(encoding='utf-8')
    first, rest = text.split('\n', 1)
    save(path, first + '\n\n' + current + rest.lstrip('\n'))

path = root / 'docs/PERFORMANCE_DIAGNOSIS.md'
text = path.read_text(encoding='utf-8')
text = text.replace('链接修正待精确CI与Web单独发布。', f'链接修正已通过精确CI{ci["id"]}成功13/13、Web294通过，单独发布{tag} / {head}；真实浏览器三个案例、反证与正常检查均通过。', 1)
text = text.replace('待精确CI、Web发布与默认缓存浏览器最终复核。', f'精确CI{ci["id"]}成功13/13、Web294通过，单独发布{tag} / {head}；默认缓存浏览器最终复核已通过。', 1)
save(path, text)

path = root / 'docs/AI_DIAGNOSIS.md'
text = path.read_text(encoding='utf-8')
needle = '页面新增“性能路径已定位，根因仍待确认”'
text = text.replace(needle, f'最新Web为{tag} / {head}，精确CI{ci["id"]}成功13/13、Web294通过。完整REFUTED保留为“已测量，异常假设被反驳”，展示实际测量与3/3判据；ACCEPT_COUNTER须通过身份、Analyzer与窗口准入，其他资源异常仍优先，取消/失败不能产生正常结果。案例链接支持React状态重放和首次列表失败后的重试，实浏览器复核通过。\n\n'+needle, 1)
save(path, text)

path = root / 'reports/architecture/performance-localization-20261001.md'
text = path.read_text(encoding='utf-8')
text = text.replace('最终发布 `20261001T094553Z`', '核心后端发布 `20261001T094553Z`', 1)
text = text.replace('正常展示范围将在实浏览器复核后记录。', '实际15样本约14秒、CPU0.286%、RSS211.434MiB且增量0；真实浏览器显示已检查范围正常，不外推全部业务。', 1)
extra = f'''## 最终前端发布与真实浏览器

最新Web单独发布`{tag}` / `{head}`；[精确CI{ci['id']}]({url})成功13/13，Web294通过，Python1358通过/10登记跳过，真实PG8通过零跳过。后端保持上述c59源码与进程。Web56文件、Worker/Analyzer各193文件SHA一致，另外12容器未替换，13容器及API3依赖健康，21注入inactive，旧0/21保持。

实浏览器在核心发布后还发现两类产品缺陷，保留首次失败和同输入负向回归：完整I/O反证被后续主机证据不足报告盖住，初筛遗漏ACCEPT_COUNTER且无效窗口覆盖有效测量（旧源码5失败）；案例链接在React重放函数状态更新时丢失，首次列表失败也提前清空请求参数（旧源码2失败）。分别修复报告选择、可信反证与窗口准入，以及纯函数状态更新/成功列表后处理链接。完整测量反证不会升级为因果根因，其他已发现资源异常仍优先。

默认浏览器连续导航还复现SSE连接滞留：前序事件流未出现CDP结束通知，新列表请求超时；缓存禁用对照与页面生命周期修复均恢复导航，支持连接生命周期导致排队的判断（CDP缺少结束通知不能作为实际连接数）；只关闭浏览器前进后退缓存的同输入对照通过（不能当作默认浏览器验收）。新增pagehide关闭连接与计时器、pageshow按原游标恢复，暂停时禁止凭据事件和旧连接错误重连；旧源码生命周期回归2失败、修复后13项通过。最终默认缓存浏览器重新完成全部检查。

修复前后JUnit、真实会话冻结输入、浏览器失败截图和最终成功截图均归档。前一轮完整Web默认并发在本机发生内存不足/导入错误，首次缺依赖运行也单独保留为环境失败；它们不能冒充业务负向复现。限制本机测试工作进程为2后完整通过，CI覆盖率与判据不改，最终294项通过。

真实Chrome最终检查CPU/HTTP定位与I/O反证（0.081ms、684次、3/3已检查）、三个案例链接、健康检查范围、默认21项、工程4项及9份原始下载SHA；后退/前进也恢复到正确案例，四种宽度无横向溢出，无JS/HTTP错误。补充连接数断言曾误把CDP未发结束通知当作实际存活连接，原失败日志和截图保留为INSTRUMENTATION_ASSERTION失败；最终验收使用真实列表请求、案例结果与导航断言，不把该计数当活跃连接数。浏览器只是读取上述不可变案例，没有重新注入或拼接成绩。

面试可依次打开[CPU热函数](https://120.24.187.205/ai-diagnosis?case=drop_insight_v2%3Ainsight_b043658db8444bf3a34dfedc69a4fa9e)、[HTTP耗时路径](https://120.24.187.205/ai-diagnosis?case=drop_insight_v2%3Ainsight_c0e59fdba9ce4147bf9729b0535a5c43)、[I/O假设被反驳](https://120.24.187.205/ai-diagnosis?case=drop_insight_v2%3Ainsight_3093704de5e4496ebc10b58379014b18)，需要现有访问凭据与可信TLS链。说明“有界观测→具体路径→原因干预”的差别，结合旧代码失败/修复回归、真实PG竞争与持续采样解释测试开发能力。

原始材料与逐文件SHA见[完整归档](../quality/performance-localization-20261001/manifest.json)，[最终浏览器](../quality/performance-localization-20261001/deployed/browser-r10/result.json)，[新批次评分](../quality/performance-localization-20261001/deployed/final-grade/localization-acceptance.json)，[最终运行复核](../quality/performance-localization-20261001/deployed/final-runtime-verification.json)。源码与证据已提交；工作区其他线程的中文源码注释和学习指南正文增补不纳入本轮提交。

'''
text = text.replace('## 仍未完成的范围\n', extra + '## 仍未完成的范围\n', 1)
save(path, text)
print(json.dumps({'documentation_source': head, 'web_release': tag, 'ci': ci['id']}))
