import { useEffect, useRef, useState } from "react";
import {
  cancelRun,
  createSession,
  deleteSession,
  getSession,
  initApi,
  listAgents,
  listModels,
  listSessions,
  patchSessionModel,
  streamMessage,
} from "./api";
import type {
  AgentRecord,
  Message,
  ModelInfo,
  SessionDetail,
  SessionMeta,
  ToolEvent,
} from "./types";

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

function Chat(props: {
  session: SessionDetail | null;
  messages: Message[];
  models: ModelInfo[];
  input: string;
  streaming: boolean;
  streamText: string;
  runId: string | null;
  error: string;
  onInput: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  onModel: (key: string) => void;
  onNew: () => void;
}) {
  const endRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [props.messages, props.streamText]);

  const model =
    props.models.find((item) => item.key === props.session?.model_key) ?? props.models[0];

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

  return (
    <main className="chat-column">
      <header className="chat-header">
        <div>
          <div className="chat-title">{props.session.title}</div>
          <div className="chat-subtitle">
            {props.session.message_count} 条消息 · {model?.label ?? props.session.model_key}
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
                <pre>{message.content}</pre>
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
              {props.streamText ? <pre>{props.streamText}</pre> : <div className="typing">正在思考…</div>}
            </div>
          </div>
        )}
        {props.error && <div className="chat-error">{props.error}</div>}
        <div ref={endRef} />
      </div>

      <footer className="composer">
        <textarea
          value={props.input}
          onChange={(event) => props.onInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              props.onSend();
            }
          }}
          placeholder="输入消息，Enter 发送，Shift+Enter 换行"
          rows={2}
        />
        <div className="composer-bar">
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
            <button className="send" onClick={props.onSend} disabled={!props.input.trim()}>
              发送
            </button>
          )}
        </div>
      </footer>
    </main>
  );
}

function RightPanel(props: {
  session: SessionDetail | null;
  agents: AgentRecord[];
  tools: ToolEvent[];
}) {
  const [tab, setTab] = useState<"tasks" | "tools" | "context">("tasks");
  const running = props.agents.filter((agent) => agent.status === "running").length;
  return (
    <aside className="right-panel">
      <div className="tabs">
        {(["tasks", "tools", "context"] as const).map((item) => (
          <button
            key={item}
            className={tab === item ? "active" : ""}
            onClick={() => setTab(item)}
          >
            {item === "tasks" ? "任务" : item === "tools" ? "工具" : "上下文"}
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
    </aside>
  );
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
  const abortRef = useRef<AbortController | null>(null);

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
  }

  async function newSession(): Promise<void> {
    const created = await createSession("新会话", detail?.model_key);
    setSessions((prev) => [created, ...prev]);
    setActiveId(created.id);
    setDetail(created);
    setMessages([]);
    setStreamText("");
    setError("");
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

  async function send(): Promise<void> {
    const text = input.trim();
    if (!detail || !text || streaming) return;
    setInput("");
    setError("");
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setStreaming(true);
    setStreamText("");
    setTools([]);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      for await (const event of streamMessage(detail.id, text, controller.signal)) {
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
        onInput={setInput}
        onSend={() => void send()}
        onStop={stop}
        onModel={(key) => void changeModel(key)}
        onNew={() => void newSession()}
      />
      <RightPanel session={detail} agents={agents} tools={tools} />
    </div>
  );
}
