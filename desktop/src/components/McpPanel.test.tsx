import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { McpPanel } from "./McpPanel";

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
            error: null,
          },
        ]}
        onRestart={onRestart}
        onToggle={onToggle}
      />,
    );
    expect(screen.getByText("浏览器（Playwright）")).toBeTruthy();
    expect(screen.getByText("运行中")).toBeTruthy();
    fireEvent.click(screen.getByText("停用"));
    expect(onToggle).toHaveBeenCalledWith("playwright", false);
    fireEvent.click(screen.getByText("重试"));
    expect(onRestart).toHaveBeenCalledWith("playwright");
  });
});
