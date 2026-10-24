# LangGraph Agent Desktop

开发版桌面壳：Electron + React + 本地 Python sidecar。

## 启动

```powershell
# 1. Python 依赖（已安装可跳过）
D:\Anaconda3\envs\rag\python.exe -m pip install -r requirements-desktop.txt

# 2. Node 依赖
cd D:\Vibecoding\Langgraph\desktop
pnpm install

# 3. 开发模式：Vite + Electron
pnpm dev
```

Electron 主进程会用 `D:\Anaconda3\envs\rag\python.exe -m desktop_api --port 0`
启动 sidecar；如果 Python 不在该路径，设置 `DSH_PYTHON`。

## 测试

```powershell
# Python
cd D:\Vibecoding\Langgraph
D:\Anaconda3\envs\rag\python.exe -m unittest discover -s tests -v

# 前端
cd D:\Vibecoding\Langgraph\desktop
pnpm test
pnpm exec tsc --noEmit
pnpm build
```

## 当前范围

- 会话列表按日期分组、新建/切换/删除。
- 聊天 SSE 流式输出、停止、工具卡片、右栏任务/工具/上下文。
- 模型切换 pill（本地 / DeepSeek / 千问）。
- 开发版从 Vite `http://localhost:5173` 加载；打包和安装器尚未完成。
