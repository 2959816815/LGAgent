import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({
  searchFiles: vi.fn(async () => []),
}));

import { Chat } from "./Chat";

const writeText = vi.fn(async () => undefined);

function makeProps() {
  return {
    session: {
      id: "s1",
      title: "会话",
      created_at: "2026-01-01T00:00:00+08:00",
      updated_at: "2026-01-01T00:00:00+08:00",
      message_count: 2,
      model_key: "local" as const,
      messages: [],
    },
    messages: [
      { role: "user" as const, content: "请复制我" },
      { role: "assistant" as const, content: "回答内容" },
    ],
    models: [
      {
        key: "local" as const,
        label: "本地",
        model: "qwen",
        available: true,
        supports_vision: false,
        context_window: 4096,
      },
    ],
    input: "",
    streaming: false,
    streamText: "",
    runId: null,
    error: "",
    attachments: [],
    approval: null,
    undoAvailable: false,
    undoing: false,
    hasEarlier: false,
    onInput: vi.fn(),
    onSend: vi.fn(),
    onStop: vi.fn(),
    onModel: vi.fn(),
    onNew: vi.fn(),
    onInsertPaths: vi.fn(),
    onAttachImages: vi.fn(),
    onRemoveAttachment: vi.fn(),
    onRename: vi.fn(),
    onApproval: vi.fn(),
    onUndo: vi.fn(),
    onRegenerate: vi.fn(),
    onEditMessage: vi.fn(),
    onLoadEarlier: vi.fn(),
  };
}

beforeEach(() => {
  writeText.mockClear();
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
});

afterEach(cleanup);

describe("Chat", () => {
  it("copies a message to the clipboard", async () => {
    render(<Chat {...makeProps()} />);

    fireEvent.click(screen.getAllByText("复制")[0]);

    expect(writeText).toHaveBeenCalledWith("请复制我");
  });

  it("offers a back-to-bottom button after scrolling up", () => {
    render(<Chat {...makeProps()} />);
    const scroller = document.querySelector(".message-scroll") as HTMLElement;
    Object.defineProperty(scroller, "scrollHeight", { value: 1000, configurable: true });
    Object.defineProperty(scroller, "clientHeight", { value: 400, configurable: true });

    scroller.scrollTop = 100;
    fireEvent.scroll(scroller);

    fireEvent.click(screen.getByText("回到底部"));

    expect(scroller.scrollTop).toBe(1000);
  });
});
