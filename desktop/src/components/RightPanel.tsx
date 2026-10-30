import { useEffect, useState } from "react";

import { listFiles, searchFiles } from "../api";
import { errorText, estimateContext, formatCount, statusLabel, timeLabel } from "../helpers";
import type {
  AgentRecord,
  FileEntry,
  McpCatalogItem,
  McpServerStatus,
  Message,
  ModelInfo,
  SessionDetail,
  ToolEvent,
} from "../types";
import { McpPanel } from "./McpPanel";

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

export function RightPanel(props: {
  session: SessionDetail | null;
  model?: ModelInfo;
  messages: Message[];
  agents: AgentRecord[];
  tools: ToolEvent[];
  mcpServers: McpServerStatus[];
  mcpCatalog: McpCatalogItem[];
  onRestartMcp: (id: string) => void;
  onToggleMcp: (id: string, enabled: boolean) => void;
  onAddMcp: (presetId: string, env: Record<string, string>) => Promise<void>;
  onRemoveMcp: (id: string) => void;
  onMcpPermission: (id: string, permission: string) => void;
  onInsertPath: (path: string) => void;
  onCancelAgent: (id: string) => void;
}) {
  const [tab, setTab] = useState<"tasks" | "tools" | "context" | "files" | "mcp">("tasks");
  const running = props.agents.filter((agent) => agent.status === "running").length;
  const windowSize = props.model?.context_window ?? 0;
  const used = estimateContext(props.messages);
  const percent = windowSize > 0 ? Math.min(100, (used / windowSize) * 100) : 0;
  const messageTokens = (role: Message["role"]) =>
    estimateContext(props.messages.filter((message) => message.role === role));
  const attachmentCount = props.messages.reduce(
    (sum, message) => sum + (message.attachments?.length ?? 0),
    0,
  );
  return (
    <aside className="right-panel">
      <div className="tabs">
        {(["tasks", "tools", "context", "files", "mcp"] as const).map((item) => (
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
                  : item === "files"
                    ? "文件"
                    : "MCP"}
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
              {agent.status === "running" && (
                <button
                  className="agent-cancel"
                  type="button"
                  onClick={() => props.onCancelAgent(agent.id)}
                >
                  取消
                </button>
              )}
              <div className="agent-meta">
                {agent.id} · {timeLabel(agent.created_at)}
              </div>
              {agent.error && agent.status !== "succeeded" && (
                <div className="agent-error">{agent.error}</div>
              )}
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
          <div className="context-summary">
            <div>
              <span>模型</span>
              <strong>{props.model?.label ?? props.session?.model_key ?? "未选择"}</strong>
            </div>
            <div>
              <span>上下文估算</span>
              <strong>
                {formatCount(used)} / {windowSize ? formatCount(windowSize) : "未知"}
              </strong>
            </div>
          </div>
          {windowSize > 0 && (
            <>
              <div className="context-bar" title={`${percent.toFixed(1)}%`}>
                <span style={{ width: `${percent}%` }} />
              </div>
              <div className="context-remaining">
                已用 {percent.toFixed(1)}% · 剩余 {formatCount(Math.max(0, windowSize - used))} tokens
              </div>
            </>
          )}

          <div className="context-breakdown">
            {(["user", "assistant", "tool"] as const).map((role) => {
              const value = messageTokens(role);
              const label = role === "user" ? "用户消息" : role === "assistant" ? "助手回复" : "工具结果";
              return (
                <div key={role} className="context-row">
                  <span>{label}</span>
                  <div className="context-row-bar">
                    <i style={{ width: `${used ? Math.min(100, (value / used) * 100) : 0}%` }} />
                  </div>
                  <b>{formatCount(value)}</b>
                </div>
              );
            })}
          </div>

          <div className="context-stats">
            <div>
              <b>{props.session?.message_count ?? 0}</b>
              <span>消息</span>
            </div>
            <div>
              <b>{props.tools.length}</b>
              <span>工具调用</span>
            </div>
            <div>
              <b>{attachmentCount}</b>
              <span>图片</span>
            </div>
            <div>
              <b>{props.agents.length}</b>
              <span>子 agent</span>
            </div>
          </div>

          <div className="context-note">
            上下文按“字符数 ÷ 4”估算，不含系统提示词；精确 token 以模型服务商为准。
          </div>
        </div>
      )}

      {tab === "files" && <FilePanel onInsert={props.onInsertPath} />}
      {tab === "mcp" && (
        <McpPanel
          servers={props.mcpServers}
          catalog={props.mcpCatalog}
          onRestart={props.onRestartMcp}
          onToggle={props.onToggleMcp}
          onAdd={props.onAddMcp}
          onRemove={props.onRemoveMcp}
          onPermission={props.onMcpPermission}
        />
      )}
    </aside>
  );
}
