# A2 数据层：SQLite 会话存储

日期：2026-10-28
状态：已实现，等待安装包回归

## 目标

把桌面端会话从 `.sessions/*.json` 迁移到单个 SQLite 数据库，同时保持
`desktop_api.py` 与前端 API 不变。安装包默认启用 SQLite；开发环境仍可用
JSON 作为回退。

## 存储位置

- 安装版：`%APPDATA%\langgraph-agent\agent.db`
- 由 `DSH_DATA_DIR` 决定；`desktop/main.js` 向 sidecar 传入 `DSH_SESSION_STORE=sqlite`

## 表结构

```sql
sessions(id, title, created_at, updated_at, model_key, message_count)
messages(session_id, seq, payload, PRIMARY KEY(session_id, seq))
state(key, value)
```

- `messages.payload` 保存 LangChain `messages_to_dict` 的单条 JSON。
- `state` 保存 `active_id` 和 `legacy_json_migrated`。
- 开启 `foreign_keys`、WAL。

## 兼容与迁移

- 首次打开空库时，如果同目录存在旧 `.sessions/`，导入所有 JSON 会话并保留
  `active_id`；写入 `legacy_json_migrated=1`，之后不重复导入。
- `DSH_SESSION_STORE=json` 可强制回退旧后端。
- 写操作仍经过 `sanitize_messages`，不完整 tool_calls 不会入库。

## 代码

- 新增 `session_store_sqlite.py`，实现与 `SessionStore` 相同的
  `create/save/load/list_sessions/delete/active/load_active_or_create` 接口。
- `desktop_api._create_session_store(data_dir)` 按环境变量选择后端。
- 新增 `tests/test_session_store_sqlite.py`：9 个用例，覆盖往返、排序、
  删除、迁移、悬空 tool_calls、非法 ID 和后端选择。

## 验证

- `tests.test_session_store_sqlite`：9/9 通过。
- 全量 Python 测试：233/233 通过。
- 待完成：安装包内首次启动迁移、重启持久化、会话搜索回归。

## 不在本次范围

- 配置迁移到 SQLite（当前 `settings.json` 仍由 safeStorage 加密）。
- 本地 OpenAI 兼容服务设置页。
- 诊断包。
