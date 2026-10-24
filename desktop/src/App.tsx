import { useEffect, useRef, useState, type DragEvent } from "react";
import {
  cancelRun,
  createSession,
  deleteSession,
  getSession,
  initApi,
  listAgents,
  listFiles,
  listModels,
  listSessions,
  patchSessionModel,
  renameSession,
  searchFiles,
  streamMessage,
} from "./api";
import type {
  AgentRecord,
  Attachment,
  FileEntry,
  Message,
  ModelInfo,
  SessionDetail,
  SessionMeta,
  ToolEvent,
} from "./types";
import { markdownToHtml } from "./markdown";

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function groupSessions(sessions: SessionMeta[]) {
  const now = new Date();
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const startYesterday = startToday - 86_400_000;
  const groups: { label: string; items: SessionMeta[] }[] = [
    { label: "今天", items: [] },
    { label: "昨天", items: [] },
    { label: "更早", items: [] },
  ];
  for (const session of sessions) {
    const time = new Date(session.updated_at).getTime();
    if (time >= startToday) groups[0].items.push(session);
    else if (time >= startYesterday) groups[1].items.push(session);
    else groups[2].items.push(session);
  }
  return groups.filter((group) => group.items.length > 0);
}

function timeLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function statusLabel(status: AgentRecord["status"]): string {
  if (status === "running") return "运行中";
  if (status === "succeeded") return "已完成";
  return "失败";
}

function Sidebar(props: {
  sessions: SessionMeta[];
  activeId: string | null;
  query: string;
  onQuery: (value: string) => void;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}) {
  const filtered = props.sessions.filter((session) =>
    session.title.toLowerCase().includes(props.query.trim().toLowerCase()),
  );
  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-dot">◆</span>
        <span>LangGraph Agent</span>
      </div>
      <button className="new-session" onClick={props.onNew}>
        ＋ 新会话
      </button>
      <input
        className="search"
        value={props.query}
        onChange={(event) => props.onQuery(event.target.value)}
        placeholder="搜索会话"
      />
      <div className="session-scroll">
        {groupSessions(filtered).map((group) => (
          <section key={group.label} className="session-group">
            <div className="group-label">{group.label}</div>
            {group.items.map((session) => (
              <div
                key={session.id}
                className={`session-row ${session.id === props.activeId ? "active" : ""}`}
                onClick={() => props.onSelect(session.id)}
              >
                <div className="session-main">
                  <div className="session-title">{session.title || "新会话"}</div>
                  <div className="session-meta">
                    {timeLabel(session.updated_at)} · {session.message_count} 条
                  </div>
                </div>
                <button
                  className="session-delete"
                  title="删除会话"
                  onClick={(event) => {
                    event.stopPropagation();
                    props.onDelete(session.id);
                  }}
                >
                  ×
                </button>
              </div>
            ))}
          </section>
        ))}
        {filtered.length === 0 && <div className="empty-hint">没有匹配的会话</div>}
      </div>
      <div className="sidebar-footer">
        <span className="status-dot" /> 本地服务已连接
      </div>
    </aside>
  );
}

function ToolCard(props: { tool: ToolEvent }) {
  const state = props.tool.status === "error" ? "error" : props.tool.status === "ok" ? "ok" : "running";
  return (
    <div className={`tool-card ${state}`}>
      <div className="tool-head">
        <span className="tool-name">{props.tool.tool}</span>
        <span className="tool-state">
          {state === "running" ? "执行中" : state === "ok" ? "完成" : "失败"}
          {props.tool.duration_ms ? ` · ${props.tool.duration_ms}ms` : ""}
        </span>
      </div>
      {props.tool.args && Object.keys(props.tool.args).length > 0 && (
        <div className="tool-args">
          {Object.entries(props.tool.args).map(([key, value]) => (
            <div key={key}>
              <span>{key}</span>
              <code>{String(value).slice(0, 160)}</code>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function formatCount(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}m`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(Math.max(0, Math.round(value)));
}

function estimateContext(messages: Message[]): number {
  const chars = messages.reduce(
    (sum, message) =>
      sum + message.content.length + (message.attachments?.length ?? 0) * 4_000,
    0,
  );
  return Math.round(chars / 4);
}

function Chat(props: {
  session: SessionDetail | null;
  messages: Message[];
  models: ModelInfo[];
  input: string;
  streaming: boolean;
  streamText: string;
  runId: string | null;
  error: string;
  attachments: Attachment[];
  onInput: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  onModel: (key: string) => void;
  onNew: () => void;
  onInsertPaths: (paths: string[]) => void;
  onAttachImages: (files: File[]) => void;
  onRemoveAttachment: (index: number) => void;
  onRename: (title: string) => void;
}) {
  const endRef = useRef<HTMLDivElement | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [dragging, setDragging] = useState(false);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
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
            {model?.context_window
              ? ` · 上下文 ≈ ${formatCount(estimateContext(props.messages))} / ${formatCount(
                  model.context_window,
                )} tokens`
              : ""}
          </div>
        </div>
        <div className="chat-actions">
          <span className={`live-dot ${props.streaming ? "on" : ""}`} />
          {props.streaming ? "生成中" : "就绪"}
        </div>
      </header>

      <div className="message-scroll">
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
        <div ref={endRef} />
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
            onChange={(event) => props.onModel(event.target.value)}
            disabled={props.streaming}
            title="切换模型"
          >
            {props.models.map((item) => (
              <option key={item.key} value={item.key} disabled={!item.available}>
                {item.label}
                {!item.available ? "（未配置）" : ""}
              </option>
            ))}
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

function FileNode(props: {
  entry: FileEntry;
  depth: number;
  onInsert: (path: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [children, setChildren] = useState<FileEntry[] | null>(null);
  const [loading, setLoading] = useState(false);
  const indent = 10 + props.depth * 14;

  async function toggle() {
    if (props.entry.type === "file") {
      props.onInsert(props.entry.path);
      return;
    }
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (children !== null) return;
    setLoading(true);
    try {
      setChildren(await listFiles(props.entry.path));
    } catch {
      setChildren([]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <button
        className={`file-row ${props.entry.type}`}
        style={{ paddingLeft: indent }}
        onClick={() => void toggle()}
        title={props.entry.path}
      >
        <span className="file-icon">
          {props.entry.type === "dir" ? (open ? "▾" : "▸") : "·"}
        </span>
        <span className="file-name">{props.entry.name}</span>
      </button>
      {open && (
        <div>
          {loading && <div className="file-loading" style={{ paddingLeft: indent + 14 }}>加载中…</div>}
          {children?.map((child) => (
            <FileNode
              key={child.path}
              entry={child}
              depth={props.depth + 1}
              onInsert={props.onInsert}
            />
          ))}
          {children?.length === 0 && (
            <div className="file-loading" style={{ paddingLeft: indent + 14 }}>空目录</div>
          )}
        </div>
      )}
    </div>
  );
}

function FilePanel(props: { onInsert: (path: string) => void }) {
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<FileEntry[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    void (async () => {
      try {
        setEntries(await listFiles("."));
      } catch (cause) {
        setError(errorText(cause));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  useEffect(() => {
    const value = query.trim();
    if (!value) {
      setResults(null);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const found = await searchFiles(value);
          if (!cancelled) setResults(found);
        } catch {
          if (!cancelled) setResults([]);
        }
      })();
    }, 180);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query]);

  return (
    <div className="file-panel">
      <input
        className="file-search"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="搜索工作区文件"
      />
      {error && <div className="file-error">{error}</div>}
      {loading && <div className="file-loading">加载文件树…</div>}
      <div className="file-tree">
        {results !== null
          ? results.map((entry) => (
              <button
                key={entry.path}
                className="file-row file"
                onClick={() => props.onInsert(entry.path)}
                title={entry.path}
              >
                <span className="file-icon">·</span>
                <span className="file-name">{entry.path}</span>
              </button>
            ))
          : entries.map((entry) => (
              <FileNode key={entry.path} entry={entry} depth={0} onInsert={props.onInsert} />
            ))}
        {results !== null && results.length === 0 && (
          <div className="file-loading">没有匹配的文件</div>
        )}
      </div>
      <div className="file-hint">点击文件插入 @路径；也可以把文件拖进输入框。</div>
    </div>
  );
}

function RightPanel(props: {
  session: SessionDetail | null;
  agents: AgentRecord[];
  tools: ToolEvent[];
  onInsertPath: (path: string) => void;
}) {
  const [tab, setTab] = useState<"tasks" | "tools" | "context" | "files">("tasks");
  const running = props.agents.filter((agent) => agent.status === "running").length;
  return (
    <aside className="right-panel">
      <div className="tabs">
        {(["tasks", "tools", "context", "files"] as const).map((item) => (
          <button
            key={item}
            className={tab === item ? "active" : ""}
            onClick={() => setTab(item)}
          >
            {item === "tasks"
              ? "任务"
              : item === "tools"
                ? "工具"
                : item === "context"
                  ? "上下文"
                  : "文件"}
            {item === "tasks" && running > 0 ? <span className="badge">{running}</span> : null}
          </button>
        ))}
      </div>

      {tab === "tasks" && (
        <div className="panel-body">
          {props.agents.length === 0 && <div className="empty-hint">还没有子 agent 任务</div>}
          {props.agents.map((agent) => (
            <div key={agent.id} className={`agent-card ${agent.status}`}>
              <div className="agent-head">
                <span className="agent-task">{agent.task}</span>
                <span className="agent-state">{statusLabel(agent.status)}</span>
              </div>
              <div className="agent-meta">
                {agent.id} · {timeLabel(agent.created_at)}
              </div>
              {agent.error && <div className="agent-error">{agent.error}</div>}
              {agent.result && <div className="agent-result">{agent.result.slice(0, 180)}</div>}
            </div>
          ))}
        </div>
      )}

      {tab === "tools" && (
        <div className="panel-body">
          {props.tools.length === 0 && <div className="empty-hint">本轮还没有工具调用</div>}
          {props.tools
            .slice()
            .reverse()
            .map((tool) => (
              <ToolCard key={`${tool.call_id}-${tool.tool}`} tool={tool} />
            ))}
        </div>
      )}

      {tab === "context" && (
        <div className="panel-body context-list">
          <div>模型：{props.session?.model_key ?? "未选择"}</div>
          <div>会话：{props.session?.id ?? "未选择"}</div>
          <div>消息数：{props.session?.message_count ?? 0}</div>
          <div>工具调用：{props.tools.length}</div>
          <div>子 agent：{props.agents.length}</div>
          <div className="context-note">上下文由 Python 会话文件持久化；刷新后仍保留。</div>
        </div>
      )}

      {tab === "files" && <FilePanel onInsert={props.onInsertPath} />}
    </aside>
  );
}

function readImageFile(file: File): Promise<Attachment> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () =>
      resolve({
        type: "image",
        data_url: String(reader.result || ""),
        name: file.name,
      });
    reader.onerror = () => reject(new Error(`读取图片失败：${file.name}`));
    reader.readAsDataURL(file);
  });
}

export function App() {
  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [query, setQuery] = useState("");
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamText, setStreamText] = useState("");
  const [runId, setRunId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [tools, setTools] = useState<ToolEvent[]>([]);
  const [agents, setAgents] = useState<AgentRecord[]>([]);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const abortRef = useRef<AbortController | null>(null);

  const currentModel =
    models.find((item) => item.key === detail?.model_key) ?? models[0];

  async function refreshSessions(): Promise<SessionMeta[]> {
    const list = await listSessions();
    setSessions(list);
    return list;
  }

  async function selectSession(id: string): Promise<void> {
    const data = await getSession(id);
    setActiveId(id);
    setDetail(data);
    setMessages(data.messages);
    setStreamText("");
    setError("");
    setAttachments([]);
  }

  async function newSession(): Promise<void> {
    const created = await createSession("新会话", detail?.model_key);
    setSessions((prev) => [created, ...prev]);
    setActiveId(created.id);
    setDetail(created);
    setMessages([]);
    setStreamText("");
    setError("");
    setAttachments([]);
  }

  async function removeSession(id: string): Promise<void> {
    if (!window.confirm("删除这个会话？")) return;
    await deleteSession(id);
    const remaining = sessions.filter((item) => item.id !== id);
    setSessions(remaining);
    if (activeId === id) {
      if (remaining.length > 0) await selectSession(remaining[0].id);
      else await newSession();
    }
  }

  async function loadAgents(): Promise<void> {
    try {
      setAgents(await listAgents());
    } catch {
      // 右栏状态不是主流程，静默失败
    }
  }

  async function renameCurrent(title: string): Promise<void> {
    if (!detail) return;
    try {
      const result = await renameSession(detail.id, title);
      setDetail((previous) => (previous ? { ...previous, title: result.title } : previous));
      setSessions((previous) =>
        previous.map((session) =>
          session.id === detail.id ? { ...session, title: result.title } : session,
        ),
      );
    } catch (cause) {
      setError(errorText(cause));
    }
  }

  async function changeModel(key: string): Promise<void> {
    if (!detail || key === detail.model_key) return;
    try {
      const result = await patchSessionModel(detail.id, key);
      const next = { ...detail, model_key: result.model_key as SessionDetail["model_key"] };
      setDetail(next);
      setSessions((prev) =>
        prev.map((session) =>
          session.id === detail.id
            ? { ...session, model_key: result.model_key as SessionMeta["model_key"] }
            : session,
        ),
      );
    } catch (cause) {
      setError(errorText(cause));
    }
  }

  async function addImages(files: File[]): Promise<void> {
    const images = files.filter((file) => file.type.startsWith("image/"));
    if (images.length === 0) return;
    if (!currentModel?.supports_vision) {
      setError("当前模型不支持图片，请切换到千问 VL");
      return;
    }
    const room = 4 - attachments.length;
    if (room <= 0) {
      setError("一次最多附加 4 张图片");
      return;
    }
    try {
      const next = await Promise.all(images.slice(0, room).map(readImageFile));
      setAttachments((previous) => [...previous, ...next]);
      setError("");
    } catch (cause) {
      setError(errorText(cause));
    }
  }

  async function send(): Promise<void> {
    const text = input.trim();
    if (!detail || (!text && attachments.length === 0) || streaming) return;
    if (attachments.length > 0 && !currentModel?.supports_vision) {
      setError("当前模型不支持图片，请切换到千问 VL");
      return;
    }
    const outgoing = attachments;
    setInput("");
    setAttachments([]);
    setError("");
    setMessages((prev) => [...prev, { role: "user", content: text, attachments: outgoing }]);
    setStreaming(true);
    setStreamText("");
    setTools([]);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      for await (const event of streamMessage(detail.id, text, controller.signal, outgoing)) {
        if (event.event === "token") {
          const id = String(event.data.run_id ?? "");
          if (id) setRunId(id);
          setStreamText((prev) => prev + String(event.data.text ?? ""));
        } else if (event.event === "tool_start") {
          setTools((prev) => [
            ...prev,
            {
              tool: String(event.data.tool ?? "tool"),
              call_id: String(event.data.call_id ?? ""),
              args: (event.data.args as Record<string, unknown>) ?? {},
            },
          ]);
        } else if (event.event === "tool_end") {
          setTools((prev) =>
            prev.map((tool) =>
              tool.call_id === event.data.call_id
                ? {
                    ...tool,
                    status: String(event.data.status ?? "ok"),
                    duration_ms: Number(event.data.duration_ms ?? 0),
                  }
                : tool,
            ),
          );
        } else if (event.event === "error") {
          setError(String(event.data.message ?? "模型调用失败"));
        }
      }
    } catch (cause) {
      if (!(cause instanceof DOMException && cause.name === "AbortError")) {
        setError(errorText(cause));
      }
    } finally {
      setStreaming(false);
      setStreamText("");
      setRunId(null);
      abortRef.current = null;
      try {
        const updated = await getSession(detail.id);
        setDetail(updated);
        setMessages(updated.messages);
      } catch {
        // 保留当前消息
      }
      await refreshSessions().catch(() => undefined);
      await loadAgents();
    }
  }

  function stop(): void {
    if (runId) void cancelRun(runId).catch(() => undefined);
    abortRef.current?.abort();
    setStreaming(false);
    setStreamText("");
    setRunId(null);
  }

  function insertPaths(paths: string[]): void {
    const refs = paths.map((path) => `@${path}`).join(" ");
    setInput((previous) => {
      if (!previous) return `${refs} `;
      return `${previous}${previous.endsWith(" ") ? "" : " "}${refs} `;
    });
  }

  useEffect(() => {
    void (async () => {
      try {
        await initApi();
        const [sessionList, modelList] = await Promise.all([listSessions(), listModels()]);
        setModels(modelList);
        setSessions(sessionList);
        if (sessionList.length > 0) {
          await selectSession(sessionList[0].id);
        } else {
          const created = await createSession("新会话");
          setSessions([created]);
          setActiveId(created.id);
          setDetail(created);
          setMessages([]);
        }
      } catch (cause) {
        setError(errorText(cause));
      }
    })();
  }, []);

  useEffect(() => {
    void loadAgents();
    const timer = window.setInterval(() => void loadAgents(), 2000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className="app-shell">
      <Sidebar
        sessions={sessions}
        activeId={activeId}
        query={query}
        onQuery={setQuery}
        onSelect={(id) => void selectSession(id)}
        onNew={() => void newSession()}
        onDelete={(id) => void removeSession(id)}
      />
      <Chat
        session={detail}
        messages={messages}
        models={models}
        input={input}
        streaming={streaming}
        streamText={streamText}
        runId={runId}
        error={error}
        attachments={attachments}
        onInput={setInput}
        onSend={() => void send()}
        onStop={stop}
        onModel={(key) => void changeModel(key)}
        onNew={() => void newSession()}
        onInsertPaths={insertPaths}
        onAttachImages={(files) => void addImages(files)}
        onRemoveAttachment={(index) =>
          setAttachments((previous) => previous.filter((_, i) => i !== index))
        }
        onRename={(title) => void renameCurrent(title)}
      />
      <RightPanel
        session={detail}
        agents={agents}
        tools={tools}
        onInsertPath={(path) => insertPaths([path])}
      />
    </div>
  );
}
