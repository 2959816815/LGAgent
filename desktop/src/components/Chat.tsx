import { useEffect, useRef, useState, type DragEvent } from "react";

import { searchFiles } from "../api";
import { copyText, formatCount, lastAssistantIndex } from "../helpers";
import { markdownToHtml } from "../markdown";
import type {
  ApprovalRequest,
  Attachment,
  FileEntry,
  Message,
  ModelInfo,
  SessionDetail,
} from "../types";

export function Chat(props: {
  session: SessionDetail | null;
  messages: Message[];
  models: ModelInfo[];
  input: string;
  streaming: boolean;
  streamText: string;
  runId: string | null;
  error: string;
  attachments: Attachment[];
  approval: ApprovalRequest | null;
  undoAvailable: boolean;
  undoing: boolean;
  onInput: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  onModel: (key: string) => void;
  onNew: () => void;
  onInsertPaths: (paths: string[]) => void;
  onAttachImages: (files: File[]) => void;
  onRemoveAttachment: (index: number) => void;
  onRename: (title: string) => void;
  onApproval: (approved: boolean) => void;
  onUndo: () => void;
  onRegenerate: () => void;
  onEditMessage: (index: number, text: string) => void;
  hasEarlier: boolean;
  onLoadEarlier: () => void;
  onAddModel: () => void;
  fallback?: { from: string; to: string; label: string; reason?: string } | null;
  networkBack?: boolean;
  switchingBack?: boolean;
  onSwitchBack?: () => void;
}) {
  const endRef = useRef<HTMLDivElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [atBottom, setAtBottom] = useState(true);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [dragging, setDragging] = useState(false);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [editingMessage, setEditingMessage] = useState<{ index: number; text: string } | null>(
    null,
  );
  const [mention, setMention] = useState<{ query: string; start: number; end: number } | null>(
    null,
  );
  const [mentionResults, setMentionResults] = useState<FileEntry[]>([]);
  const [mentionIndex, setMentionIndex] = useState(0);
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [props.messages, props.streamText]);

  useEffect(() => {
    if (!mention || !mention.query) {
      setMentionResults([]);
      return;
    }
    let active = true;
    const timer = window.setTimeout(() => {
      void searchFiles(mention.query)
        .then((results) => {
          if (active) setMentionResults(results.slice(0, 8));
        })
        .catch(() => {
          if (active) setMentionResults([]);
        });
    }, 140);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [mention?.query]);

  const model =
    props.models.find((item) => item.key === props.session?.model_key) ?? props.models[0];
  const canAttach = Boolean(model?.supports_vision) && props.attachments.length < 4;
  const sendDisabled =
    props.streaming || (!props.input.trim() && props.attachments.length === 0);

  function changeInput(value: string, cursor: number): void {
    props.onInput(value);
    const before = value.slice(0, cursor);
    const match = before.match(/(?:^|\s)@([^\s@]*)$/);
    if (!match) {
      setMention(null);
      return;
    }
    const query = match[1];
    setMention({ query, start: before.length - query.length - 1, end: cursor });
    setMentionIndex(0);
  }

  function chooseMention(entry: FileEntry): void {
    if (!mention) return;
    const next = `${props.input.slice(0, mention.start)}@${entry.path} ${props.input.slice(
      mention.end,
    )}`;
    props.onInput(next);
    setMention(null);
    setMentionResults([]);
    window.requestAnimationFrame(() => textareaRef.current?.focus());
  }

  function handleScroll(): void {
    const element = scrollRef.current;
    if (!element) return;
    setAtBottom(element.scrollHeight - element.scrollTop - element.clientHeight < 80);
  }

  function scrollToBottom(): void {
    const element = scrollRef.current;
    if (!element) return;
    element.scrollTop = element.scrollHeight;
    setAtBottom(true);
  }

  if (!props.session) {
    return (
      <main className="chat-column empty-chat">
        <div>
          <h1>LangGraph Agent</h1>
          <p>选择或新建一个会话开始。</p>
          <button className="primary" onClick={props.onNew}>
            新建会话
          </button>
        </div>
      </main>
    );
  }

  const session = props.session;

  return (
    <main className="chat-column">
      <header className="chat-header">
        <div>
          {editingTitle ? (
            <input
              className="title-input"
              autoFocus
              value={titleDraft}
              onChange={(event) => setTitleDraft(event.target.value)}
              onBlur={() => {
                setEditingTitle(false);
                if (titleDraft.trim() && titleDraft.trim() !== session.title) {
                  props.onRename(titleDraft.trim());
                }
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter") event.currentTarget.blur();
                if (event.key === "Escape") {
                  setTitleDraft(session.title);
                  setEditingTitle(false);
                }
              }}
            />
          ) : (
            <div
              className="chat-title"
              title="双击重命名"
              onDoubleClick={() => {
                setTitleDraft(session.title);
                setEditingTitle(true);
              }}
            >
              {props.session.title}
            </div>
          )}
          <div className="chat-subtitle">
            {props.session.message_count} 条消息 · {model?.label ?? props.session.model_key}
            {props.session.context && props.session.context.window
              ? ` · 上下文 ≈ ${formatCount(props.session.context.view_tokens)} / ${formatCount(
                  props.session.context.window,
                )} tokens`
              : ""}
          </div>
        </div>
        <div className="chat-actions">
          {props.undoAvailable && (
            <button className="undo-button" onClick={props.onUndo} disabled={props.undoing}>
              {props.undoing ? "撤销中…" : "撤销上次写入"}
            </button>
          )}
          <span className={`live-dot ${props.streaming ? "on" : ""}`} />
          {props.streaming ? "生成中" : "就绪"}
        </div>
      </header>

      {props.fallback && (
        <div className="fallback-banner" role="status">
          <span>
            网络不可用，已自动切换到本地模型
            {props.fallback.label ? `（${props.fallback.label}）` : ""}。
            本地模型只启用核心工具（无 Office / 子 agent / MCP）。
          </span>
          {props.networkBack && (
            <button
              type="button"
              onClick={props.onSwitchBack}
              disabled={props.switchingBack || props.streaming}
            >
              {props.switchingBack
                ? "切换中…"
                : `网络已恢复，切回 ${props.fallback.from}`}
            </button>
          )}
        </div>
      )}

      <div className="message-scroll" ref={scrollRef} onScroll={handleScroll}>
        {session.summary && (
          <details className="compacted-summary">
            <summary>
              已摘要 {session.summary_upto ?? session.summary_count ?? 0} 条早期消息（原文仍保留）
            </summary>
            <pre className="compacted-summary-text">{session.summary}</pre>
          </details>
        )}
        {props.hasEarlier && (
          <div className="load-earlier">
            <button type="button" onClick={props.onLoadEarlier}>
              加载更早的消息
            </button>
          </div>
        )}
        {props.messages.length === 0 && !props.streaming && (
          <div className="welcome">
            <h2>今天想做什么？</h2>
            <p>可以读文件、改 Office 文档、搜索网页，也可以把任务交给子 agent。</p>
          </div>
        )}
        {props.messages.map((message, index) => {
          if (message.role === "tool") {
            return (
              <div key={`tool-${index}`} className="tool-message">
                <span>{message.name || "tool"}</span>
                <pre>{message.content}</pre>
              </div>
            );
          }
          if (message.role === "user" && editingMessage?.index === index) {
            return (
              <div key={`${message.role}-${index}`} className="message user">
                <div className="avatar">你</div>
                <div className="bubble">
                  <textarea
                    className="edit-box"
                    aria-label="编辑消息"
                    value={editingMessage.text}
                    onChange={(event) =>
                      setEditingMessage({ index, text: event.target.value })
                    }
                  />
                  <div className="message-actions">
                    <button
                      type="button"
                      className="primary"
                      disabled={props.streaming || !editingMessage.text.trim()}
                      onClick={() => {
                        const text = editingMessage.text;
                        setEditingMessage(null);
                        props.onEditMessage(index, text);
                      }}
                    >
                      保存并重发
                    </button>
                    <button type="button" onClick={() => setEditingMessage(null)}>
                      取消
                    </button>
                  </div>
                </div>
              </div>
            );
          }
          const isLastAssistant =
            message.role === "assistant" && index === lastAssistantIndex(props.messages);
          return (
            <div key={`${message.role}-${index}`} className={`message ${message.role}`}>
              <div className="avatar">{message.role === "user" ? "你" : "◆"}</div>
              <div className="bubble">
                {message.attachments?.length ? (
                  <div className="message-images">
                    {message.attachments.map((image, imageIndex) => (
                      <img
                        key={`${message.role}-${index}-${imageIndex}`}
                        src={image.data_url}
                        alt={image.name || "图片"}
                      />
                    ))}
                  </div>
                ) : null}
                <div
                  className="markdown"
                  dangerouslySetInnerHTML={{ __html: markdownToHtml(message.content) }}
                />
                {message.tool_calls?.length ? (
                  <div className="tool-calls">
                    {message.tool_calls.map((call) => (
                      <span key={call.id}>{call.name}</span>
                    ))}
                  </div>
                ) : null}
                {message.role === "user" && (
                  <div className="message-actions">
                    <button
                      type="button"
                      disabled={props.streaming}
                      onClick={() => setEditingMessage({ index, text: message.content })}
                    >
                      编辑
                    </button>
                    <button type="button" onClick={() => void copyText(message.content)}>
                      复制
                    </button>
                  </div>
                )}
                {message.role === "assistant" && (
                  <div className="message-actions">
                    <button type="button" onClick={() => void copyText(message.content)}>
                      复制
                    </button>
                    {isLastAssistant && (
                      <button
                        type="button"
                        disabled={props.streaming}
                        onClick={props.onRegenerate}
                      >
                        重新生成
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>
          );
        })}
        {props.streaming && (
          <div className="message assistant">
            <div className="avatar">◆</div>
            <div className="bubble">
              {props.streamText ? (
                <div
                  className="markdown"
                  dangerouslySetInnerHTML={{ __html: markdownToHtml(props.streamText) }}
                />
              ) : (
                <div className="typing">正在思考…</div>
              )}
            </div>
          </div>
        )}
        {props.error && <div className="chat-error">{props.error}</div>}
        {props.approval && (
          <div className="approval-card">
            <div className="approval-head">
              <strong>{props.approval.title}</strong>
              <span className="approval-path">{props.approval.path}</span>
            </div>
            <pre className="approval-diff">
              {props.approval.diff || props.approval.detail || "此操作会修改文件。"}
            </pre>
            <div className="approval-actions">
              <button className="approval-deny" onClick={() => props.onApproval(false)}>
                拒绝
              </button>
              <button className="approval-allow" onClick={() => props.onApproval(true)}>
                允许执行
              </button>
            </div>
          </div>
        )}
        <div ref={endRef} />
        {!atBottom && (
          <div className="scroll-bottom-wrap">
            <button type="button" className="scroll-bottom" onClick={scrollToBottom}>
              回到底部
            </button>
          </div>
        )}
      </div>

      <footer
        className={`composer ${dragging ? "drop-active" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
            setDragging(false);
          }
        }}
        onDrop={(event: DragEvent<HTMLElement>) => {
          event.preventDefault();
          setDragging(false);
          const files = Array.from(event.dataTransfer.files);
          if (files.length === 0) return;
          const images = files.filter((file) => file.type.startsWith("image/"));
          if (images.length > 0) props.onAttachImages(images);
          const others = files.filter((file) => !file.type.startsWith("image/"));
          if (others.length === 0) return;
          const paths = others.map((file) => {
            try {
              return window.desktop?.getPathForFile(file) || file.name;
            } catch {
              return file.name;
            }
          });
          props.onInsertPaths(paths);
        }}
      >
        {props.attachments.length > 0 && (
          <div className="attachment-row">
            {props.attachments.map((image, index) => (
              <div className="attachment-chip" key={`${image.name}-${index}`}>
                <img src={image.data_url} alt={image.name || "图片"} />
                <button
                  type="button"
                  title="移除图片"
                  onClick={() => props.onRemoveAttachment(index)}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
        <div className="composer-input">
          {mention && mentionResults.length > 0 && (
            <div className="mention-popover">
              {mentionResults.map((entry, index) => (
                <button
                  type="button"
                  key={entry.path}
                  className={index === mentionIndex ? "active" : ""}
                  onMouseDown={(event) => {
                    event.preventDefault();
                    chooseMention(entry);
                  }}
                >
                  <span>{entry.type === "dir" ? "▸" : "·"}</span>
                  <span className="mention-path">{entry.path}</span>
                </button>
              ))}
            </div>
          )}
          <textarea
            ref={textareaRef}
            value={props.input}
            onChange={(event) =>
              changeInput(event.target.value, event.target.selectionStart ?? 0)
            }
            onPaste={(event) => {
              const images = Array.from(event.clipboardData.files).filter((file) =>
                file.type.startsWith("image/"),
              );
              if (images.length > 0) {
                event.preventDefault();
                props.onAttachImages(images);
              }
            }}
            onKeyDown={(event) => {
              if (mention && mentionResults.length > 0) {
                if (event.key === "ArrowDown") {
                  event.preventDefault();
                  setMentionIndex((index) => (index + 1) % mentionResults.length);
                  return;
                }
                if (event.key === "ArrowUp") {
                  event.preventDefault();
                  setMentionIndex(
                    (index) => (index - 1 + mentionResults.length) % mentionResults.length,
                  );
                  return;
                }
                if (event.key === "Enter" || event.key === "Tab") {
                  event.preventDefault();
                  chooseMention(mentionResults[mentionIndex]);
                  return;
                }
                if (event.key === "Escape") {
                  event.preventDefault();
                  setMention(null);
                  return;
                }
              }
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                if (!sendDisabled) props.onSend();
              }
            }}
            placeholder="输入消息，Enter 发送，Shift+Enter 换行；可拖入文件、粘贴截图或输入 @ 引用文件"
            rows={2}
          />
        </div>
        <div className="composer-bar">
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            multiple
            hidden
            onChange={(event) => {
              props.onAttachImages(Array.from(event.target.files || []));
              event.target.value = "";
            }}
          />
          <button
            type="button"
            className="attach-button"
            title={model?.supports_vision ? "添加图片" : "当前模型不支持图片"}
            disabled={!canAttach}
            onClick={() => fileRef.current?.click()}
          >
            + 图片
          </button>
          <select
            className="model-pill"
            value={props.session.model_key}
            onChange={(event) => {
              const value = event.target.value;
              if (value === "__add__") {
                props.onAddModel();
                return;
              }
              props.onModel(value);
            }}
            disabled={props.streaming}
            title="切换模型"
          >
            {props.models.map((item) => (
              <option key={item.key} value={item.key} disabled={!item.available}>
                {item.label}
                {!item.available ? "（未配置）" : ""}
              </option>
            ))}
            <option value="__add__">＋ 添加模型…</option>
          </select>
          <span className="composer-hint">{props.streaming ? "可随时停止" : "本地优先，API 兜底"}</span>
          {props.streaming ? (
            <button className="stop" onClick={props.onStop}>
              停止
            </button>
          ) : (
            <button className="send" onClick={props.onSend} disabled={sendDisabled}>
              发送
            </button>
          )}
        </div>
      </footer>
    </main>
  );
}
