"""Built-in MCP server presets shown in the desktop panel.

Only stdio servers are supported.  Presets describe a *default* command and
environment; users can override the values when adding a server.  Secrets stay
in ``mcp_servers.json`` (or can be referenced as ``${ENV_NAME}``).
"""

from __future__ import annotations

import sys
from pathlib import Path


def _root() -> Path:
    return Path(__file__).resolve().parent


CATALOG: list[dict] = [
    {
        "id": "sqlite",
        "name": "SQLite 数据库",
        "description": "只读查询本地 SQLite：列表、表结构、SELECT/PRAGMA。",
        "command": [sys.executable, str(_root() / "mcp_sqlite_server.py")],
        "cwd": ".",
        "env": {"MCP_SQLITE_DB": "data/agent.db"},
        "fields": [
            {
                "key": "MCP_SQLITE_DB",
                "label": "数据库路径",
                "default": "data/agent.db",
                "required": True,
                "secret": False,
            }
        ],
        "permission": "ask",
        "docs_url": "",
    },
    {
        "id": "playwright",
        "name": "浏览器（Playwright）",
        "description": "真实浏览器：打开网页、点击、截图、读取页面内容。",
        "command": [
            "node",
            "desktop/node_modules/@playwright/mcp/cli.js",
            "--browser",
            "chromium",
            "--headless",
            "--no-sandbox",
            "--isolated",
        ],
        "cwd": ".",
        "env": {
            "PLAYWRIGHT_BROWSERS_PATH": str(_root() / ".playwright-cache"),
            "PWTEST_SERVER_REGISTRY": str(_root() / ".playwright-state" / "b"),
            "PWTEST_DAEMON_SESSION_DIR": str(_root() / ".playwright-state" / "daemon"),
            "TEMP": str(_root() / ".playwright-temp"),
            "TMP": str(_root() / ".playwright-temp"),
        },
        "fields": [],
        "permission": "ask",
        "docs_url": "",
    },
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


def catalog_items() -> list[dict]:
    """Return a JSON-safe copy of the catalog."""
    return [
        {
            **item,
            "command": list(item["command"]),
            "env": dict(item["env"]),
            "fields": [dict(field) for field in item["fields"]],
        }
        for item in CATALOG
    ]


def get_preset(preset_id: str) -> dict:
    for item in CATALOG:
        if item["id"] == preset_id:
            return item
    raise KeyError(preset_id)
