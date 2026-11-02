"""Agent tool for searching the local knowledge base."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field


class KnowledgeSearchInput(BaseModel):
    query: str = Field(description="要在本地知识库中检索的问题或关键词")
    top_k: int = Field(default=5, ge=1, le=10, description="返回的结果数量")


def build_knowledge_tools(searcher) -> list:
    def search_knowledge_base(query: str, top_k: int = 5) -> str:
        result = searcher.search(query, top_k=top_k)
        if not result.get("enabled"):
            return "[知识库结果] 知识库未启用。请在设置 → 知识库中打开。"
        if result.get("reason") == "empty":
            return (
                "[知识库结果] 知识库还没有索引内容。"
                "请先在设置 → 知识库中添加文件夹。"
            )
        if not result.get("results"):
            return (
                "[知识库结果] 未找到相关内容。"
                "可以在设置 → 知识库中添加文件夹或更新索引。"
            )
        lines = ["[知识库结果]"]
        if result.get("warning"):
            lines.append(f"[提示] {result['warning']}")
        for index, item in enumerate(result["results"], 1):
            location: list[str] = []
            if item.get("heading"):
                location.append(f"标题: {item['heading']}")
            if item.get("page") is not None:
                location.append(f"第{item['page']}页")
            if item.get("sheet"):
                location.append(f"工作表: {item['sheet']}")
            if item.get("slide") is not None:
                location.append(f"第{item['slide']}页幻灯片")
            suffix = " · " + " · ".join(location) if location else ""
            lines.append(
                f"{index}. 来源: {item['path']}（{item['absolute_path']}）"
                f"{suffix} · 相关度 {float(item['score']):.3f}"
            )
            lines.append(f"   {item['text']}")
        return "\n".join(lines)

    return [
        StructuredTool.from_function(
            func=search_knowledge_base,
            name="search_knowledge_base",
            description=(
                "在本地知识库中检索用户添加的文件夹。"
                "当用户询问自己的资料、文档、笔记、之前说过的内容，"
                "或提到某个文件/主题可能收在知识库时，优先使用此工具。"
                "普通常识问题不要调用。"
            ),
            args_schema=KnowledgeSearchInput,
        )
    ]
