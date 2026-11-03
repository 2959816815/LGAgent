import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({
  getMcpResources: vi.fn(async () => ({
    resources: [{ uri: "demo://readme", name: "README", description: "", mime_type: "text/plain" }],
    templates: [],
  })),
  getMcpPrompts: vi.fn(async () => ({
    prompts: [{ name: "summarize", description: "", arguments: [{ name: "text", description: "", required: true }] }],
  })),
  readMcpResource: vi.fn(async () => ({ uri: "demo://readme", text: "hello", binary: false, size: 5 })),
  getMcpPrompt: vi.fn(async () => ({ name: "summarize", description: "", messages: [{ role: "user", text: "请总结" }] })),
}));

import { McpServerDetail } from "./McpServerDetail";

afterEach(cleanup);

describe("McpServerDetail", () => {
  it("loads and reads a remote resource", async () => {
    render(
      <McpServerDetail
        server={{ id: "demo", name: "Demo", enabled: true, status: "running", permission: "ask", tool_count: 1, tools: ["echo"], transport: "streamable-http" }}
        onInsert={vi.fn()}
      />,
    );

    expect(await screen.findByText(/README/)).toBeTruthy();
    fireEvent.click(screen.getByText("读取"));
    expect(await screen.findByText("hello")).toBeTruthy();
  });
});
