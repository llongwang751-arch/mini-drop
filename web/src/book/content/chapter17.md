## 这一页先接哪套后端

本章要把前面写出的结果交到浏览器里。前置条件是已经按第 11 章启动独立 mini-drop，知道任务的字符串 ID、attempt、状态与结果是什么。实际页面是 `web/src/book/LabWorkbench.tsx`，路径为 `/book/workbench`；课程机制实验在 `tutorials/labs/analysis.py / SSEDecoder`，生产阅读入口是 TaskDetail、event-stream 和 profile-loader。

先分清两套接口。mini 的 Go API 监听本机 4330，提供任务 REST 与任务内 events 数组，不提供 SSE，也没有正式 Drop 的登录、数据库与远程采集。浏览器通过 `/mini-api` 本地代理访问它。生产任务详情则通过 `/api/v1/tasks/...` 读取，并订阅真实事件流。两套系统有共同概念，不能给 mini 编造一个不存在的 stream 地址。

工作台初次打开只显示“尚未连接”，不会自动请求教学 API。这样你可以只读书，不必先启动服务，也不会把普通章节浏览变成一串连接错误。点击“连接本地服务”才读取列表，连接成功后才能创建受控任务。这个明确的起点也让浏览器实验容易验证：点击前没有请求，点击后才有。

本章最终交付不是一张静态成功截图，而是一个能解释请求、状态、来源、单位和生命周期的页面。你会完成一次真实本机受控自采集的展示，同时用合成 SSE 字节练习分块恢复。两个实验各自有输入和结论，不能把合成事件通过算作生产实时链路验收。

## 先把数据流写成五步

打开 LabWorkbench，从用户动作往下读：按钮调用 refresh，refresh 请求 `/tasks`，request 给它加 `/mini-api` 前缀，代理转到本机 Go，返回 `{data:[task,...]}` 后才更新 tasks。创建任务则 POST `/tasks`，正文为 iterations，并带一个幂等键；服务端安排采集和分析，页面随后重新读取列表。

mini task 至少有 id、attempt、status、iterations 和 events。SUCCESS 时才有 result，其中 summary 保留 `source=controlled_self、unit=cpu_ticks、total_ticks、clock_ticks_per_second`，还有 input_sha256 与 flamegraph。字段直接取自 Python 分析结果，不应在 React 里根据等待时间生成一个假的采样数。

active 表示列表中还有 QUEUED 或 RUNNING。已连接且存在活动任务时，页面每八百毫秒刷新；上一轮读取尚未结束时不再启动下一次，避免网络慢时堆积请求。终态后不需要靠这个计时器假装推进任务，服务端已经决定状态，页面只是读取它。

画出这个数据流后，再问每一步谁拥有事实。Go 拥有 task 状态，Python 生成结果结构，C++ 给出本进程计数，React 保存最近读到的快照和当前选中项。前端缓存失效或组件重挂载不会让后台重新采集；只有明确创建或重试请求才产生新的执行意图。

## 写一个只负责解释结果的 React 组件

先做展示，不要一上来把请求、图形和所有交互塞进一个组件。下面函数可以用于自己的临时前端练习，输入契约与 mini result 对应；它没有网络动作。这里显示“未生成结果”，不自动把缺失结果补成零，否则 RUNNING 与“测到了零 tick”会被混成同一件事。

```tsx
type Result = {
  summary: {
    source: string;
    unit: string;
    total_ticks: number;
    clock_ticks_per_second: number;
    limitations: string[];
  };
  input_sha256: string;
};

export function ResultEvidence({ result }: { result?: Result }) {
  if (!result) return <p>分析完成后才会出现结果。</p>;
  return (
    <section aria-label="采集结果及边界">
      <dl>
        <dt>数据来源</dt>
        <dd>{result.summary.source}</dd>
        <dt>测量单位</dt>
        <dd>{result.summary.unit}</dd>
        <dt>本次 CPU 计数</dt>
        <dd>{result.summary.total_ticks}</dd>
        <dt>每秒计数</dt>
        <dd>{result.summary.clock_ticks_per_second}</dd>
      </dl>
      <p>只测受控进程自身，不包含 Python 函数调用栈。</p>
      <ul>
        {result.summary.limitations.map((line, index) => (
          <li key={index}>{line}</li>
        ))}
      </ul>
      <p>
        输入 SHA-256：<code>{result.input_sha256}</code>
      </p>
    </section>
  );
}
```

先喂一份明确标记的合成 props：total_ticks 为零，应显示零而不是错误；result 为 undefined，显示等待；source 未知时照实显示，不擅自改成 controlled_self。这个练习检查展示逻辑，不会创建任何采集任务。真正连后端以后，props 再来自已完成的真实结果。

把内容拆成小组件的好处，是能独立讨论“这一项应该怎么解释”。请求失败不会逼你重写指标文案，文案调整也不应改任务生命周期。生产页面更复杂，但结果解释、授权读取与观察状态仍值得分开，它们承担的正确性责任不同。

## 请求函数必须收住大小和时间

LabWorkbench 的 request 使用 AbortController，五秒后取消；正文通过流读取累计，超过一 MiB 就停止，而不是无限调用 response.json。请求结束后清理定时器、取消 reader 并释放锁，控制器也从当前集合移除。正常读取、解析失败和网络错误都要经过 finally，不能只在成功时收尾。

为什么 API 已经限制输出，浏览器还要再限制？因为代理配置错误可能返回一个很大的 HTML 页面，错误服务也可能回意外数据。应用边界是各自面对自己接收的字节，不能把全部希望放在“对方应该已经检查过”。正文不是合法 JSON 时，页面提示检查教学 API 与代理，不能继续把它当空任务列表。

这套 request 的 credentials 为 omit，并拒绝重定向。mini 没有正式账号，不应该顺手把生产应用 Cookie 带进教学接口；服务端也只允许数字回环监听和规定的 Origin。这个页面的本地权限模型很有限，它不能拿来替代正式 Drop 对每项任务和制品的资源授权。

TypeScript 接口是编写代码的提示，不是完整运行时校验。现在的教学列表至少检查返回是否数组；正式 profile 还会验证安全整数、树守恒和制品身份。若后续把工作台用于不再受控的数据源，应先完善运行时 schema，而不是用 `as LessonTask[]` 把未知 JSON 宣布成可信对象。

## 双击、响应丢失和主动重试怎么区分

创建表单把“本次提交的 iterations”和幂等键保存在 pendingCreation。只要请求结果还不确定、参数也没改，再次提交沿用原键；一旦拿到成功响应才清掉它。这样服务器可能已经建成任务而响应丢失时，浏览器重发仍代表同一个意图，不会顺手多采一次。

主动重试失败任务则是另一种意图。页面为该 task 保存新的 retry key，调用 `/tasks/{id}/retry`；服务端新增 attempt，旧轮文件保留。这里不能把创建键、任务 ID 和 attempt 当成同一个标识：键对应一次 HTTP 操作的幂等身份，task 对应业务记录，attempt 对应实际执行轮次。

禁用 busy 按钮是体验措施，不能取代服务器幂等。浏览器可以重载，两个窗口也可以同时发请求；仅凭本地 disabled 无法保证重复提交不发生。mini 的 Go 测试验证重放与冲突，生产则把这些约束放进数据库事务和唯一关系，前端只能配合使用。

练习时不要靠反复点按钮猜是否幂等。记录一次提交的参数、键与返回 ID，再用相同键重复，检查是否仍是同一记录；换内容保留键应被拒绝，主动新建才换键。观察的是身份关系，不是“这次机器太快，列表只看见一行”。

## 浏览器实验：从零请求到一份真实结果

先按 `tutorials/mini-drop/README.md` 编译 collector 并启动 Go API，保持服务终端运行。独立参考的编译命令启用 C++17 和 warnings as errors，正式 core 仍遵循自己的 C++20 配置；Go 与 Python 使用当前本机工具。不要把 workdir 指向生产目录，也不要同时开两个控制面共享同一个 journal。详细启动参数以该 README 为准，不能省略后猜程序路径。

另一个终端进入 web 执行开发服务，使用 Vite 的本地监听。工作台代理预先指向 4330，浏览器打开实际打印的前端地址后加 `/book/workbench`。当前 mini Origin 允许课程约定端口，若自己改端口，应同时核对代理与服务端规则，不能为了连接成功把 Origin 检查整段删掉。

```powershell
cd web
npm ci
npm run dev -- --port 4322 --strictPort
```

打开开发者工具 Network，筛选 mini-api。刚打开时应没有自动请求；点击“连接本地服务”，应出现 GET `/mini-api/tasks`，状态文字变成已连接。把迭代次数保持在一千到一千万的整数范围，点击“创建采集任务”，应看见带 Idempotency-Key 的 POST，然后是列表刷新。

任务可能很快跳到 SUCCESS，中间状态不一定肉眼可见；事件记录用于回看经过。成功结果应显示 controlled_self / cpu_ticks、非负 total_ticks、正的 clock_ticks_per_second 和 SHA。具体 tick 数随平台与运行而变，零也可能合法，课程不规定一个假的基准值。树只应有受控进程计账节点，不会突然出现 Python 函数名。

## 离开页面后，谁负责停止什么

组件卸载时，LabWorkbench 把 mounted 置为 false，并取消自己集合中的全部控制器。每个刷新在写 tasks 前检查 mounted，避免卸载以后继续更新。这个清理只停止浏览器请求，不会取消已经提交的 Go 任务；服务端仍继续自己的固定采集与分析流程。

mounted 只回答组件是否仍然存在，不能单独防止同一页面里旧响应覆盖新响应。当前工作台还保存 latestRefresh：每次 refresh 先增加请求序号，收到结果时只有 mounted 且序号仍为最新，才更新列表。旧读取可以结束，但不能拿过期快照盖住较新的观察；reading 则只限制轮询之间的重叠，不替代序号检查。

生产 `event-stream.ts` 就有显式的 current(controller)：订阅未清理、active 仍是当前控制器、signal 未取消，才允许回调。abort 负责尽快释放网络资源，身份检查负责挡住已经排队的异步回调。二者解决不同时间差，不能只留一个。

可以在工作台 Network 中限速，点击刷新后立刻导航回章节，预期该请求被取消或结果不再更新已离开的页面；后台任务不被改成 FAILED。恢复到工作台仍需按当前界面重新读取，不能从旧 DOM 推断服务端状态。真实浏览器观察要记录操作与结果，不能把阅读这段文字当作已经验证。

## 在浏览器里复现一次旧响应覆盖

要理解身份检查，没必要等真实网络碰巧慢下来。可以在开发者工具 Console 运行一个没有网络请求的小实验：两个 Promise 代表 A、B 两次刷新，故意让后发的 B 先完成，再让 A 回来。下面使用请求版本号，只允许最新请求改写 view；所有字符串是合成状态，不读取任何任务。

```javascript
let lessonVersion = 0;
let lessonView = "empty";
async function lessonLoad(promise) {
  const mine = ++lessonVersion;
  const value = await promise;
  if (mine === lessonVersion) lessonView = value;
}
let lessonResolveA;
let lessonResolveB;
const lessonA = new Promise((resolve) => {
  lessonResolveA = resolve;
});
const lessonB = new Promise((resolve) => {
  lessonResolveB = resolve;
});
void lessonLoad(lessonA);
void lessonLoad(lessonB);
lessonResolveB("B-new");
await Promise.resolve();
lessonResolveA("A-old");
await Promise.resolve();
console.log(lessonView);
```

预期打印 B-new。再在临时副本中删除 if 条件，重新运行一组不同变量名，最后会得到 A-old。错误控制让你看见：所有 Promise 都成功了，程序没有报异常，页面仍可能显示旧快照。判断标准是结果是否属于最新请求，不是网络是否返回二百。

这个小实验只演示回写资格，没有取消资源。对照当前工作台的 latestRefresh，就能找到同样的序号判断；在自己的临时副本去掉序号条件，才是要让负例抓住的坏实现。组件退出还会 abort 自己拥有的请求，清理计时器。版本号、控制器身份或 Query 的 key 都是在表达“这份结果归哪一次观察”，资源取消则负责尽早停止无用工作。

反过来，也别把每个并发请求都粗暴取消。详情、事件与制品可能各自需要独立的身份和生命周期，不能共用一个不断增加的全局数字，让读详情把下载图意外作废。先按数据对象分清通道，再为同一通道的替换请求建立资格检查，代码才不会为了防一个竞争又引入另一个。

## 结果能画出来以后，再检查交互有没有换分母

给一棵合成函数树安排固定权重：main 总计一百、自身十，parse 总计九十、自身二十，decode 总计和自身都是七十。完整树中 decode 占百分之七十；聚焦到 parse 后，当前子树的分母为九十，它在画布里约占百分之七十七点八。原始权重七十完全没变，变的是观察范围。

学生可以先用这棵小树阅读 `components/Flamegraph.tsx` 的 setRoot 与重置路径，再看 Worker 布局如何使用当前根。不要把缩放后的百分比保存回原始结果，也不要在生成报告时拿可见布局替代原树。页面的交互状态属于观察方式，测量制品属于证据，二者要能独立复原。

再加入系统框：某个系统节点 total 三十，其中 self 五，下游业务权重二十五。隐藏该系统框时，业务后代应该提升保留，系统自身五仍需单独计账；不能让它消失，也不能加给业务函数让业务看起来变贵。`prepareFlameTree` 把隐藏 self 留在展示根，并返回隐藏数量供解释。

负向检查不是“按钮能点”，而是聚焦和重置后原输入不变，隐藏前后根总量守恒，切换 self 聚合不重复累加 total，坏分支不能靠隐藏逃过结构校验。现有 flame-analysis 回归正适合沿这些行为阅读。若只对 SVG 截图比像素，很难发现少量权重被错误转移的问题。

最后在界面旁写一句分母说明：这是当前树或当前聚焦子树中的权重比例，不是时间轴，也不是请求耗时百分比。PROC_CPU 的 mini 只有受控进程计账节点，这套函数例子仍是合成教学树；你不能通过给节点换名字，就让真实自采集获得不存在的函数粒度。

## 生产 SSE：先找到实际调用者

现在才切到正式链路。`TaskDetail.tsx` 的 useQuery 读取 `/tasks/{id}`，活动任务按查询策略轮询；另一个 effect 调用 `subscribeTaskEvents`。收到 task_status，只执行详情 query 的 invalidate，让 REST 重读，而不是根据事件正文在浏览器重演完整状态机。

生产事件连接访问 `/api/v1/tasks/{id}/events/stream`，用 fetch 携带 Last-Event-ID；这是正式 API 的能力，不是 mini 的接口。SSE 像通知铃，REST 像账本，通知暂时断开时轮询仍能获取最终状态。页面连接状态 polling 不等于 task FAILED，live 也不等于采集成功。

这样设计还减少事件版本变化对页面的影响。通知只告诉你需要重新读，权威对象仍由任务详情端点给出；重复通知最多造成一次额外刷新，不应重复执行采集。事件序号负责恢复与去重，任务状态与当前 attempt 负责业务资格，它们各自有明确作用。

练习时对照两个文件画出真实箭头：subscribe 回调到 query invalidation，queryFn 到 api，服务端响应到 task 渲染。若你画出“收到 RUNNING 文本就创建一个新任务”，说明把观察与动作混在了一起。先纠正数据流，再讨论动画与延迟优化。

## 从半个汉字到完整事件

SSE 输入是字节流，read 的边界不等于行或事件的边界。一个“采”字的 UTF-8 字节可以切在两次读取之间，CRLF 可以切成前一块的 CR 与后一块的 LF。`EventStreamDecoder` 因此保留增量 TextDecoder、未完成行、data 列表、pendingId 和 afterCR，不是每收到一块就 JSON.parse。

只有空行封口才提交 pendingId，随后发送完整事件。若在 data 中间断线，候选 ID 不能当作已处理游标，否则重连从它之后继续，会跳过其实从未完整交给页面的消息。多条 data 行还要用换行拼接，注释心跳只维持连接，不产生业务事件。

课程 case 将同样机制做成标准库 Python 实现，并把一个汉字切开、再把末尾 CRLF 切开。运行它不访问网页或服务器：

```powershell
python -m tutorials.labs --case sse-frames --json
```

预期完整事件的中文仍为“采集完成”，committed_cursor 为字符串 9007199254740993。紧接着半个新事件带 ID 9007199254740994，却没有空行结束；finish 后没有新事件，truncated_cursor 仍是旧值。非法 UTF-8、超 int64 游标与超过帧限额的注释都必须失败，不能因为不是 JSON 正文就不计资源。

## 游标精度与重连时间线

数据库事件序号可能超过 JavaScript number 的精确整数范围。9007199254740992 与 9007199254740993 转成浮点数时可能相同，拿它们去重会误丢事件。生产保留字符串，用 BigInt 比较；Python 课程使用精确整数，并用 float 碰撞作负向控制，说明问题不是显示格式，而是身份会真的丢失。

按一条时间线演练：完整收到序号 A，保存 A；序号 B 到了一半，连接断开；重连携带 A；B 完整重发，才推进到 B。若服务端又重发 A，订阅层比较后丢弃。decoder 管帧完整，订阅层管顺序，两者不应混成一个“每看见 id 行就更新全局状态”的变量。

网络暂时失败后，客户端采用有上限的指数退避，收到有效新事件再清零失败次数。标签页隐藏时释放连接，重新可见后从已提交游标恢复；旧连接的 finally 也要检查身份，不能清理新连接。401 与不可恢复协议错误有不同处理，不应该永远重试一个已经无权读取的任务。

这段机制没有保证浏览器保存了永不丢失的任务账本。重载可以从详情重新读当前状态，历史事件仍在服务端；游标是提高通知连续性的观察位置。保持这个分工，才能在不完美网络下得到最终一致的界面，而不是试图让每个浏览器成为另一个任务数据库。

## Worker 与制品授权：把大输入放到合适位置

mini 结果很小，工作台只是限额读取和展示 JSON，没有假装需要大型火焰图 Worker。正式 Drop 的 `profile-loader.ts` 才管理专用 profile Worker，`profile.worker.ts` 获取短期制品授权、限额下载、gzip 解压和树校验。结果响应与独立制品的载入路径也有区别，要顺着真实调用读。

当前正式制品压缩上限十六 MiB、展开上限三十二 MiB，总期限三十秒。退出路由、失败、超时和成功都要终止专用 Worker；不能只在正常回包后清理。ArrayBuffer 可转移所有权减少复制，但 transfer 不提供结构安全，树仍要检查深度、节点数、安全整数与权重守恒。

制品下载先向当前授权 API 申请短期地址，再校验同源对象路径；对象请求 omit Cookie、拒绝重定向，地址不进日志。已经有权读取的文件仍可能损坏，有效树也仍可能属于无权任务，所以授权、下载限制和内容校验分别保留。浏览器不保存长期对象存储凭据。

若想做这部分负向实验，先运行现有 profile-loader 与 profile-data 回归，观察取消会终止 Worker、展开超限会报错、非法地址不会进入对象请求。不要向生产服务上传故意膨胀的大文件“试试看”，本地有界夹具已经足够检验这类保护行为。

## 用一个有意义的界面改动验收

选一个真正改善理解的本地任务：把工作台 result 展示拆出刚才的 ResultEvidence，并明确显示“零 tick 合法，不含函数栈”；或者在生产结果区域补充现有单位与来源。先写四种输入预期：result 缺失、零 tick、导入来源、未知单位，再决定实现位置。不要为了截图制造假的现场数据。

前端验证入口如下，范围根据改动调整；涉及 SSE、Worker 或树处理才需要相应机制回归。命令是供你执行的步骤，不代表你的环境已通过：

```powershell
cd web
npm test -- src/event-stream.test.ts src/profile-loader.test.ts src/profile-data.test.ts src/components/flame-analysis.test.ts
npm run build
```

浏览器验收记录至少包括：初次打开未连接时无 mini 请求，连接后才读取列表，创建有幂等键，成功展示真实来源与单位，离开页面停止浏览器工作，后台 task 仍由 Go 管理。若你只看到最终绿色状态，没有核对请求与输入来源，仍不足以证明界面没有展示旧轮结果。

自测答案也很明确。SSE 断开不等于任务失败；number 不适合保存大事件 ID；abort 不能替代异步身份检查；CPU tick 或样本占比不能推出请求 P95。前端真正交付的是可理解、可追溯的观察界面，下一章再把这种要求推进到新增采集类型，让契约从输入一路延续到结果。
