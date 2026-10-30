import { useState } from "react";

import type { McpCatalogItem, McpServerStatus } from "../types";

const STATUS_LABEL: Record<string, string> = {
  starting: "启动中",
  running: "运行中",
  ready: "就绪",
  failed: "失败",
  stopped: "已停止",
  disabled: "已禁用",
};

const PERMISSION_LABEL: Record<string, string> = {
  auto: "自动执行",
  ask: "每次询问",
  deny: "禁止执行",
};

export function McpPanel(props: {
  servers: McpServerStatus[];
  catalog: McpCatalogItem[];
  onRestart: (id: string) => void;
  onToggle: (id: string, enabled: boolean) => void;
  onAdd: (presetId: string, env: Record<string, string>) => Promise<void>;
  onRemove: (id: string) => void;
  onPermission: (id: string, permission: string) => void;
}) {
  const [adding, setAdding] = useState(false);
  const [selected, setSelected] = useState<McpCatalogItem | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  function choose(preset: McpCatalogItem) {
    if (preset.installed) return;
    setError("");
    if (preset.fields.length === 0) {
      setSaving(true);
      void props
        .onAdd(preset.id, {})
        .then(() => setAdding(false))
        .catch((err: unknown) => setError(String(err)))
        .finally(() => setSaving(false));
      return;
    }
    setSelected(preset);
    setValues(
      Object.fromEntries(preset.fields.map((field) => [field.key, field.default])),
    );
  }

  function submit() {
    if (!selected) return;
    setSaving(true);
    setError("");
    void props
      .onAdd(selected.id, values)
      .then(() => {
        setAdding(false);
        setSelected(null);
      })
      .catch((err: unknown) => setError(String(err)))
      .finally(() => setSaving(false));
  }

  return (
    <div className="panel-body mcp-list">
      <div className="mcp-note">
        MCP 是可选能力；失败不会影响文件、Office、会话和本地模型。
      </div>
      <div className="mcp-toolbar">
        <button className="primary" onClick={() => setAdding((value) => !value)}>
          {adding ? "收起目录" : "添加服务"}
        </button>
      </div>

      {adding && (
        <div className="mcp-catalog">
          {props.catalog.map((preset) => (
            <div key={preset.id} className="mcp-preset">
              <div className="mcp-preset-head">
                <strong>{preset.name}</strong>
                <button
                  disabled={preset.installed || saving}
                  onClick={() => choose(preset)}
                >
                  {preset.installed ? "已添加" : "添加"}
                </button>
              </div>
              <div className="mcp-preset-desc">{preset.description}</div>
              {selected?.id === preset.id && (
                <div className="mcp-preset-form">
                  {preset.fields.map((field) => (
                    <label key={field.key}>
                      {field.label}
                      <input
                        type={field.secret ? "password" : "text"}
                        value={values[field.key] ?? ""}
                        placeholder={field.default}
                        onChange={(event) =>
                          setValues((current) => ({
                            ...current,
                            [field.key]: event.target.value,
                          }))
                        }
                      />
                    </label>
                  ))}
                  <button disabled={saving} onClick={submit}>
                    {saving ? "添加中..." : "确认添加"}
                  </button>
                </div>
              )}
            </div>
          ))}
          {error && <div className="mcp-error">{error}</div>}
        </div>
      )}

      {props.servers.length === 0 && <div className="empty-hint">还没有 MCP Server</div>}
      {props.servers.map((server) => (
        <div key={server.id} className={`mcp-card ${server.status}`}>
          <div className="mcp-head">
            <strong>{server.name}</strong>
            <span className="mcp-status">
              {STATUS_LABEL[server.status] ?? server.status}
            </span>
          </div>
          <div className="mcp-meta">{server.tool_count} 个工具</div>
          <label className="mcp-permission">
            权限
            <select
              value={server.permission}
              onChange={(event) => props.onPermission(server.id, event.target.value)}
            >
              {Object.entries(PERMISSION_LABEL).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          {server.last_error && <div className="mcp-error">{server.last_error}</div>}
          <div className="mcp-actions">
            <button
              disabled={server.status === "starting"}
              onClick={() => props.onRestart(server.id)}
            >
              重试
            </button>
            <button
              disabled={server.status === "starting"}
              onClick={() => props.onToggle(server.id, !server.enabled)}
            >
              {server.enabled ? "停用" : "启用"}
            </button>
            <button
              className="danger"
              disabled={server.status === "starting"}
              onClick={() => props.onRemove(server.id)}
            >
              移除
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
