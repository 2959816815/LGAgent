import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({
  initApi: vi.fn(async () => undefined),
  listSessions: vi.fn(async () => []),
  listModels: vi.fn(async () => [
    { key: "local", label: "本地 qwen3.5", model: "qwen3.5:2b", available: true },
  ]),
  listAgents: vi.fn(async () => []),
  createSession: vi.fn(async () => ({
    id: "20260101-000000-abcd",
    title: "新会话",
    created_at: "2026-01-01T00:00:00+08:00",
    updated_at: "2026-01-01T00:00:00+08:00",
    message_count: 0,
    model_key: "local",
    messages: [],
  })),
  getSession: vi.fn(),
  deleteSession: vi.fn(),
  patchSessionModel: vi.fn(),
  streamMessage: vi.fn(),
  cancelRun: vi.fn(),
}));

import { App } from "./App";

beforeEach(() => {
  window.desktop = {
    getConfig: async () => ({ baseUrl: "http://test", token: "t" }),
  };
});

describe("App", () => {
  it("renders the three-column shell", async () => {
    render(<App />);
    expect(await screen.findByText("LangGraph Agent")).toBeTruthy();
    expect(screen.getByText("本地服务已连接")).toBeTruthy();
    expect(screen.getByText("今天想做什么？")).toBeTruthy();
  });
});
