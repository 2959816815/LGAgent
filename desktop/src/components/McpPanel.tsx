import type { McpServerStatus } from "../types";

const STATUS_LABEL: Record<string, string> = {
  starting: "启动中",
  running: "运行中",
  ready: "就绪",
  failed: "失败",
  stopped: "已停止",
  disabled: "已禁用",
};

export function McpPanel(props: {
  servers: McpServerStatus[];
  onRestart: (id: string) => void;
  onToggle: (id: string, enabled: boolean) => void;
}) {
  return (
    <div className="panel-body mcp-list">
      <div className="mcp-note">
        MCP 是可选能力；失败不会影响文件、Office、会话和本地模型。
      </div>
      {props.servers.length === 0 && <div className="empty-hint">还没有 MCP Server</div>}
      {props.servers.map((server) => (
        <div key={server.id} className={`mcp-card ${server.status}`}>
          <div className="mcp-head">
            <strong>{server.name}</strong>
            <span className="mcp-status">
              {STATUS_LABEL[server.status] ?? server.status}
            </span>
          </div>
          <div className="mcp-meta">
            {server.permission} · {server.tool_count} 个工具
          </div>
          {server.error && <div className="mcp-error">{server.error}</div>}
          <div className="mcp-actions">
            <button onClick={() => props.onRestart(server.id)}>重试</button>
            <button onClick={() => props.onToggle(server.id, !server.enabled)}>
              {server.enabled ? "停用" : "启用"}
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
