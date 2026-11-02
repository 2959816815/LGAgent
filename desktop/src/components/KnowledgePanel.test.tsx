import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => {
  const status = {
    enabled: true,
    indexing: false,
    state: "done",
    progress: {
      total_files: 2,
      processed_files: 2,
      indexed_documents: 2,
      failed_documents: 0,
      current_file: "",
      started_at: "",
      finished_at: "",
      message: "",
    },
    sources: [{ id: 1, path: "D:/资料", document_count: 2, enabled: true }],
    documents: 2,
    chunks: 8,
    last_index_at: "2026-11-02T10:00:00+08:00",
    embedding: {
      model: "qwen3-embedding:0.6b",
      available: true,
      reason: "",
      warning: "",
      dim: 1024,
    },
  };
  return {
    getKnowledgeStatus: vi.fn(async () => status),
    addKnowledgeSource: vi.fn(async () => status),
    removeKnowledgeSource: vi.fn(async () => status),
    startKnowledgeIndex: vi.fn(async () => ({ ...status, started: true })),
    cancelKnowledgeIndex: vi.fn(async () => status),
    setKnowledgeEnabled: vi.fn(async () => ({ ...status, enabled: false })),
  };
});

import { KnowledgePanel } from "./KnowledgePanel";
import {
  addKnowledgeSource,
  setKnowledgeEnabled,
  startKnowledgeIndex,
} from "../api";

beforeEach(() => {
  window.desktop = {
    pickFolder: vi.fn(async () => "D:/资料"),
  } as unknown as Window["desktop"];
});

afterEach(cleanup);

describe("KnowledgePanel", () => {
  it("adds a folder through the native picker", async () => {
    render(<KnowledgePanel />);
    fireEvent.click(await screen.findByText("添加文件夹"));

    await waitFor(() =>
      expect(addKnowledgeSource).toHaveBeenCalledWith("D:/资料"),
    );
  });

  it("toggles the global switch", async () => {
    render(<KnowledgePanel />);
    fireEvent.click(await screen.findByLabelText(/启用知识库/));

    await waitFor(() =>
      expect(setKnowledgeEnabled).toHaveBeenCalledWith(false),
    );
  });

  it("starts an incremental index", async () => {
    render(<KnowledgePanel />);
    fireEvent.click(await screen.findByText("更新索引"));

    await waitFor(() => expect(startKnowledgeIndex).toHaveBeenCalledWith(false));
  });
});
