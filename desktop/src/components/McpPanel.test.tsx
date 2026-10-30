import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { McpPanel } from "./McpPanel";

afterEach(cleanup);

describe("McpPanel", () => {
  it("renders running server and actions", () => {
    const onRestart = vi.fn();
    const onToggle = vi.fn();
    render(
      <McpPanel
        servers={[
          {
            id: "playwright",
            name: "浏览器（Playwright）",
            status: "running",
            enabled: true,
            permission: "ask",
            tool_count: 23,
            last_error: null,
          },
        ]}
        catalog={[]}
        onRestart={onRestart}
        onToggle={onToggle}
        onAdd={vi.fn(async () => {})}
        onRemove={vi.fn()}
        onPermission={vi.fn()}
      />,
    );
    expect(screen.getByText("浏览器（Playwright）")).toBeTruthy();
    expect(screen.getByText("运行中")).toBeTruthy();
    fireEvent.click(screen.getByText("停用"));
    expect(onToggle).toHaveBeenCalledWith("playwright", false);
    fireEvent.click(screen.getByText("重试"));
    expect(onRestart).toHaveBeenCalledWith("playwright");
  });

  it("disables actions while a server is starting", () => {
    render(
      <McpPanel
        servers={[
          {
            id: "booting",
            name: "启动中的服务",
            status: "starting",
            enabled: true,
            permission: "ask",
            tool_count: 0,
            last_error: null,
          },
        ]}
        catalog={[]}
        onRestart={vi.fn()}
        onToggle={vi.fn()}
        onAdd={vi.fn(async () => {})}
        onRemove={vi.fn()}
        onPermission={vi.fn()}
      />,
    );
    expect(screen.getByText("启动中")).toBeTruthy();
    expect((screen.getByText("重试") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByText("停用") as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows why a server failed", () => {
    render(
      <McpPanel
        servers={[
          {
            id: "broken",
            name: "失败的服务器",
            status: "failed",
            enabled: true,
            permission: "ask",
            tool_count: 0,
            last_error: "握手超时",
          },
        ]}
        catalog={[]}
        onRestart={vi.fn()}
        onToggle={vi.fn()}
        onAdd={vi.fn(async () => {})}
        onRemove={vi.fn()}
        onPermission={vi.fn()}
      />,
    );
    expect(screen.getByText("握手超时")).toBeTruthy();
  });

  it("adds a preset with its required fields", async () => {
    const onAdd = vi.fn(async () => {});
    render(
      <McpPanel
        servers={[]}
        catalog={[
          {
            id: "sqlite",
            name: "SQLite 数据库",
            description: "只读查询",
            command: ["python", "mcp_sqlite_server.py"],
            cwd: ".",
            env: {},
            fields: [
              {
                key: "MCP_SQLITE_DB",
                label: "数据库路径",
                default: "data/agent.db",
                required: true,
                secret: false,
              },
            ],
            permission: "ask",
            installed: false,
          },
        ]}
        onRestart={vi.fn()}
        onToggle={vi.fn()}
        onAdd={onAdd}
        onRemove={vi.fn()}
        onPermission={vi.fn()}
      />,
    );
    fireEvent.click(screen.getAllByText("添加服务")[0]);
    fireEvent.click(screen.getByText("添加"));
    const input = await screen.findByLabelText("数据库路径");
    fireEvent.change(input, { target: { value: "data/demo.db" } });
    fireEvent.click(screen.getByText("确认添加"));
    await waitFor(() =>
      expect(onAdd).toHaveBeenCalledWith("sqlite", { MCP_SQLITE_DB: "data/demo.db" }),
    );
  });
});
