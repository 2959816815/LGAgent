from __future__ import annotations

import unittest

from mcp import types

from mcp_resources import (
    MAX_PROMPT_CHARS,
    MAX_RESOURCE_CHARS,
    McpPromptArgument,
    format_call_tool_result,
    format_prompt_for_composer,
    format_read_resource,
    parse_prompts,
    parse_resources,
    parse_templates,
    parse_tools,
)


class ParseTests(unittest.TestCase):
    def test_parses_tools_from_sdk_types(self):
        result = types.ListToolsResult(
            tools=[
                types.Tool(
                    name="echo",
                    description="回显",
                    inputSchema={"type": "object", "properties": {}},
                    annotations=types.ToolAnnotations(readOnlyHint=True),
                )
            ]
        )

        specs = parse_tools(result)

        self.assertEqual(specs[0].name, "echo")
        self.assertTrue(specs[0].read_only)

    def test_parses_resources_and_templates(self):
        resources = types.ListResourcesResult(
            resources=[
                types.Resource(
                    uri="demo://readme",
                    name="README",
                    description="说明",
                    mimeType="text/plain",
                )
            ]
        )
        templates = types.ListResourceTemplatesResult(
            resourceTemplates=[
                types.ResourceTemplate(
                    uriTemplate="demo://items/{id}",
                    name="Item",
                    description="",
                    mimeType="",
                )
            ]
        )

        parsed = parse_resources(resources)
        parsed_templates = parse_templates(templates)

        self.assertEqual(parsed[0].uri, "demo://readme")
        self.assertEqual(parsed_templates[0].uri_template, "demo://items/{id}")

    def test_parses_prompts_and_arguments(self):
        result = types.ListPromptsResult(
            prompts=[
                types.Prompt(
                    name="summarize",
                    description="总结",
                    arguments=[
                        types.PromptArgument(name="text", description="内容", required=True)
                    ],
                )
            ]
        )

        prompts = parse_prompts(result)

        self.assertEqual(prompts[0].name, "summarize")
        self.assertEqual(
            prompts[0].arguments[0],
            McpPromptArgument(name="text", description="内容", required=True),
        )


class FormatTests(unittest.TestCase):
    def test_text_resource_is_returned(self):
        result = types.ReadResourceResult(
            contents=[
                types.TextResourceContents(
                    uri="demo://readme", mimeType="text/plain", text="hello"
                )
            ]
        )

        formatted = format_read_resource(result)

        self.assertEqual(formatted["text"], "hello")
        self.assertFalse(formatted["binary"])

    def test_binary_resource_is_summarized(self):
        result = types.ReadResourceResult(
            contents=[
                types.BlobResourceContents(
                    uri="demo://image", mimeType="image/png", blob="AAAA"
                )
            ]
        )

        formatted = format_read_resource(result)

        self.assertTrue(formatted["binary"])
        self.assertIn("[二进制资源]", formatted["text"])
        self.assertIn("image/png", formatted["text"])

    def test_resource_text_is_truncated(self):
        result = types.ReadResourceResult(
            contents=[
                types.TextResourceContents(
                    uri="demo://long", mimeType="text/plain", text="x" * (MAX_RESOURCE_CHARS + 10)
                )
            ]
        )

        formatted = format_read_resource(result)

        self.assertLessEqual(len(formatted["text"]), MAX_RESOURCE_CHARS + 20)
        self.assertIn("资源内容已截断", formatted["text"])

    def test_call_tool_result_extracts_text(self):
        result = types.CallToolResult(
            content=[types.TextContent(type="text", text="完成")]
        )

        self.assertEqual(format_call_tool_result(result), "完成")

    def test_prompt_composer_text_has_roles_and_cap(self):
        messages = [
            types.PromptMessage(
                role="user", content=types.TextContent(type="text", text="问题")
            ),
            types.PromptMessage(
                role="assistant", content=types.TextContent(type="text", text="回答")
            ),
        ]

        from mcp_resources import parse_prompt_messages

        text = format_prompt_for_composer("demo", parse_prompt_messages(types.GetPromptResult(messages=messages)))

        self.assertIn("[Prompt: demo]", text)
        self.assertIn("[user]", text)
        self.assertIn("[assistant]", text)
        self.assertLessEqual(len(text), MAX_PROMPT_CHARS + 30)
