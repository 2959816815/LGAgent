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
    getModelHealth: vi.fn(async () => ({ key: "deepseek", reachable: true })),
    addRemoteMcpServer: vi.fn(async () => undefined),
    testMcpServer: vi.fn(async () => ({
      ok: true,
      transport: "streamable-http",
      server_info: {},
      capabilities: {},
      tools: 0,
      resources: 0,
      prompts: 0,
      error: "",
    })),
    importMcpServers: vi.fn(async () => ({ entries: [], errors: [] })),
    getMcpResources: vi.fn(async () => ({ resources: [], templates: [] })),
    readMcpResource: vi.fn(async () => ({
      uri: "",
      text: "",
      binary: false,
      size: 0,
    })),
    getMcpPrompts: vi.fn(async () => ({ prompts: [] })),
    getMcpPrompt: vi.fn(async () => ({ name: "", description: "", messages: [] })),
  };
});

import { App } from "./App";
import {
  compactSession,
  getModelHealth,
  getSession,
  patchSessionModel,
  streamMessage,
} from "./api";

beforeEach(() => {
  window.desktop = {
    getConfig: async () => ({ baseUrl: "http://test", token: "t" }),
    getSetupState: async () => ({ needsSetup: false, baseUrl: "https://api.deepseek.com", model: "deepseek-flash" }),
    getSettings: async () => ({ baseUrl: "https://api.deepseek.com", model: "deepseek-flash", hasApiKey: true }),
    getModels: async () => [],
    saveModels: async () => ({ baseUrl: "http://test", token: "t" }),
    testModel: async () => ({ ok: true }),
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

  it("shows the app version next to the brand", async () => {
    render(<App />);
    expect(await screen.findByText(`v${__APP_VERSION__}`)).toBeTruthy();
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

  it("shows the fallback banner and switches back", async () => {
    vi.mocked(streamMessage).mockImplementationOnce(async function* () {
      yield {
        event: "model_fallback",
        data: { from: "deepseek", to: "local", label: "本地 qwen3.5", reason: "连接失败" },
      };
      yield { event: "done", data: {} };
    });
    vi.mocked(patchSessionModel).mockResolvedValue({
      model_key: "deepseek",
      label: "DeepSeek",
    });
    render(<App />);

    fireEvent.click(await screen.findByText("重新生成"));

    expect(await screen.findByText(/已自动切换到本地模型/)).toBeTruthy();
    const button = await screen.findByText(/网络已恢复/);
    fireEvent.click(button);

    await waitFor(() =>
      expect(patchSessionModel).toHaveBeenCalledWith(
        "20260101-000000-abcd",
        "deepseek",
      ),
    );
  });

  it("compacts the current session from the context tab", async () => {
    vi.stubGlobal("confirm", () => true);
    render(<App />);

    fireEvent.click(await screen.findByText("上下文"));
    fireEvent.click(await screen.findByText("更新摘要"));

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
    expect(await screen.findByText("＋ 添加模型")).toBeTruthy();
    expect(screen.getByText("本地服务已连接")).toBeTruthy();
  });
});
