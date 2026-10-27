# 生产化桌面 Agent v1.0（A1：可安装外壳）设计

状态：用户已批准生产化方向；本文冻结 A1 范围。
日期：2026-10-27
工作区：`D:\Vibecoding\Langgraph`

## 目标

把现有开发态桌面应用推进到「别人电脑上安装后可用」的第一个闭环：

- Windows 10/11 x64 安装包
- 不要求用户安装 Python、Node、Ollama
- 首次启动填写 DeepSeek API Key
- Key 加密存储，不写 `.env`
- 能完成基础对话、文件读写、会话持久化
- 应用重启后配置和会话仍在
- 安装目录与用户数据目录分离，卸载不删除用户数据（除非用户选择）

## A1 范围

1. Electron 生产模式加载构建后的前端，而不是 `localhost:5173`
2. Python sidecar 打包为独立 exe，由 Electron 启动
3. 首次启动向导：DeepSeek Base URL、Model、API Key、测试连接
4. API Key 使用 Electron `safeStorage`（Windows DPAPI）加密
5. 设置存 `%APPDATA%\langgraph-agent\settings.json`
6. sidecar 数据目录指向 `%APPDATA%\langgraph-agent`
7. sidecar 日志落盘
8. electron-builder 生成 NSIS 安装包
9. 干净 Windows 验收清单

## 非目标（后续阶段）

- 本地 OpenAI 兼容服务配置（A2）
- SQLite 会话存储与迁移（A2）
- MCP / Playwright 安装（A2/A3；A1 安装包不承诺）
- 终端命令执行
- RAG、技能、diff/回滚
- macOS/Linux
- 自动更新、代码签名、崩溃上报

## 架构

```text
Electron main
  ├─ settings.json (DPAPI encrypted key)
  ├─ spawn resources/sidecar/langgraph-sidecar.exe --port 0
  ├─ READY {port} handshake
  └─ BrowserWindow -> file://.../dist/index.html
                         │
                         └─ HTTP + X-Desktop-Token -> 127.0.0.1:<port>
```

开发模式保持现状：Vite `5173` + 本机 Python。

## 配置与密钥

`settings.json`：

```json
{
  "version": 1,
  "deepseek": {
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-flash",
    "key_encrypted": "base64..."
  }
}
```

- `base_url`、`model` 明文；API Key 仅存密文
- `safeStorage.isEncryptionAvailable()` 为 false 时拒绝保存并提示
- 不把密钥写入日志、不放进 URL
- 首次启动无配置时只显示向导，不启动 sidecar

## 安装布局

```text
Program Files/LangGraph Agent/
  LangGraph Agent.exe
  resources/app.asar
  resources/sidecar/langgraph-sidecar.exe
  resources/sidecar/_internal/...
```

用户数据：

```text
%APPDATA%/langgraph-agent/
  settings.json
  .sessions/
  logs/sidecar.log
```

## 首次启动流程

1. 应用启动，main 读取 settings
2. 无有效 DeepSeek Key → 创建窗口，渲染 SetupWizard
3. 用户填 Key/Base URL/Model，点「测试连接」
4. main 调用 `POST {base_url}/chat/completions`，15s 超时
5. 成功后加密保存；启动 sidecar；reload 窗口
6. 有配置 → 启动 sidecar → 正常进入 App

## 验收

在干净 Windows 10/11 x64 虚拟机：

- [ ] 不安装 Python/Node/Ollama，安装包可安装
- [ ] 首次启动显示向导
- [ ] 错误 Key 显示明确错误，不崩溃
- [ ] 正确 Key 保存后进入主界面
- [ ] 发消息收到 DeepSeek 回复
- [ ] 重启应用后无需重填 Key
- [ ] 会话在重启后仍存在
- [ ] 日志文件存在且不含 API Key
- [ ] 卸载程序可运行

## 后续

A2：本地服务地址、SQLite、MCP 安装、诊断包。
A3：终端、RAG、技能、自动更新、跨平台。

## 本机验收记录（2026-10-28）

已在 Windows 10 x64 本机用打包后的 `release/win-unpacked` 和全新 `APPDATA` 验证：

- 首次启动显示 DeepSeek 向导。
- 错误 Key 返回明确 401 错误，向导不崩溃。
- 正确 Key 保存后进入主界面；Key 以 `safeStorage` 密文写入 `%APPDATA%\langgraph-agent\settings.json`。
- 发送消息收到 `deepseek-flash` 回复。
- 关闭并重启后不再要求填写 Key，已有会话仍存在。
- `%APPDATA%\langgraph-agent\logs\sidecar.log` 中未出现 API Key 明文。

尚未完成：干净 Windows 虚拟机验收、卸载程序实测。安装包内快捷方式由应用首次启动时补写（带 `--no-sandbox`），仍建议在目标机器上复核。
