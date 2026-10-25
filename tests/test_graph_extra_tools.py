from __future__ import annotations

import unittest
from unittest.mock import patch

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

import graph_agent


class EchoArgs(BaseModel):
    text: str


def echo(text: str) -> str:
    return text


class FakeLLM:
    def __init__(self) -> None:
        self.bound = []

    def bind_tools(self, tools):
        self.bound = list(tools)
        return self

    def invoke(self, messages):
        raise AssertionError("not invoked")


class GraphExtraToolsTests(unittest.TestCase):
    def test_build_graph_includes_extra_tools(self) -> None:
        fake = FakeLLM()
        extra = StructuredTool.from_function(
            func=echo,
            name="mcp__demo__echo",
            description="[demo] echo",
            args_schema=EchoArgs,
            metadata={"permission": "auto"},
        )
        with patch.object(graph_agent, "build_llm", return_value=fake):
            graph_agent.build_graph(
                manager=None,
                include_subagent_tools=False,
                extra_tools=[extra],
            )
        names = [tool.name for tool in fake.bound]
        self.assertIn("mcp__demo__echo", names)


if __name__ == "__main__":
    unittest.main()
