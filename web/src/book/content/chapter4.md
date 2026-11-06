## 4.1 点下创建以后，究竟调用了谁

这一章先不看火焰图。我们盯住一个很普通的动作：用户点击“开始采集”。如果把实现写成“插一行 tasks，然后调用远端”，页面很快就能跑起来。真正麻烦的是：数据库写完了，远端没收到怎么办？远端已经收到，回包却丢了怎么办？用户连点两次，又算几次采集？

先打开 `api/internal/httpapi/server.go`，找到注册 `/tasks` 的路由，再跟到 `resources.go` 的 `createTask`。它先占用一个创建请求槽位，再绑定请求体，接着调用 `Tasks.Create`。请求太多会返回限流错误；这个槽位约束 API 当前正在处理的请求，不能替代数据库里的用户任务配额。

以下是 `resources.go` 的实际摘录：

```go
t, replay, err := s.Tasks.Create(c.Request.Context(), user(c), in, c.GetHeader("Idempotency-Key"), c.GetString("request_id"))
if err != nil {
    s.fail(c, err)
    return
}
status := 201
if replay {
    status = 200
}
s.ok(c, status, t)
```

第一行把当前用户、业务输入、幂等键、请求追踪号交给 service。中间遇错立即返回，避免失败后继续拼一个成功响应。最后两种状态码区别在于“新建了一项”还是“找到了同一个请求的已有结果”，不是采集成功或失败。HTTP 返回以后，采集通常还没开始。

因此阅读路径应该是 `createTask → Tasks.Create → Repository.CreateTask → DispatchOne → Control.Dispatch`。前两步解释用户能不能提出请求，中间建立持久记录，后两步才尝试叫醒采集控制面。别把五个函数当成五层包装；每跨一步，负责的失败情形都变了。

## 4.2 校验分好几道门，每一道回答不同的问题

`server.go` 的绑定器使用严格 JSON 解码：陌生结构字段、坏格式、超限请求不能悄悄进入 service。会话和写请求的 CSRF 检查在 HTTP 中间件完成。它们回答的是“这是不是一个身份有效、来源符合要求的请求”，没有回答这个用户能不能操作所选 Agent。

`Tasks.Create` 首先检查 `u.CanWrite()`，所以只读用户不能因为绕过前端按钮就创建任务。随后检查幂等键、调用 `Normalize` 和 `Validate`、检查目标组。现场采集继续调用 `AuthorizedAgent(..., true)`，确认当前用户拥有该 Agent 的操作权限，再判断在线状态、心跳新鲜度、采集能力和目标资源参数。

这里有两个容易混在一起的词。参数合法，表示 PID、时长、频率、选项等符合任务类型的契约；操作获准，表示这个用户可以使用这个资源。一个漂亮的 PID 数字不会自动授予权限，一个管理员身份也不会让不存在的工具变得可用。后续 C++ 还要在真正的机器上检查进程身份。

导入走另一条分支：service 清除现场 PID、时长、频率、目标，再标记 `source=import`。这不是偷偷帮用户补参数，而是避免一份离线文件在结果里冒充刚刚执行的现场采样。导入不派发 Agent，后面“请求入库、上传对象、登记分析”的恢复规则也与现场采集不同。

练习时先写一张拒绝清单：只读角色、错误组、无权 Agent、离线 Agent、缺少能力、非法参数分别由谁挡住。能从源码指出位置，比记住一串“鉴权、限流、校验”的名词更有用。也不要为了让测试通过，把这些错误一律改成 HTTP 200。

## 4.3 幂等键是重试凭据，请求摘要负责防止偷换内容

网络超时只说明客户端没有拿到答案。假设服务端已经建好了任务，你刷新页面再发一次，就可能把同一进程采两遍。Drop 要求创建携带 `Idempotency-Key`；长度为 8 到 128，拒绝回车、换行和 NUL。同一次创建因网络问题重发，要保留原键；明确调用 `/tasks/:id/retry` 开始新轮次，要给这次重试操作一个新键。重试接口本身若丢了响应，再重发它时仍保留这个新键，不能又建一轮。

但是只记一个键还不够。第一次用键 K 采 PID 100，第二次仍用 K 却改采 PID 200，系统不能把第二次也解释成无害重放。`tasks.go` 在输入规范化和校验后，对 JSON 编码的输入计算摘要，然后按用户与键查已有任务：

```go
body, _ := json.Marshal(in)
hash := repository.DigestBytes(body)
existing, err := s.Repo.Existing(ctx, u.ID, key)
if err == nil {
    if existing.RequestHash != hash {
        return existing, false, domain.Fail("IDEMPOTENCY_CONFLICT", "该幂等键已用于不同请求")
    }
    if !imported || existing.CollectionStatus != "CREATED" {
        return existing, true, nil
    }
}
```

先序列化规范化输入，能让“缺省值已经补齐”的请求按程序认可的口径比较；它不是接受任意 JSON 文本后做字符串相等。摘要描述业务请求，不是一次网络连接。追踪字段是在这个摘要之后由服务端注入的，客户端不能通过换一个 request_id 强行制造一项新工作。

提前查询主要帮助快速处理常见重放，不能独自解决并发。两个请求完全可能一起查到“不存在”。因此仓储事务里还会再次检查键与摘要。这种重复检查有明确的时间原因：第一次是进入服务时的观察，第二次是在争用协调之后准备落库的裁决。

## 4.4 一项任务为什么要一起写下五种记录

`Repository.CreateTask` 建立的不只是 task。它还写首个 attempt、`TASK_CREATED` 事件、可选的 DISPATCH Outbox 和审计。以下几行是原函数中的实际语句，省略的内容是检查和字段构造，顺序未改：

```go
err := r.Context(ctx).Transaction(func(tx *gorm.DB) error {
    // 本摘录省略配额、幂等检查及各对象构造。
    if err := tx.Create(&t).Error; err != nil {
        return err
    }
    if err := tx.Create(&a).Error; err != nil {
        return err
    }
    if err := tx.Create(&event).Error; err != nil {
        return err
    }
    // queue 为真时，原函数在这里创建 out。
    if err := tx.Create(&audit).Error; err != nil {
        return err
    }
    result = t
    return nil
})
```

task 代表用户心里的一件事；attempt 代表这件事的某一轮执行；事件记录发生过什么；Outbox 记录还有什么通知必须送出去；审计记录谁提出了操作。它们都在同一 SQL 事务中，任何一步出错，之前的写入一起回滚。

如果任务已存在却没有 Outbox，后台没人知道要派发；如果只有事件却没有 attempt，页面会看到一个找不到执行身份的开工记录。让这些记录原子出现，比事后用定时脚本猜测“缺什么补什么”可靠。事务保护的范围是 PostgreSQL，远端 RPC 和对象存储并没有因此自动加入事务。

本章不要把任务成功理解成一个布尔值。采集和分析各有状态，创建时是 `CREATED/PENDING`。一项任务可能已经 `COLLECTED`，分析却 `FAILED`。这个组合应保留，让用户知道是证据没收回来，还是证据处理出了错。

## 4.5 为什么数完配额还得加锁

项目限制同一 owner 最多十项活动采集。假设当前有九项，两个 API 请求同时 `count=9`，接着各插一项，就变成十一项。`count < 10` 这个判断没有写错，错的是两个请求之间没有协调。

创建事务的实际语句是：

```go
if err := tx.Exec("SELECT pg_advisory_xact_lock(hashtextextended(?,0))", "task-quota:"+t.OwnerID).Error; err != nil {
    return err
}
```

锁的逻辑名字包含 owner，让同一人的创建和重试按顺序检查配额，不同人的请求仍可继续。它是事务级建议锁，事务结束就释放；不能拿 Go 进程中的 mutex 代替，因为第二个 API 实例不会认识第一个实例内存里的锁。

随后事务再次查幂等键，再统计 `CREATED/QUEUED/DELIVERED/RUNNING/UPLOADING`。这里统计的是活动采集，不是历史任务总数；否则用户做完十次就再也不能采了。重试也使用同一配额锁，再锁任务行，避免新建和重试各走一套互不认识的限制。

锁也要讲顺序。若流程甲先拿配额锁再拿任务锁，流程乙反过来，就可能互相等。读并发实现时，别只圈出 `FOR UPDATE`；在纸上按实际取得顺序画出资源，才能判断是不是所有入口遵守同一约定。

## 4.6 Outbox 是一份待办账，不是神奇消息总线

后台 `Tasks.Run` 每秒尝试一批派发。`DispatchOne` 在事务里选 `published_at IS NULL` 且已到可用时间的 Outbox，使用 `FOR UPDATE SKIP LOCKED` 锁住这一条。取消优先，再按任务优先级和先来顺序挑选。另一个 dispatcher 遇到已锁的行可以跳过，去处理其他待办。

领取后还要读取 task、消息绑定的 attempt 和最新 attempt，并调用 `Dispatchable`。旧消息已经过时，就标记处理完，不能把它转交给新执行。真正的 DISPATCH 会生成短期 PUT URL，把目标、预算、类型等组成 `TaskDesc`，再调用 Core；CANCEL 会传明确的 task_id 和 attempt_id。

注意一个实现细节：网络调用期间 Outbox 行锁仍在，但不持有任务行锁。若 API 拿着任务锁等 Core，而 Core 接到 RPC 后也要更新同一任务，就会形成“我等你回包，你等我放锁”。不能为了看起来更原子，把远端调用塞进一个锁住所有关联行的大事务。

这个设计仍有代价：慢 RPC 会延长 Outbox 事务，造成数据库连接占用，所以 RPC 有四秒超时，派发数量也有界。Outbox 解决的是已提交工作不会仅因 API 当时崩溃而无人投递；它没有消除网络不确定性，也不保证下游工具永远只运行一次。

## 4.7 回包丢了，失败处理反而最容易把成功写坏

把下面这段时间线读两遍。它是教学情景，不是本次执行日志：

```text
T0  API 读到 CREATED，发起 Dispatch
T1  Core 已将任务提交为 QUEUED
T2  网络丢掉响应，API 收到超时
T3  API 的旧快照仍然是 CREATED
```

如果 T3 直接按旧快照宣布“派发失败”，就把已入队的事实改坏了。Drop 的派发重试有次数、退避和截止时间；到耗尽时，它重新锁定并读取当前任务与最新 attempt，再判断旧 Outbox 是否仍有资格推进失败。当前已经 QUEUED、已取消或已换 attempt，就退役旧消息。

所以“超时后重试”只是半句话。完整说法是：保留消息身份，接收端按任务/attempt 幂等接受；提交失败终态前重读持久状态；任何旧消息都不得越过当前执行栅栏。`dispatch_recovery_integration_test.go` 正在测试 accepted、canceled、retried 三种竞态，而不是只测 RPC 返回了一个 error。

不要在介绍项目时说“Outbox 保证 exactly once”。更准确的是，平台使用可重放投递、接收端幂等、执行身份和持久恢复限制重复副作用。跨数据库、网络和进程的任意崩溃组合仍需要逐个定义，不能靠一句术语包掉所有窗口。

## 4.8 attempt 像轮次编号，旧轮次没有当前发布权

用户重试保留 task_id，但创建新的 attempt，原始对象 key 也包含轮次。旧轮次的失败事件和制品保留，新轮次从 `CREATED/PENDING` 开始。把旧行清空重用虽然省表，却会失去“现在收到的结果到底来自哪一轮”的判断依据。

以下是 `fencing.go` 中完整的纯判断：

```go
func Dispatchable(o domain.Outbox, t domain.Task, a domain.Attempt, latestID int64) bool {
    if o.AttemptID != latestID || a.ID != latestID {
        return false
    }
    if o.EventType == "CANCEL" {
        return t.CollectionStatus == "CANCELED" && a.Status == "CANCELED"
    }
    return o.EventType == "DISPATCH" && t.CollectionStatus == "CREATED" && a.Status == "CREATED"
}
```

第一道判断同时核对消息和执行记录，不是只问 task_id 相不相等。第二道允许取消消息处理当前已取消执行。最后一道只派发当前仍处于 CREATED 的轮次；字符串叫 DISPATCH 也不能绕过状态条件。只有这一层还不够，Core 的最终写入同样要验证。

`server.cpp` 的 `current` 先锁 task，再执行新的查询读取最新 attempt。下面是原 SQL 的关键条件摘录，`$1/$2/$3` 都绑定参数：

```sql
WHERE t.id=$1::bigint AND a.id=$2::bigint AND
a.agent_id=$3 AND a.attempt=(SELECT max(x.attempt) FROM task_attempts
x WHERE x.task_id=t.id) FOR UPDATE OF t,a
```

为什么锁后重新发 SQL？因为等待任务锁期间，别的事务可能完成重试。不能把等待前读到的“最新”拿来继续用。这是并发代码里很具体的一秒差距，普通顺序单测往往看不见。

## 4.9 取消、完成、重试撞在一起时谁赢

取消事务锁住当前 task，已终态直接返回；否则同时更新任务、attempt、分析作业和取消 Outbox，并清除相应上传授权。重复点取消不应该再制造一批事件和审计。取消不是浏览器终止 fetch，也不是仅把按钮文字改成“已取消”。

按下面的顺序推演。表里是应有行为，不代表所有网络包都会立即消失：

| 先提交的事实               | 随后到达的动作          | 应保留的结果               |
| -------------------------- | ----------------------- | -------------------------- |
| attempt 1 已取消           | attempt 1 上报采集成功  | 保留取消，拒绝恢复为成功   |
| attempt 1 已终态并重试为 2 | 轮次 1 的 CANCEL Outbox | 退役旧消息，不能取消轮次 2 |
| Core 已把当前轮次入队      | 旧派发最后一次 RPC 超时 | 重读后保留 QUEUED          |
| 采集与分析都已完成         | 用户再次取消            | 保留既有终态，不重复记事件 |
| 任务仍在 RUNNING           | 用户请求重试            | 拒绝；先明确结束当前轮次   |

“谁先请求”不等于“谁先提交”。浏览器时间、日志打印先后也不能代替数据库的事务顺序。阅读日志时，把 task_id、attempt_id 和事件序列放在一起，别仅按几行时间戳推断并发输赢。

也不要误解清除上传 URL 的能力：已签发 URL 可能在短期有效期内仍被对象存储接受。真正的保护还包括轮次独立 key 和数据库登记栅栏。字节可能上传成功，不意味着旧 attempt 重新拥有当前任务的发布权。

## 4.10 动手：用标准库复现一次旧消息误伤

在自己的临时目录保存 `state_lab.py`，运行 `python state_lab.py`。它只使用内存和 Python 标准库，不连接任何项目服务。以下是教学模型，不是可替换生产仓储的代码；本教程提供预期，没有替你执行这个实验。

```python
from dataclasses import dataclass
import unittest

@dataclass
class State:
    latest: int = 1
    status: str = "CANCELED"

def naive_cancel(state, message_attempt):
    state.status = "CANCELED"

def fenced_cancel(state, message_attempt):
    if message_attempt != state.latest:
        return False
    state.status = "CANCELED"
    return True

class StateLab(unittest.TestCase):
    def test_late_cancel(self):
        state = State(latest=2, status="CREATED")
        self.assertFalse(fenced_cancel(state, 1))
        self.assertEqual(state.status, "CREATED")

    def test_current_cancel(self):
        state = State(latest=2, status="CREATED")
        self.assertTrue(fenced_cancel(state, 2))
        self.assertEqual(state.status, "CANCELED")

if __name__ == "__main__":
    unittest.main()
```

预期是两项测试通过。先别庆祝，把第一个用例中的 `fenced_cancel(state, 1)` 暂时换成 `naive_cancel(state, 1)`，并把那句断言改成直接调用。下一句应失败：轮次 2 本来应该保持 CREATED，却被轮次 1 的取消写坏了。再恢复正确函数，观察它为什么通过。

答案不在“Python 比 Go 容易”或“加了一个 if”。真正建立的规则是，任何外部消息都要携带执行身份，并在改变当前状态的边界重新核对。教学模型没有并发锁、事务、授权、重复事件控制；它只能帮你看清单个不变量，不能当成真实数据库测试已经通过。

## 4.11 对照已有回归，把纸上推演变成可检查的工作

已有 Go 依赖时，可在 `E:\tx-drop\api` 运行下面两条小范围命令。它们选择纯函数和服务入口测试，不要求生产连接，也不启动采集器。命令是你的练习步骤，不是本章编写时的执行收据。

```powershell
go test ./internal/repository -run 'TestOldCancellationCannotCancelRetriedAttempt|TestCanceledTaskCannotBeDispatched' -count=1 -v
go test ./internal/service -run 'TestViewerCannotCreateOrCancelBeforeRepositoryAccess|TestIdempotencyKeysAreBounded' -count=1 -v
```

先打开对应测试，看它们究竟构造了什么。`TestOldCancellationCannotCancelRetriedAttempt` 用轮次 1 消息、最新轮次 2 验证拒绝；还包含“任务已取消，但轮次已经变了”的负例。少了后者，错误实现可能只检查状态，恰好蒙过最直观的用例。

真实锁等待与事务竞态要看 `TestPostgresExhaustedDispatchCannotOverwriteConcurrentLifecycle`。它需要明确配置的独立测试 PostgreSQL，并由测试工具准备数据；未配置时可能跳过。只看到总命令退出码为零，不足以宣称这个并发窗口已验证。绝不能把生产 DSN 填进测试环境凑一项 PASS。

给自己留一份小练习答案：我保护哪个不变量，坏实现怎样破坏它，纯测试证明了哪一部分，还缺哪种真实并发验证。这样得到的是能复查的理解，而不是“我把测试跑绿了”的一句话。

## 4.12 现在再回答：这里是不是只在增删改查

用户管理和任务列表当然包含 CRUD，SQL 的最终动作也仍是插入和更新。但同样一条 UPDATE，若要同时满足当前身份、合法状态、权限、配额、锁顺序和原子事件，它解决的已经不是表单字段搬运。难点在于错误时间点上来的请求也必须得到正确结果。

最后做一道不看答案的推演：导入文件的 PUT 成功，随后登记制品的 SQL 失败，客户端还保留同一个幂等键。下一次应新建 task，还是补齐已有 task？答案是后者：稳定对象 key 与已有身份允许补齐 CREATED 的导入；另造一项会分裂同一请求，也可能遗留难以解释的文件。

再问一句：为什么不能让所有错误都无限重试？因为授权会变化、任务会取消、旧轮次会过时，URL 和本地证据也会过期。可恢复系统需要说明何时继续、何时停止、何时留下明确失败。下一章去 C++ 端看：控制面已经发出一项有身份的任务，Agent 怎样在操作真实进程时守住同样的承诺。
