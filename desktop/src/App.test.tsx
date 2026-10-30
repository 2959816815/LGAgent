import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => {
  const detail = {
    id: "20260101-000000-abcd",
    title: "历史会话",
    created_at: "2026-01-01T00:00:00+08:00",
    updated_at: "2026-01-01T00:00:00+08:00",
    message_count: 2,
    model_key: "local",
    has_more: true,
    next_before: 4,
    messages: [
      { role: "user", content: "原始问题" },
      { role: "assistant", content: "原始回答" },
    ],
  };
  return {
    initApi: vi.fn(async () => undefined),
    listSessions: vi.fn(async () => [detail]),
    listModels: vi.fn(async () => [
      { key: "local", label: "本地 qwen3.5", model: "qwen3.5:2b", available: true },
    ]),
    listAgents: vi.fn(async () => []),
    listFiles: vi.fn(async () => []),
    searchFiles: vi.fn(async () => []),
    createSession: vi.fn(async () => detail),
    getSession: vi.fn(async () => detail),
    deleteSession: vi.fn(),
    patchSessionModel: vi.fn(),
    streamMessage: vi.fn(async function* () {
      // 空流：测试只关心调用参数
    }),
    compactSession: vi.fn(async () => detail),
    cancelRun: vi.fn(),
  };
});

import { App } from "./App";
import { compactSession, getSession, streamMessage } from "./api";

beforeEach(() => {
  window.desktop = {
    getConfig: async () => ({ baseUrl: "http://test", token: "t" }),
    getSetupState: async () => ({ needsSetup: false, baseUrl: "https://api.deepseek.com", model: "deepseek-flash" }),
    getSettings: async () => ({ baseUrl: "https://api.deepseek.com", model: "deepseek-flash", hasApiKey: true }),
    testDeepSeek: async () => ({ ok: true }),
    testSettings: async () => ({ ok: true }),
    saveDeepSeek: async () => ({ baseUrl: "http://test", token: "t" }),
    saveSettings: async () => ({ baseUrl: "http://test", token: "t" }),
    exportDiagnostics: async () => null,
    getPathForFile: () => "",
  };
});

afterEach(cleanup);

describe("App", () => {
  it("renders the three-column shell", async () => {
    render(<App />);
    expect(await screen.findByText("LangGraph Agent")).toBeTruthy();
  });

  it("regenerates the last assistant reply", async () => {
    render(<App />);

    fireEvent.click(await screen.findByText("重新生成"));

    await waitFor(() =>
      expect(streamMessage).toHaveBeenCalledWith(
        "20260101-000000-abcd",
        "",
        expect.any(AbortSignal),
        [],
        { mode: "regenerate", editIndex: undefined },
      ),
    );
  });

  it("compacts the current session from the context tab", async () => {
    vi.stubGlobal("confirm", () => true);
    render(<App />);

    fireEvent.click(await screen.findByText("上下文"));
    fireEvent.click(await screen.findByText("立即压缩上下文"));

    await waitFor(() =>
      expect(compactSession).toHaveBeenCalledWith("20260101-000000-abcd"),
    );
    vi.unstubAllGlobals();
  });

  it("loads earlier messages on demand", async () => {
    render(<App />);

    fireEvent.click(await screen.findByText("加载更早的消息"));

    await waitFor(() =>
      expect(getSession).toHaveBeenCalledWith("20260101-000000-abcd", {
        limit: 200,
        before: 4,
      }),
    );
  });

  it("opens the settings dialog from the sidebar", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "设置" }));
    expect(await screen.findByPlaceholderText("留空表示不修改")).toBeTruthy();
    expect(screen.getByText("本地服务已连接")).toBeTruthy();
  });
});
