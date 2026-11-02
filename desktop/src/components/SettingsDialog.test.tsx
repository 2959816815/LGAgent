import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsDialog } from "./SettingsDialog";

const getModels = vi.fn();
const saveModels = vi.fn();
const testModel = vi.fn();

beforeEach(() => {
  getModels.mockResolvedValue([
    {
      id: "deepseek",
      kind: "api",
      label: "DeepSeek",
      base_url: "https://api.deepseek.com",
      model: "deepseek-flash",
      hasKey: true,
    },
    {
      id: "local",
      kind: "local",
      label: "本地 qwen3.5",
      base_url: "http://127.0.0.1:11434/v1",
      model: "qwen3.5:2b",
      hasKey: false,
    },
  ]);
  saveModels.mockResolvedValue({ baseUrl: "http://127.0.0.1:1", token: "t" });
  testModel.mockResolvedValue({ ok: true });
  (window as unknown as { desktop: unknown }).desktop = {
    getModels,
    saveModels,
    testModel,
  };
});

afterEach(() => {
  cleanup();
  delete (window as unknown as { desktop?: unknown }).desktop;
  vi.clearAllMocks();
});

describe("SettingsDialog", () => {
  it("lists configured models and their key state", async () => {
    render(<SettingsDialog onClose={vi.fn()} onSaved={vi.fn()} />);

    expect(await screen.findByText("DeepSeek")).toBeTruthy();
    expect(screen.getByText("本地 qwen3.5")).toBeTruthy();
    expect(screen.getByText(/API · 已配置 Key/)).toBeTruthy();
    expect(screen.getByText(/本地 Ollama/)).toBeTruthy();
  });

  it("adds an API model and saves the list", async () => {
    const onSaved = vi.fn();
    render(<SettingsDialog onClose={vi.fn()} onSaved={onSaved} />);
    await screen.findByText("DeepSeek");

    fireEvent.click(screen.getByText("＋ 添加模型"));
    fireEvent.change(screen.getByLabelText("模型名称"), { target: { value: "我的 API" } });
    fireEvent.change(screen.getByLabelText("模型地址"), {
      target: { value: "https://example.com/v1" },
    });
    fireEvent.change(screen.getByLabelText("模型名"), { target: { value: "foo-1" } });
    fireEvent.change(screen.getByLabelText("API Key"), { target: { value: "sk-x" } });
    fireEvent.click(screen.getByLabelText("支持图片"));
    fireEvent.click(screen.getByText("保存模型"));
    fireEvent.click(screen.getByText("保存"));

    await waitFor(() => expect(saveModels).toHaveBeenCalledTimes(1));
    const saved = saveModels.mock.calls[0][0] as Array<Record<string, unknown>>;
    expect(saved).toHaveLength(3);
    expect(saved[2]).toMatchObject({
      kind: "api",
      label: "我的 API",
      base_url: "https://example.com/v1",
      model: "foo-1",
      api_key: "sk-x",
      supports_vision: true,
    });
    expect(saved[1]).toMatchObject({ id: "local", api_key: "" });
    expect(onSaved).toHaveBeenCalled();
  });

  it("tests a local Ollama connection with the saved address", async () => {
    render(<SettingsDialog onClose={vi.fn()} onSaved={vi.fn()} />);
    await screen.findByText("本地 qwen3.5");

    fireEvent.click(screen.getAllByText("编辑")[1]);
    fireEvent.click(screen.getByText("测试连接"));

    await waitFor(() =>
      expect(testModel).toHaveBeenCalledWith({
        kind: "local",
        baseUrl: "http://127.0.0.1:11434/v1",
        model: "qwen3.5:2b",
        apiKey: "",
      }),
    );
  });

  it("opens the knowledge tab", async () => {
    const api = await import("../api");
    vi.spyOn(api, "getKnowledgeStatus").mockResolvedValue({
      enabled: true,
      indexing: false,
      state: "idle",
      progress: {
        total_files: 0,
        processed_files: 0,
        indexed_documents: 0,
        failed_documents: 0,
        current_file: "",
        started_at: "",
        finished_at: "",
        message: "",
      },
      sources: [],
      documents: 0,
      chunks: 0,
      last_index_at: "",
      embedding: {
        model: "qwen3-embedding:0.6b",
        available: true,
        reason: "",
        warning: "",
        dim: 1024,
      },
    });
    render(<SettingsDialog onClose={() => undefined} onSaved={() => undefined} />);
    fireEvent.click(screen.getByText("知识库"));
    expect(await screen.findByText("添加文件夹")).toBeTruthy();
  });
});
