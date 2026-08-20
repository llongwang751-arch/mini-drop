# 数据库迁移操作手册

Mini-Drop 使用 Alembic 管理 PostgreSQL Schema。Compose 启动时只有 `migrate` 服务执行迁移，其他服务等待迁移成功，避免多个副本同时改表。

## 一键启动

```bash
docker compose up -d --build
```

检查迁移与服务：

```bash
docker compose ps
docker compose logs migrate
docker compose exec server python -m alembic current
```

正常结果应包含 `20260801_0002 (head)`，且 `migrate` 状态为成功退出。

## 手工升级

```bash
make db-upgrade
make db-current
```

等价命令：

```bash
docker compose run --rm migrate python -m alembic upgrade head
docker compose run --rm migrate python -m alembic current
```

## 回滚一个版本

先备份数据库，再执行：

```bash
make db-downgrade
```

基线版本用于接管已有数据库，基线 downgrade 不删除历史业务表；后续版本必须提供可逆 downgrade。

## 新增迁移

1. 修改 SQLAlchemy Model；
2. 在 `server/migrations/versions/` 新建递增版本；
3. 同时实现 `upgrade()` 与 `downgrade()`；
4. 运行 `tests/test_migrations.py` 验证空库升级、回滚和再次升级；
5. 在现有数据副本上演练后再进入部署流程。

应用服务设置 `MINI_DROP_SCHEMA_MANAGED=1` 后只校验关键表和 `alembic_version`，不再在启动阶段隐式建表或改表。
