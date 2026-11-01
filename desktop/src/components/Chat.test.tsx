import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({
  searchFiles: vi.fn(async () => []),
}));

import { Chat } from "./Chat";
import type { SessionDetail } from "../types";

const writeText = vi.fn(async () => undefined);

function makeProps() {
  const session: SessionDetail = {
    id: "s1",
    title: "会话",
    created_at: "2026-01-01T00:00:00+08:00",
    updated_at: "2026-01-01T00:00:00+08:00",
    message_count: 2,
    model_key: "local",
    messages: [],
    summary: "",
    summary_count: 0,
    summary_upto: 0,
  };
  return {
    session,
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
    onAddModel: vi.fn(),
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

  it("shows the compacted summary banner", () => {
    const props = makeProps();
    props.session.summary = "早先的摘要内容";
    props.session.summary_count = 12;
    props.session.summary_upto = 12;

    render(<Chat {...props} />);

    expect(screen.getByText("已摘要 12 条早期消息（原文仍保留）")).toBeTruthy();
    expect(screen.getByText(/早先的摘要内容/)).toBeTruthy();
  });

  it("shows server-provided context usage", () => {
    const props = makeProps();
    props.session.context = {
      raw_tokens: 31000,
      view_tokens: 9800,
      window: 16384,
      tool_tokens: 3200,
      system_tokens: 620,
      summary_tokens: 900,
      recent_turns: 2,
      dropped_turns: 9,
      images_dropped: 0,
      fits: true,
    };

    render(<Chat {...props} />);

    expect(screen.getByText(/9.8k \/ 16.4k tokens/)).toBeTruthy();
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
