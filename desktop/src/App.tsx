import { useEffect, useRef, useState } from "react";
import {
  addMcpServer,
  cancelAgent,
  cancelRun,
  compactSession,
  createSession,
  deleteSession,
  getModelHealth,
  getSession,
  initApi,
  listAgents,
  listMcpCatalog,
  listMcpServers,
  listModels,
  listSessions,
  patchSessionModel,
  removeMcpServer,
  renameSession,
  restartMcpServer,
  resolveApproval,
  setMcpEnabled,
  setMcpPermission,
  streamMessage,
  undoSession,
} from "./api";
import { Chat } from "./components/Chat";
import { RightPanel } from "./components/RightPanel";
import { SettingsDialog } from "./components/SettingsDialog";
import { Sidebar } from "./components/Sidebar";
import { errorText, readImageFile } from "./helpers";
import type {
  AgentRecord,
  ApprovalRequest,
  Attachment,
  McpCatalogItem,
  McpServerStatus,
  Message,
  ModelInfo,
  SessionDetail,
  SessionMeta,
  ToolEvent,
} from "./types";

const HISTORY_PAGE = 200;

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
  const [mcpServers, setMcpServers] = useState<McpServerStatus[]>([]);
  const [mcpCatalog, setMcpCatalog] = useState<McpCatalogItem[]>([]);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [approval, setApproval] = useState<ApprovalRequest | null>(null);
  const [undoAvailable, setUndoAvailable] = useState(false);
  const [undoing, setUndoing] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [compacting, setCompacting] = useState(false);
  const [fallback, setFallback] = useState<{
    from: string;
    to: string;
    label: string;
    reason?: string;
  } | null>(null);
  const [networkBack, setNetworkBack] = useState(false);
  const [switchingBack, setSwitchingBack] = useState(false);
  const [earlier, setEarlier] = useState<{ hasMore: boolean; nextBefore: number | null }>({
    hasMore: false,
    nextBefore: null,
  });
  const abortRef = useRef<AbortController | null>(null);
  const streamSeq = useRef(0);
  const viewSeq = useRef(0);

  const currentModel =
    models.find((item) => item.key === detail?.model_key) ?? models[0];

  async function refreshSessions(): Promise<SessionMeta[]> {
    const list = await listSessions();
    setSessions(list);
    return list;
  }

  function cancelActiveStream(): void {
    streamSeq.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
    setStreamText("");
    setRunId(null);
    setTools([]);
    setApproval(null);
  }

  async function selectSession(id: string): Promise<void> {
    const seq = ++viewSeq.current;
    cancelActiveStream();
    const data = await getSession(id, { limit: HISTORY_PAGE });
    if (seq !== viewSeq.current) return;
    setActiveId(id);
    setDetail(data);
    setMessages(data.messages);
    setEarlier({ hasMore: Boolean(data.has_more), nextBefore: data.next_before ?? null });
    setStreamText("");
    setError("");
    setFallback(null);
    setNetworkBack(false);
    setAttachments([]);
    setApproval(null);
    setTools([]);
    setUndoAvailable(false);
  }

  async function loadEarlier(): Promise<void> {
    if (!detail || !earlier.hasMore || earlier.nextBefore === null) return;
    const sessionId = detail.id;
    try {
      const page = await getSession(sessionId, {
        limit: HISTORY_PAGE,
        before: earlier.nextBefore,
      });
      setMessages((prev) => [...page.messages, ...prev]);
      setEarlier({
        hasMore: Boolean(page.has_more),
        nextBefore: page.next_before ?? null,
      });
    } catch (cause) {
      setError(errorText(cause));
    }
  }

  async function newSession(): Promise<void> {
    const seq = ++viewSeq.current;
    cancelActiveStream();
    const created = await createSession("新会话", detail?.model_key);
    if (seq !== viewSeq.current) return;
    setSessions((prev) => [created, ...prev]);
    setActiveId(created.id);
    setDetail(created);
    setMessages([]);
    setEarlier({ hasMore: false, nextBefore: null });
    setStreamText("");
    setError("");
    setFallback(null);
    setNetworkBack(false);
    setAttachments([]);
    setApproval(null);
    setTools([]);
    setUndoAvailable(false);
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
      setAgents(await listAgents(activeId ?? undefined));
    } catch {
      // 右栏状态不是主流程，静默失败
    }
  }

  async function cancelAgentAndRefresh(id: string): Promise<void> {
    try {
      await cancelAgent(id);
      await loadAgents();
    } catch (cause) {
      setError(errorText(cause));
    }
  }

  async function refreshMcp(): Promise<void> {
    try {
      const [servers, catalog] = await Promise.all([listMcpServers(), listMcpCatalog()]);
      setMcpServers(servers);
      setMcpCatalog(catalog);
    } catch {
      // MCP 失败不影响主界面
    }
  }

  async function addMcp(presetId: string, env: Record<string, string>): Promise<void> {
    await addMcpServer(presetId, env);
    await refreshMcp();
  }

  async function removeMcp(id: string): Promise<void> {
    await removeMcpServer(id);
    await refreshMcp();
  }

  async function setPermission(id: string, permission: string): Promise<void> {
    await setMcpPermission(id, permission);
    await refreshMcp();
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

  async function executeRun(options: {
    sessionId: string;
    text: string;
    outgoing: Attachment[];
    mode?: "regenerate";
    editIndex?: number;
  }): Promise<void> {
    const { sessionId, text, outgoing } = options;
    const seq = ++streamSeq.current;
    setError("");
    setStreaming(true);
    setStreamText("");
    setTools([]);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      for await (const event of streamMessage(sessionId, text, controller.signal, outgoing, {
        mode: options.mode,
        editIndex: options.editIndex,
      })) {
        if (seq !== streamSeq.current) break;
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
        } else if (event.event === "approval_request") {
          setApproval(event.data as unknown as ApprovalRequest);
        } else if (event.event === "undo_available") {
          setUndoAvailable(true);
        } else if (event.event === "model_fallback") {
          setFallback({
            from: String(event.data.from ?? ""),
            to: String(event.data.to ?? ""),
            label: String(event.data.label ?? ""),
            reason: String(event.data.reason ?? ""),
          });
          setNetworkBack(false);
          setStreamText("");
          setTools([]);
        } else if (event.event === "error") {
          setError(String(event.data.message ?? "模型调用失败"));
        }
      }
    } catch (cause) {
      if (seq === streamSeq.current && !(cause instanceof DOMException && cause.name === "AbortError")) {
        setError(errorText(cause));
      }
    } finally {
      if (seq !== streamSeq.current) return;
      setStreaming(false);
      setStreamText("");
      setRunId(null);
      abortRef.current = null;
      try {
        const updated = await getSession(sessionId);
        if (seq !== streamSeq.current) return;
        setDetail(updated);
        setMessages(updated.messages);
      } catch {
        // 保留当前消息
      }
      await refreshSessions().catch(() => undefined);
      await loadAgents();
    }
  }

  async function send(): Promise<void> {
    const text = input.trim();
    if (!detail || (!text && attachments.length === 0) || streaming || abortRef.current) return;
    if (attachments.length > 0 && !currentModel?.supports_vision) {
      setError("当前模型不支持图片，请切换到千问 VL");
      return;
    }
    const outgoing = attachments;
    const sessionId = detail.id;
    setInput("");
    setAttachments([]);
    setMessages((prev) => [...prev, { role: "user", content: text, attachments: outgoing }]);
    await executeRun({ sessionId, text, outgoing });
  }

  async function regenerate(): Promise<void> {
    if (!detail || streaming || abortRef.current) return;
    setMessages((prev) => {
      const lastUser = prev.map((message) => message.role).lastIndexOf("user");
      return lastUser >= 0 ? prev.slice(0, lastUser + 1) : prev;
    });
    await executeRun({ sessionId: detail.id, text: "", outgoing: [], mode: "regenerate" });
  }

  async function editAndResend(index: number, text: string): Promise<void> {
    const value = text.trim();
    if (!detail || streaming || abortRef.current || !value) return;
    setMessages((prev) =>
      prev
        .slice(0, index + 1)
        .map((message, i) =>
          i === index ? { ...message, content: value, attachments: [] } : message,
        ),
    );
    await executeRun({
      sessionId: detail.id,
      text: value,
      outgoing: [],
      editIndex: index,
    });
  }

  async function decideApproval(approved: boolean): Promise<void> {
    if (!approval) return;
    const current = approval;
    setApproval(null);
    try {
      await resolveApproval(current.run_id, current.approval_id, approved);
    } catch (cause) {
      setError(errorText(cause));
    }
  }

  async function compactCurrent(): Promise<void> {
    if (!detail || streaming || compacting) return;
    const confirmed = window.confirm(
      "将把较早的消息压缩成摘要，最近 10 条保留原文。压缩后无法还原原始消息，继续？",
    );
    if (!confirmed) return;
    setCompacting(true);
    try {
      const updated = await compactSession(detail.id);
      setDetail(updated);
      setMessages(updated.messages);
      setError("");
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setCompacting(false);
    }
  }

  async function undoLastWrite(): Promise<void> {
    if (!detail || undoing) return;
    setUndoing(true);
    try {
      await undoSession(detail.id);
      setUndoAvailable(false);
      setError("");
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setUndoing(false);
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
  }, [activeId]);

  useEffect(() => {
    void refreshMcp();
    const timer = window.setInterval(() => void refreshMcp(), 3000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!fallback) return;
    let cancelled = false;
    const check = async () => {
      try {
        const health = await getModelHealth(fallback.from);
        if (!cancelled) setNetworkBack(health.reachable);
      } catch {
        // 网络探测失败时保持当前状态
      }
    };
    void check();
    const timer = window.setInterval(check, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [fallback?.from, fallback?.to]);

  async function switchBack(): Promise<void> {
    if (!detail || !fallback || streaming) return;
    setSwitchingBack(true);
    try {
      await changeModel(fallback.from);
      setFallback(null);
      setNetworkBack(false);
    } finally {
      setSwitchingBack(false);
    }
  }

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
        onSettings={() => setSettingsOpen(true)}
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
        approval={approval}
        undoAvailable={undoAvailable}
        undoing={undoing}
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
        onApproval={(approved) => void decideApproval(approved)}
        onUndo={() => void undoLastWrite()}
        onRegenerate={() => void regenerate()}
        onEditMessage={(index, text) => void editAndResend(index, text)}
        hasEarlier={earlier.hasMore}
        onLoadEarlier={() => void loadEarlier()}
        onAddModel={() => setSettingsOpen(true)}
        fallback={fallback}
        networkBack={networkBack}
        switchingBack={switchingBack}
        onSwitchBack={() => void switchBack()}
      />
      <RightPanel
        session={detail}
        model={currentModel}
        messages={messages}
        agents={agents}
        tools={tools}
        mcpServers={mcpServers}
        mcpCatalog={mcpCatalog}
        onRestartMcp={(id) => void restartMcpServer(id).then(refreshMcp)}
        onToggleMcp={(id, enabled) => void setMcpEnabled(id, enabled).then(refreshMcp)}
        onAddMcp={addMcp}
        onRemoveMcp={(id) => void removeMcp(id).catch((cause) => setError(errorText(cause)))}
        onMcpPermission={(id, permission) =>
          void setPermission(id, permission).catch((cause) => setError(errorText(cause)))
        }
        onInsertPath={(path) => insertPaths([path])}
        onCancelAgent={(id) => void cancelAgentAndRefresh(id)}
        onCompact={() => void compactCurrent()}
        compacting={compacting}
      />
      {settingsOpen && (
        <SettingsDialog
          onClose={() => setSettingsOpen(false)}
          onSaved={() => {
            setSettingsOpen(false);
            void (async () => {
              try {
                await initApi();
                setModels(await listModels());
              } catch (cause) {
                setError(errorText(cause));
              }
            })();
          }}
        />
      )}
    </div>
  );
}
