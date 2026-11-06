## 20.1 恢复之前，先承认有些事情现在不知道

上一章的工具网关能决定请求可不可以执行。现在加入一次崩溃：工具已经收到动作，返回结果还没写进账本，调用进程退出。重启后再执行一次很容易，但你并不知道第一次是否已经发生。对只读查询可能只是多耗资源，对创建采集、外部测试或支付类动作却可能重复产生副作用。

这一章实现一个明确的调用账本：执行前登记 pending，确认以后保存 complete 回执；相同请求可以读取完成回执，内容变化必须拒绝，未确认状态要停下来。我们不声称靠这个小模型解决所有分布式事务，而是把“已完成、未开始、无法确定”分成三个可观察结果。

代码仍在 `tutorials/labs/diagnosis.py`，主要读 `LedgerState` 和 `CheckpointLedger`。运行 `python -m tutorials.labs --case checkpoint-replay --json`，预期能看到两个模型预算、两个工具预算已消耗，以及 pending 阻止恢复的检查通过。实验没有调用真实模型，这些计数是有执行规则的合成账本。

一个重要限制先写在前面：resume 创建的新对象共享原 LedgerState，模拟重新连到同一持久存储。它没有把内存变成磁盘，关闭 Python 进程以后状态会消失。实验验证恢复规则，真实跨进程持久性要读后面的 PostgreSQL 实现与专门回归，不能偷偷把这两种证据混称。

## 20.2 三种身份分开：阶段、调用、执行者

先给账本画三层盒子。最外层是 scope，例如某次 diagnosis 的阶段记录；里面有 call_id 标识该阶段的一次工具调用；当前操作盒子的 worker 还必须拿着有效 lease。scope 决定能查哪本账，call_id 决定查哪一笔，lease 决定此刻有没有改账资格。

只用 call_id 做全局字典会串账。两个阶段都可能出现 call-1，不能让修复阶段复用另一次诊断的观测。只用 scope 也不够，一轮模型会产生多个工具调用。至于公开 run_id，它可以用于观察和关联，不能自动成为调用权凭据。

LedgerState 保存 scope、owner、attempt、source_digest、spec_digest、计数、sequence、可见状态和 receipts。它不保存 lease 秘密；当前租约属于 Authority，运行时每次 fence 再核验。真实系统同样不会把临时 capability 放入 checkpoint，恢复时重新发临时能力。

练习答案应写出一对反例：同 call_id 不同 scope，必须分别归属；同 scope、同 call_id、不同请求，必须拒绝重用。不要把它们都叫重复请求，因为前者是不同阶段合法各自使用局部编号，后者是在已经绑定的编号下偷换动作。

## 20.3 规范化请求是为了认出同一动作，不是把差异抹掉

JSON 对象键顺序和无意义空白可以不同，业务动作却相同。教学 canonical 使用排序键与紧凑编码，所以 `{round:1,name:...,arguments:...}` 调换键顺序后仍得到同一摘要。摘要同时包含 round、name、arguments，不能只对参数或工具名字求 hash。

```python
# 教学实现摘录，真正类型/深度/字节限制在 canonical 中。
fingerprint = digest(request)
old = self.state.receipts.get(call_id)
if old is not None:
    require(old["request_digest"] == fingerprint, "CALL_ID_REUSED")
    require(old["status"] == "complete", "AMBIGUOUS_PENDING")
    return "replay", deepcopy(old["response"])
```

先比请求，再看状态。否则调用者可以拿一张 complete 回执冒充另一个参数的成功，或者故意修改 pending 请求内容绕过歧义检查。返回 deepcopy 防止调用者修改回放结果时反向改坏存档，回归也会专门改变取出的 weight 再看账本仍保持原值。

本模型的请求契约只允许整数数值，浮点数拒绝，因此不会默默把 rank=1.0 当整数一。真实 `canonicalAgentJSON` 使用 Go UseNumber，规范化键与空白，同时保留小数、指数和负零的数值词法；两者范围不同，不要把 Python 的普通 json.dumps 当生产严格协议的完整替代。

为什么不能先转 float 再比较？超出二进制浮点精度的两个不同请求可能被压成相同值。对于审计与精确事实，信息在这里丢掉，后面再加 SHA 也找不回来。规范化必须先说明哪些差异允许消失，哪些差异必须留下。

## 20.4 调用前先记账，失败也不能免费得到一轮预算

教学 `reserve_model` 先 fence，再检查剩余次数，最后增加 model_calls。外部动作应该发生在预留之后。即使 provider 随后失败或进程崩溃，这一笔也不自动退回；否则攻击者可以不断制造失败重启，在有限预算的名义下得到无限调用。

工具同样先记 pending 并增加 tool_calls，才把 action=execute 交给调用方。下面是教学模型中的实际语句：

```python
require(request["round"] == self.state.model_calls and request["round"] > 0, "CALL_ROUND")
require(self.state.tool_calls < self.state.tool_limit, "TOOL_BUDGET")
self.state.receipts[call_id] = {"request_digest": fingerprint, "status": "pending"}
self.state.tool_calls += 1
return "execute", None
```

顺序模型里这几行连续执行；真实数据库中插入 pending 与计数增加必须同事务。否则 pending 已插入、计数没增加，恢复就可能漏收费；反过来只加次数却没留下调用身份，排查又不知道它花在何处。不能把“函数里写在一起”误当跨进程原子性。

预留不是拿到成功保证，而是获得一次有界尝试资格。也不要把 model_calls、tool_calls、token 用量和活跃时间混成一个数字：一次工具可能不调用模型，模型实际 token 需要 provider 用量，等待数据库租约的时间也不等于模型执行时间。真实实现分别维护这些账本。

自己改坏一次：把 reserve_model 移到 provider 返回成功之后。纸上演算连续两次“已发出请求但返回前崩溃”，原本应花掉的预算就没记上。这个反例说明收费边界为什么在 I/O 前，不能只从成功率页面的角度设计。

## 20.5 pending 是不确定，不是排队等你随便重做

考虑下面的故障时间线。它是教学情景，并非当前服务器执行记录：

```text
T0  事务提交：call-7=pending，tool_calls 加一
T1  外部工具接到请求，可能已经完成工作
T2  调用方崩溃，没有保存 complete 响应
T3  新 worker 看到 pending
```

T3 既不能宣布成功，也不能断言 T1 没发生。实验的 resume 会拒绝任何仍有 pending 的账本，begin_tool 对同一 pending call 也返回 AMBIGUOUS_PENDING。它保守地停止，而不是凭“重试一般没事”自动启动第二次。

若下游拥有独立执行 token 和可查询回执，可以设计一个专门对账步骤，核实同一动作是否完成，再完成原调用。若下游没有这种能力，就需要明确失败或人工介入。把 pending 时间戳改旧、清空它再跑，不会让历史副作用消失，只会抹掉你知道自己不确定的证据。

这个选择不等于只读工具也永远不能重查。读请求可能允许按业务策略重做，但必须明确新观测的时间与语义，不能把新结果伪装为旧结果回执。真实 Drop 对普通 checkpoint pending 采取拒绝盲恢复，而采集 helper 等路径有自己的 token 和恢复契约，不能混用规则。

最重要的一句话是：没有跨外部系统共享事务，就不要承诺 exactly once。可完成的目标是，同一完成回执可重放、不同内容不可冒领、不确定状态不被假装确定，以及每个已知副作用有可追踪身份。

## 20.6 complete 回执能省去重新执行，但不能跳过当前授权

工具成功后，complete_tool 检查当前 fence、回执是否存在、请求摘要是否一致、状态是否仍为 pending，再保存有界响应。它不会接受“我这里有个结果，请帮我顺便建一张不存在的回执”，也不会允许旧请求去完成另一笔调用。

恢复时已完成回执返回 action=replay，调用方应使用已有 response，不能又调用工具再对照一遍。实验以相同 call_id 和不同键顺序构造请求，预期得到相同 weight，tool_calls 仍为一。重放本身不应该再收一次实际工具调用预算。

不过回执存在不代表任何人永远都可以读。begin_tool 的第一步仍是 fence，旧 lease 已失效就拒绝。若先查缓存立即返回，再检查用户权限，被撤权的调用者仍可能通过重放获取敏感观测。缓存也必须属于授权路径，不能成为旁门。

另一个细节是 complete 不接受重复覆盖。第一次记录什么，就保留什么；重复网络消息应该走 replay 读取，而不是随便把 response 改成新值。对账、修订与重新执行需要新身份和清楚的事件，不能在原记录上悄悄改写过去。

观察测试 `test_receipt_replay_never_reexecutes_and_cannot_change_content`：正常重放、修改返回对象、修改参数和更改 round 都有独立预期。只比较调用次数不够，因为一个错误实现可能不重做工具，却返回了不属于这个请求的旧结果。

## 20.7 新进程接班，预算应该继承债务而不是恢复满血

实验先预留一轮 model，完成一个工具，再换 lease 创建 resumed wrapper。新对象的 state 仍是原账本，model_calls 和 tool_calls 都保持一。随后再预留一次模型到二，第三次必须 MODEL_BUDGET；重新构造 Python 对象不能恢复免费次数。

“预算没有重置”要观察已有计数，而非只检查新对象 limit 还是二。一个错误实现可能保留 limit 却把 calls 清零，看起来配置没有变，实际上每次重启都重新发钱。状态与上限是两件事，恢复要同时校验。

真实 `AgentCheckpointStore.load` 读取累计 model_calls、tool_calls、round_tools 和使用量，超预算直接拒绝。`reserveActive` 先预留小段活跃执行时间，崩溃未用完也不退款，避免连续崩溃不断续到免费时间；排队等租约的时间另算，不能把所有墙钟一股脑归给模型。

模型 provider 重试也算真实尝试。若第一次网络失败，SDK 在内部自动连发五次，而 Go 账本只看到一次 model_start，就失去硬预算意义。真实 runtime 禁用不受控自动重试，受控 provider 重试每次先经过计数；学习模型没有 provider，但要保留这个 I/O 前收费原则。

练习时可以先写三列：剩余预算、已预留、已报告用量。失败不退款是预算规则，token 未知就保持未知是观测规则。不要为了让收据漂亮，把未知 token 填零，也不要让模型自己提交一个更小调用次数覆盖服务端账本。

## 20.8 sequence 防止旧可见状态覆盖新状态

上下文也会竞争。worker A 暂存到 sequence=1 后卡住，worker B 完成更多步骤写到二；A 醒来若直接覆盖 state，新工具结果就丢了。教学 save_visible 要求传入 sequence 恰好等于当前加一，旧的一不能再写一次。

```python
require(type(sequence) is int and sequence == self.state.sequence + 1, "CHECKPOINT_STALE")
require(type(visible) is dict and set(visible) == {"cursor", "observations"}, "CHECKPOINT_SCHEMA")
```

cursor 只允许 model 或 finish，observations 只存有限 E 编号列表。这个投影刻意没有隐藏思考、模型密钥、任意 SDK 对象和可执行代码。保存得越多不是越可靠，反而可能让恢复协议依赖私有内部结构，或把不该落盘的秘密带进去。

真实 Go checkpoint envelope 将 SDK 可见上下文与服务端权威计数、accepted 结果分开。Python 可以提交经过校验的可见投影，不能用自己的 state 覆盖已接受结果和计数。恢复 accepted 也不应凭模型再生成一遍，已经接受的原始 JSON 才是权威结果。

请增加一个合法更新到二的用例，再验证一与三都失败。只测旧一失败，可能漏掉“任何不等于当前的序列都接受”的错误实现；只测试从一到二，也看不到跳号。序列的具体递增契约必须在边界上用正负例一起包住。

## 20.9 来源变了、工具定义变了，就不能继承旧故事

source_digest 绑定原始输入，spec_digest 绑定运行规范。教学 resume 默认要求 teaching-spec-v1；更换为 changed-tools 即拒绝 SPEC_CHANGED。即使同一任务还在，若模型、阶段工具 schema 或原证据已经变化，旧上下文可能不再适用。

这不是保守到无法升级。升级可以明确开始新阶段、新版本记录或定义兼容迁移，但必须由维护者决定，不能让“重试”偷偷承接一个语义已经变化的运行。否则昨天参数 rank 的含义与今天不同，旧 complete 回执看起来结构一样，解释却错位。

Authority 变化还要分别测试。旧 binding 在来源更新后失败 SOURCE_CHANGED；拿当前新来源 binding 访问旧 state，也会因 CHECKPOINT_BINDING 拒绝。前一个防旧调用者继续工作，后一个防新调用者把旧账本嫁接到新证据，两层都需要。

lease 接管与 source 更新也不相同。同一来源、同一 scope 的新合法 worker 可以继承完成账本；来源改变则不能仅换 lease 接着跑。这正是身份字段分开的用途，不能把每次失败一概处理成“刷新token再试”。

练习记录里请保留拒绝时的安全错误码与阶段，不记录原始 profile、capability 或完整工具参数。诊断日志应该帮助你找到哪一道 fence 失效，而不是为了排障把权限材料和敏感观测全部公开。

还应把状态版本与解释文字分开保存。文字可以告诉人为什么拒绝，但程序恢复必须依赖有限枚举、明确身份和结构化字段，不能从一句自然语言错误里猜“似乎可以重试”。日志措辞调整不应该改变账本的转移规则。

## 20.10 回到真实 SQL：锁、账本与业务 fence 的位置

`db/migrations/010_agent_investigation_extensions.sql` 定义 checkpoint 主键为 `(scope_type,scope_id)`，工具回执主键为 `(scope_type,scope_id,call_id)`。status 只允许 pending/complete，并要求 pending 没有响应、complete 必须有响应。这些约束能阻止一部分半合法记录进入表。

```sql
-- 实际迁移中的约束摘录，不是本章要求你执行的迁移。
PRIMARY KEY(scope_type,scope_id,call_id),
FOREIGN KEY(scope_type,scope_id)
  REFERENCES agent_checkpoints(scope_type,scope_id) ON DELETE CASCADE,
CHECK((status='pending' AND response IS NULL)
   OR (status='complete' AND response IS NOT NULL))
```

真正事务在 `AgentCheckpointStore.withRow`：先运行业务 Fence，再锁 checkpoint 行，核对 input digest、过期时间和状态。beginTool 在这个边界中插入 pending 与增加计数，completeTool 再次运行同一 fence，要求恰好更新一条 pending 回执。

不要把远端工具调用放在锁住所有业务行的大事务中。正确分界是预留事务提交、执行外部工具、完成事务重新验证。这样会留下 pending 窗口，所以恢复必须诚实处理不确定；试图用长事务包住网络，也不能让外部副作用跟着 SQL rollback 自动撤销。

锁顺序同样是协议。业务 fence、checkpoint、回执要按约定取得，其他恢复路径不能反过来拿锁。若两个函数单看都有 FOR UPDATE，却互相先拿对方需要的锁，仍会死锁。教学内存模型没有这类并发证明，真实 PG 回归才检查实际事务交错。

还要检查 UPDATE 的影响行数。SQL 没报错，只表示语句被数据库正常执行；若当前 lease 已换人，条件可能一行都没匹配。生产 completeTool 要求恰好一行 pending 记录变为 complete，否则拒绝。把零行更新当成功，会让调用方相信结果已持久化，随即删除本地证据，恢复时却找不到回执。

外键只能证明这笔回执引用了一个存在的 checkpoint，不能证明操作者仍有权限，更不能证明输入来源还没变。所以表约束、业务 fence、行锁和影响行数各管一部分，没有哪一个能单独包办。读 SQL 时可以给条件逐项标注用途，再用“删去这一项会放过谁”检验理解。

另一个常见错误是更新回执时只用 call_id。因为主键是阶段和调用编号的组合，WHERE 同样要限定 scope_type、scope_id、call_id 与 pending 状态。查到同名记录不意味着查到本次记录；这种串账在单用户单阶段演示中很难暴露，必须主动构造两个 scope 的负例。

如果数据库提交成功但 ACK 丢失，重试应查同一笔已完成回执，而不是补插第二笔。唯一约束能挡住重复行，但它不会自动返回原响应；应用仍要识别同一 canonical 请求，读取已有 complete，或对不同内容给出明确冲突。这就是数据库约束与应用幂等配合的地方。

## 20.11 从一次错误恢复追到正确结果，别靠重跑洗掉失败

先在纸上实现一个坏恢复器：它只读 visible，创建一个新 LedgerState，计数和 receipts 都为空。重启以后它会重新执行已完成 call-1，还会把 pending 当没见过；即使最终报告相同，也已经发生额外动作与预算丢失。

修复不是只把 receipts 复制回来。你还要保留 scope/source/spec、计数、sequence、可见投影，并由新 wrapper 使用当前 Authority。复制旧 lease 会让接管失败，忽略 lease 又会放过旧进程。要恢复的是持久事实，不是把旧进程内存原封不动搬到新进程。

可以用这条检查顺序逐步定位：先确认新 wrapper 的 state 身份；再确认完成调用返回 replay；改变参数确认拒绝；再耗尽模型预算确认不重置；最后留下 pending 确认恢复停止。每一步只引入一个变化，失败原因才不会互相遮盖。

如果 pending 负例失败，不要修改实验让它忽略不确定。保留原输出，恢复正确规则，再新增修复后记录。一次失败是你已经观察到的事实，不能因为第二次成功就把第一次删掉；第二十二章会把这种记录接进毕业项目的证据提交。

同样，不要在生产 SQL 手工把 status 改成 complete 来让系统继续。没有真实回执的 complete 是伪造证据。学习者可以在隔离模型里制造坏状态观察防线，真实系统的对账与恢复必须有明确操作范围和来源证明。

## 20.12 实验验收：同时验证回执、预算、游标与无重复动作

运行 checkpoint-replay 后阅读 observations：model_calls 为二、tool_calls 为二、sequence 为一、pending_receipts 为一。这里仍然 PASS 的原因是实验预期 pending 恰好被拒绝；PASS 表示断言成立，不表示账本中的每项业务动作都成功。

```powershell
python -m tutorials.labs --case checkpoint-replay --json
python -m unittest tutorials.labs.test_diagnosis.TeachingDiagnosisTests.test_pending_blocks_resume_and_replay_without_creating_second_receipt -v
```

再读 `test_handover_budget_source_and_cursor_are_independent_fences`。它让同一来源的新 lease 接班，旧 lease 保存状态失败，旧 sequence 失败，换 spec 失败，预算耗尽失败，来源变化也失败。每个断言检验独立条件，不能用一个“总之抛异常”的宽泛断言代替。

真实项目对应 `TestPostgresAgentCheckpointBudgetReceiptsAndLeaseRecovery`、`TestPostgresAgentCheckpointPendingSideEffectNeverBlindlyReplayed`，以及 AgentScope runtime 的恢复测试。前者需要独立 PostgreSQL，后者的 SDK 环境也有锁定依赖。未运行或被 skip 就分别记录，不能用标准库教学实验代替这些验收。

完成后做一项小扩展：新增一个工具请求中含不同 round 的重放负例，要求 call_id 不变也不能借用回执；再把返回响应对象改坏，验证存档未变化。你应该能够指出哪个摘要字段或拷贝边界让它失败，而不仅知道测试名变绿。

## 20.13 下一步是持久实现，但先为迁移写清接受条件

若你想把教学 ledger 保存到本地文件，先定义版本、最大字节、原子写入、崩溃半文件拒绝和重读完整性，再写两个独立进程之间的恢复实验。仅加一行 json.dump 不足以证明可恢复；它可能把旧 state 与新计数分开落盘，产生一份看似能读、事实不一致的账。

若选择 SQLite，说明它是单机教学存储，写事务与锁等待行为不同于生产 PostgreSQL。对照迁移时要保留同 scope/call 唯一约束与 pending/response 约束，再设计真正的并发读写测试。不能只把字典替换成 SELECT，就宣传获得生产级持久 Agent。

本轮可交付的最小成果是：一份字段设计、一段故障时间线、一个能抓错误重放的测试和最终修复 diff。把模型恢复到哪个可见边界、什么时候必须停止也写出来。恢复不是从隐藏思考的某个 token 接着生成，项目承诺的是经过验证的可见步骤与累计预算。

有了身份、回执和账本，我们仍只知道动作是否被受控执行。动作真的改善了程序吗？下一章不再让模型为自己打分，而是准备固定正确答案、受控改动、配对对照与 sham，让错误输出和不稳定测量都能否决一份漂亮结果。
