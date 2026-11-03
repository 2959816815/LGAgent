import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { McpRemoteForm } from "./McpRemoteForm";

afterEach(cleanup);

describe("McpRemoteForm", () => {
  it("tests a connection with headers", async () => {
    const onTest = vi.fn(async () => ({
      ok: true,
      transport: "streamable-http",
      server_info: {},
      capabilities: {},
      tools: 1,
      resources: 1,
      prompts: 1,
      error: "",
    }));
    render(<McpRemoteForm onSave={vi.fn()} onTest={onTest} onCancel={vi.fn()} />);

    fireEvent.change(screen.getByPlaceholderText("remote_demo"), { target: { value: "demo" } });
    fireEvent.change(screen.getByPlaceholderText("https://example.com/mcp"), { target: { value: "https://example.com/mcp" } });
    fireEvent.click(screen.getByText("测试连接"));

    await waitFor(() => expect(onTest).toHaveBeenCalled());
    expect(await screen.findByText(/连接成功/)).toBeTruthy();
  });
});
