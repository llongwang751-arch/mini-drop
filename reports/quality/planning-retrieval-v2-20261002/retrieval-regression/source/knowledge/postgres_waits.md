# PostgreSQL 等待事件与阻塞关系

通过 pg_stat_activity 的状态、事务开始时间、wait_event_type 和 wait_event 判断调查方向。active 状态不保证正在消耗 CPU；可能同时存在等待。

## PostgreSQL 长事务与锁等待

建立阻塞方与被阻塞方关系，并记录事务年龄、请求窗口和查询标识。连接数高不能单独证明锁竞争。查询文本可能含业务敏感数据，使用摘要和受限的只读查询。

## PostgreSQL 处置前后比较

先区分连接池排队、锁等待与查询计算消耗。取消或终止事务会影响业务，不能由知识命中自动执行。复测保持相同事务组合与到达率，并比较等待时长、错误率和吞吐。没有数据库连接器时应明确无法观测。

来源：https://www.postgresql.org/docs/current/monitoring-stats.html 。核对日期 2026-09-19；以部署主版本为准。
