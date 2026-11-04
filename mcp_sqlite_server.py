"""A tiny read-only SQLite MCP server.

The desktop App talks to this process over stdio.  It intentionally exposes
only read operations: the Agent can inspect schema and run SELECT/PRAGMA
queries, but cannot modify the database.
"""

from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations


mcp = FastMCP("sqlite")
_TABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_READ_ONLY_PREFIXES = ("select", "with", "pragma", "explain")
# 跳过开头的注释，避免 "-- 备注\nSELECT ..." 被误判成非只读语句。
_LEADING_COMMENTS = re.compile(r"^(?:\s*--[^\n]*(?:\n|$)|\s*/\*.*?\*/)*", re.S)


def _first_keyword(statement: str) -> str:
    stripped = _LEADING_COMMENTS.sub("", statement).lstrip("( \t\r\n")
    if not stripped:
        return ""
    return stripped.split(None, 1)[0].lower()


def _db_path() -> Path:
    raw = os.environ.get("MCP_SQLITE_DB", "data/agent.db").strip()
    path = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    path = _db_path()
    if not path.is_file():
        raise ValueError(f"数据库不存在: {path}")
    # mode=ro guarantees this server never writes, even if a query is crafted.
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _format_rows(columns: list[str], rows: list[sqlite3.Row], total: int) -> str:
    lines = [" | ".join(columns), "-+-".join("-" * len(name) for name in columns)]
    for row in rows:
        lines.append(" | ".join("" if row[name] is None else str(row[name]) for name in columns))
    if total > len(rows):
        lines.append(f"... 已截断，仅显示前 {len(rows)} 行（还有更多行）")
    return "\n".join(lines)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def list_tables() -> str:
    """列出当前 SQLite 数据库中的表名。"""
    try:
        with _connect() as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        if not rows:
            return "（数据库中没有表）"
        return "\n".join(str(row["name"]) for row in rows)
    except Exception as exc:
        return f"[错误] 读取表列表失败: {exc}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def describe_table(table: str) -> str:
    """查看一张表的列定义和索引。"""
    name = (table or "").strip()
    if not _TABLE_NAME.fullmatch(name):
        return "[错误] 表名不合法。"
    try:
        with _connect() as connection:
            columns = connection.execute(f'PRAGMA table_info("{name}")').fetchall()
            indexes = connection.execute(f'PRAGMA index_list("{name}")').fetchall()
        if not columns:
            return f"[错误] 表不存在或没有列: {name}"
        lines = [f"表: {name}", "列:"]
        for column in columns:
            primary = " PRIMARY KEY" if column["pk"] else ""
            not_null = " NOT NULL" if column["notnull"] else ""
            lines.append(
                f"- {column['name']} {column['type'] or 'ANY'}{primary}{not_null}"
            )
        if indexes:
            lines.append("索引:")
            lines.extend(f"- {index['name']}" for index in indexes)
        return "\n".join(lines)
    except Exception as exc:
        return f"[错误] 读取表结构失败: {exc}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def query(sql: str, max_rows: int = 50) -> str:
    """执行只读查询（SELECT/WITH/PRAGMA/EXPLAIN），返回表格文本。"""
    statement = (sql or "").strip()
    if not statement:
        return "[错误] SQL 不能为空。"
    # 不再手工找分号：字符串字面量里的 ';' 是合法的，多语句交给 sqlite3 判定。
    first = _first_keyword(statement)
    if first not in _READ_ONLY_PREFIXES:
        return "[错误] 只允许只读查询（SELECT/WITH/PRAGMA/EXPLAIN）。"
    try:
        limit = max(1, min(int(max_rows), 200))
    except (TypeError, ValueError):
        limit = 50
    try:
        with _connect() as connection:
            cursor = connection.execute(statement)
            columns = [item[0] for item in cursor.description or []]
            rows = cursor.fetchmany(limit + 1)
            total = len(rows)
            shown = rows[:limit]
        if not columns:
            return "（查询没有返回列）"
        return _format_rows(columns, shown, total)
    except sqlite3.ProgrammingError as exc:
        if "one statement" in str(exc).lower():
            return "[错误] 一次只能执行一条语句。"
        return f"[错误] 查询失败: {exc}"
    except Exception as exc:
        return f"[错误] 查询失败: {exc}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def schema() -> str:
    """返回所有表的 CREATE 语句。"""
    try:
        with _connect() as connection:
            rows = connection.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        if not rows:
            return "（数据库中没有用户表）"
        return "\n\n".join(f"-- {row['name']}\n{row['sql']}" for row in rows)
    except Exception as exc:
        return f"[错误] 读取 schema 失败: {exc}"


if __name__ == "__main__":
    mcp.run()
