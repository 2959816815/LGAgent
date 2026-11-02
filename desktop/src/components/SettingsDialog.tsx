import { useEffect, useState } from "react";
import { KnowledgePanel } from "./KnowledgePanel";

interface ModelEntry {
  id: string;
  kind: "api" | "local";
  label: string;
  base_url: string;
  model: string;
  hasKey: boolean;
  supports_vision: boolean;
  /** 仅表单内使用：新填写的 Key，留空表示沿用已保存的 */
  apiKey: string;
}

interface DraftModel {
  index: number | null;
  kind: "api" | "local";
  label: string;
  base_url: string;
  model: string;
  supports_vision: boolean;
  apiKey: string;
}

function toEntry(raw: ConfiguredModel): ModelEntry {
  return {
    id: String(raw.id || ""),
    kind: raw.kind === "local" ? "local" : "api",
    label: String(raw.label || raw.id || ""),
    base_url: String(raw.base_url || ""),
    model: String(raw.model || ""),
    hasKey: Boolean(raw.hasKey),
    supports_vision: Boolean(raw.supports_vision),
    apiKey: "",
  };
}

function slug(text: string): string {
  const base = text
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 24);
  return base || `model-${Date.now().toString(36)}`;
}

export function SettingsDialog({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: () => void;
}) {
  const [models, setModels] = useState<ModelEntry[]>([]);
  const [draft, setDraft] = useState<DraftModel | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<"models" | "knowledge">("models");

  useEffect(() => {
    let cancelled = false;
    const desktop = window.desktop;
    if (!desktop) return;
    desktop
      .getModels()
      .then((list) => {
        if (!cancelled) setModels((list || []).map(toEntry));
      })
      .catch((error) => {
        if (!cancelled) setMessage(error instanceof Error ? error.message : String(error));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function startAdd(): void {
    setDraft({
      index: null,
      kind: "api",
      label: "",
      base_url: "https://",
      model: "",
      supports_vision: false,
      apiKey: "",
    });
    setMessage("");
  }

  function startEdit(index: number): void {
    const item = models[index];
    setDraft({
      index,
      kind: item.kind,
      label: item.label,
      base_url: item.base_url,
      model: item.model,
      supports_vision: item.supports_vision,
      apiKey: "",
    });
    setMessage("");
  }

  function removeAt(index: number): void {
    setModels((previous) => previous.filter((_, position) => position !== index));
  }

  function commitDraft(): void {
    if (!draft) return;
    const label = draft.label.trim() || draft.model.trim();
    if (!label) {
      setMessage("请填写名称");
      return;
    }
    const baseUrl = draft.base_url.trim();
    if (!/^https?:\/\//i.test(baseUrl)) {
      setMessage("地址必须以 http:// 或 https:// 开头");
      return;
    }
    const model = draft.model.trim();
    if (!model) {
      setMessage("请填写模型名（Ollama 里 `ollama list` 的名字）");
      return;
    }
    const existing = draft.index !== null ? models[draft.index] : null;
    const entry: ModelEntry = {
      id: existing ? existing.id : slug(label),
      kind: draft.kind,
      label,
      base_url: baseUrl,
      model,
      hasKey: draft.apiKey.trim() ? true : existing ? existing.hasKey : false,
      supports_vision: draft.kind === "local" ? false : draft.supports_vision,
      apiKey: draft.apiKey.trim(),
    };
    setModels((previous) =>
      draft.index !== null
        ? previous.map((item, position) => (position === draft.index ? entry : item))
        : [...previous, entry],
    );
    setDraft(null);
    setMessage("");
  }

  async function testDraft(): Promise<void> {
    const desktop = window.desktop;
    if (!desktop || !draft) return;
    setBusy(true);
    setMessage("");
    try {
      await desktop.testModel({
        kind: draft.kind,
        baseUrl: draft.base_url,
        model: draft.model,
        apiKey: draft.apiKey,
      });
      setMessage("连接成功");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function saveAll(): Promise<void> {
    const desktop = window.desktop;
    if (!desktop || draft) return;
    setBusy(true);
    setMessage("");
    try {
      await desktop.saveModels(
        models.map((item) => ({
          id: item.id,
          kind: item.kind,
          label: item.label,
          base_url: item.base_url,
          model: item.model,
          api_key: item.apiKey,
          supports_vision: item.supports_vision,
        })),
      );
      onSaved();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form
        className="modal-card settings-card"
        onClick={(event) => event.stopPropagation()}
        onSubmit={(event) => {
          event.preventDefault();
          if (tab === "models") void saveAll();
        }}
      >
        <h2>设置 · {tab === "models" ? "模型" : "知识库"}</h2>
        <div className="settings-tabs">
          <button
            type="button"
            className={tab === "models" ? "active" : ""}
            onClick={() => setTab("models")}
          >
            模型
          </button>
          <button
            type="button"
            className={tab === "knowledge" ? "active" : ""}
            onClick={() => setTab("knowledge")}
          >
            知识库
          </button>
        </div>
        {tab === "models" && (
          <>
        <p className="settings-hint">
          本地模型连接 Ollama；API 模型需要填写 Key 与地址。Key 会加密保存在本机。
        </p>

        <div className="model-list">
          {models.map((item, index) => (
            <div className="model-row" key={item.id}>
              <div className="model-row-main">
                <strong>{item.label}</strong>
                <span className="model-row-meta">
                  {item.kind === "local"
                    ? "本地 Ollama"
                    : item.hasKey
                      ? "API · 已配置 Key"
                      : "API · 未配置 Key"}
                  {" · "}
                  <code>{item.model}</code>
                  {item.supports_vision ? " · 支持图片" : ""}
                </span>
              </div>
              <div className="model-row-actions">
                <button type="button" onClick={() => startEdit(index)} disabled={!!draft}>
                  编辑
                </button>
                <button type="button" className="danger" onClick={() => removeAt(index)}>
                  删除
                </button>
              </div>
            </div>
          ))}
          {models.length === 0 && <p className="setup-message">还没有模型</p>}
        </div>

        <button type="button" className="model-add" onClick={startAdd} disabled={!!draft}>
          ＋ 添加模型
        </button>

        {draft && (
          <div className="model-form">
            <label>
              名称
              <input
                aria-label="模型名称"
                value={draft.label}
                onChange={(event) => setDraft({ ...draft, label: event.target.value })}
              />
            </label>
            <label>
              类型
              <select
                aria-label="模型类型"
                value={draft.kind}
                onChange={(event) =>
                  setDraft({ ...draft, kind: event.target.value as "api" | "local" })
                }
              >
                <option value="api">API 模型</option>
                <option value="local">本地 Ollama</option>
              </select>
            </label>
            <label>
              {draft.kind === "local" ? "服务地址" : "Base URL"}
              <input
                aria-label="模型地址"
                value={draft.base_url}
                onChange={(event) => setDraft({ ...draft, base_url: event.target.value })}
              />
            </label>
            <label>
              模型名
              <input
                aria-label="模型名"
                value={draft.model}
                onChange={(event) => setDraft({ ...draft, model: event.target.value })
                }
              />
            </label>
            {draft.kind === "api" && (
              <label className="model-checkbox">
                <input
                  type="checkbox"
                  aria-label="支持图片"
                  checked={draft.supports_vision}
                  onChange={(event) =>
                    setDraft({ ...draft, supports_vision: event.target.checked })
                  }
                />
                支持图片（视觉模型）
              </label>
            )}
            {draft.kind === "api" && (
              <label>
                API Key
                <input
                  aria-label="API Key"
                  type="password"
                  value={draft.apiKey}
                  onChange={(event) => setDraft({ ...draft, apiKey: event.target.value })}
                  placeholder="留空表示不修改"
                  autoComplete="off"
                />
              </label>
            )}
            <div className="model-form-actions">
              <button type="button" onClick={() => void testDraft()} disabled={busy}>
                测试连接
              </button>
              <button type="button" className="primary" onClick={commitDraft}>
                保存模型
              </button>
              <button type="button" onClick={() => setDraft(null)}>
                取消
              </button>
            </div>
          </div>
        )}
          </>
        )}

        {tab === "knowledge" && <KnowledgePanel />}

        <div className="modal-actions">
          <button type="button" onClick={onClose} disabled={busy}>
            关闭
          </button>
          {tab === "models" && (
            <button type="submit" className="primary" disabled={busy || !!draft}>
              保存
            </button>
          )}
        </div>
        {message && <p className="setup-message">{message}</p>}
      </form>
    </div>
  );
}
