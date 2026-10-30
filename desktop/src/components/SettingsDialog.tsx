import { useEffect, useState } from "react";

interface SettingsState {
  baseUrl: string;
  model: string;
  hasApiKey: boolean;
}

export function SettingsDialog({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: () => void;
}) {
  const [loaded, setLoaded] = useState<SettingsState | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const desktop = window.desktop;
    if (!desktop) {
      setLoaded({ baseUrl: "", model: "", hasApiKey: false });
      return;
    }
    desktop
      .getSettings()
      .then((state) => {
        if (cancelled) return;
        setLoaded(state);
        setBaseUrl(state.baseUrl);
        setModel(state.model);
      })
      .catch((error) => {
        if (!cancelled) setMessage(error instanceof Error ? error.message : String(error));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const canSubmit = Boolean(loaded?.hasApiKey || apiKey.trim());

  async function run(action: () => Promise<unknown>, success: string): Promise<boolean> {
    setBusy(true);
    setMessage("");
    try {
      await action();
      setMessage(success);
      return true;
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function test(): Promise<void> {
    const desktop = window.desktop;
    if (!desktop) return;
    await run(() => desktop.testSettings({ apiKey, baseUrl, model }), "连接成功");
  }

  async function save(): Promise<void> {
    const desktop = window.desktop;
    if (!desktop) return;
    const ok = await run(() => desktop.saveSettings({ apiKey, baseUrl, model }), "已保存");
    if (ok) onSaved();
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form
        className="modal-card"
        onClick={(event) => event.stopPropagation()}
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <h2>设置</h2>
        {!loaded ? (
          <p className="setup-message">加载中…</p>
        ) : (
          <>
            <label>
              API Key
              <input
                aria-label="API Key"
                type="password"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder="留空表示不修改"
                autoComplete="off"
              />
            </label>
            <label>
              Base URL
              <input
                aria-label="Base URL"
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
              />
            </label>
            <label>
              模型
              <input
                aria-label="模型"
                value={model}
                onChange={(event) => setModel(event.target.value)}
              />
            </label>
          </>
        )}
        <div className="modal-actions">
          <button type="button" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button type="button" onClick={() => void test()} disabled={busy || !canSubmit}>
            测试连接
          </button>
          <button type="submit" className="primary" disabled={busy || !canSubmit}>
            保存
          </button>
        </div>
        {message && <p className="setup-message">{message}</p>}
      </form>
    </div>
  );
}
