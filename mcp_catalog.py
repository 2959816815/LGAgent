"""Built-in MCP server presets shown in the desktop panel.

Only stdio servers are supported.  Presets describe a *default* command and
environment; users can override the values when adding a server.  Secrets stay
in ``mcp_servers.json`` (or can be referenced as ``${ENV_NAME}``).

预设按需构建（而不是在导入时固化），这样开发态与打包态的默认值（例如 SQLite
数据库路径）能各自正确。
"""

from __future__ import annotations

import sys
from pathlib import Path


def _root() -> Path:
    return Path(__file__).resolve().parent


def _sqlite_command() -> list[str]:
    """Use the frozen sidecar as the MCP entrypoint when packaged."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--mcp-sqlite"]
    return [sys.executable, str(_root() / "mcp_sqlite_server.py")]


def _sqlite_default_db() -> str:
    """打包后不给默认值：避免默认指向 App 自己的会话库，由用户显式选择。"""
    if getattr(sys, "frozen", False):
        return ""
    return "data/agent.db"


def _sqlite_preset() -> dict:
    default = _sqlite_default_db()
    return {
        "id": "sqlite",
        "name": "SQLite 数据库",
        "description": "只读查询本地 SQLite：列表、表结构、SELECT/PRAGMA。",
        "command": _sqlite_command(),
        "cwd": ".",
        "env": {"MCP_SQLITE_DB": default},
        "fields": [
            {
                "key": "MCP_SQLITE_DB",
                "label": "数据库路径",
                "default": default,
                "required": True,
                "secret": False,
            }
        ],
        "permission": "ask",
        "docs_url": "",
    }


def _playwright_preset() -> dict:
    # 用 npx 而不是仓库内的 node_modules 路径：打包后仓库结构并不存在。
    # 也不覆盖 PLAYWRIGHT_BROWSERS_PATH / TEMP，让 Playwright 使用用户目录下的
    # 默认位置，避免写入只读的安装目录。
    return {
        "id": "playwright",
        "name": "浏览器（Playwright）",
        "description": "真实浏览器：打开网页、点击、截图、读取页面内容。",
        "command": [
            "npx",
            "-y",
            "@playwright/mcp@latest",
            "--browser",
            "chromium",
            "--headless",
            "--no-sandbox",
            "--isolated",
        ],
        "cwd": ".",
        "env": {},
        "fields": [],
        "permission": "ask",
        "docs_url": "",
    }


_STATIC_PRESETS: list[dict] = [
    {
        "id": "fetch",
        "name": "Fetch 网页抓取",
        "description": "轻量抓取静态网页并转 Markdown；不执行 JavaScript。",
        "command": ["uvx", "mcp-server-fetch"],
        "cwd": ".",
        "env": {},
        "fields": [],
        "permission": "ask",
        "docs_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/fetch",
    },
    {
        "id": "github",
        "name": "GitHub",
        "description": "读取仓库、Issue、PR、代码搜索等（需要 Personal Access Token）。",
        "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
        "cwd": ".",
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_PERSONAL_ACCESS_TOKEN}"},
        "fields": [
            {
                "key": "GITHUB_PERSONAL_ACCESS_TOKEN",
                "label": "GitHub Token",
                "default": "",
                "required": True,
                "secret": True,
            }
        ],
        "permission": "ask",
        "docs_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/github",
    },
    {
        "id": "notion",
        "name": "Notion",
        "description": "搜索页面、读取数据库和内容（需要 Notion Integration Token）。",
        "command": ["npx", "-y", "@notionhq/notion-mcp-server"],
        "cwd": ".",
        "env": {"NOTION_TOKEN": "${NOTION_TOKEN}"},
        "fields": [
            {
                "key": "NOTION_TOKEN",
                "label": "Notion Token",
                "default": "",
                "required": True,
                "secret": True,
            }
        ],
        "permission": "ask",
        "docs_url": "https://github.com/makenotion/notion-mcp-server",
    },
    {
        "id": "filesystem",
        "name": "文件系统",
        "description": "通过 MCP 读写指定目录；与内置文件工具重叠，默认建议只读使用。",
        "command": [
            "npx",
            "-y",
            "@modelcontextprotocol/server-filesystem",
            "${MCP_FS_ROOT}",
        ],
        "cwd": ".",
        "env": {"MCP_FS_ROOT": "."},
        "fields": [
            {
                "key": "MCP_FS_ROOT",
                "label": "允许访问的根目录",
                "default": ".",
                "required": True,
                "secret": False,
            }
        ],
        "permission": "ask",
        "docs_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
    },
]


def _all_presets() -> list[dict]:
    return [_sqlite_preset(), _playwright_preset(), *_STATIC_PRESETS]


def _copy(item: dict) -> dict:
    return {
        **item,
        "command": list(item["command"]),
        "env": dict(item["env"]),
        "fields": [dict(field) for field in item["fields"]],
    }


def catalog_items() -> list[dict]:
    """Return a JSON-safe copy of the catalog."""
    return [_copy(item) for item in _all_presets()]


def get_preset(preset_id: str) -> dict:
    for item in _all_presets():
        if item["id"] == preset_id:
            return item
    raise KeyError(preset_id)
