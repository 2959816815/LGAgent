import { useState } from "react";

interface SetupState {
  baseUrl: string;
  model: string;
}

export function SetupWizard({
  initialState,
  onDone,
}: {
  initialState: SetupState;
  onDone: () => void;
}) {
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(initialState.baseUrl);
  const [model, setModel] = useState(initialState.model);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const input = { apiKey, baseUrl, model };

  async function test(): Promise<void> {
    setBusy(true);
    setMessage("");
    try {
      await window.desktop!.testDeepSeek(input);
      setMessage("连接成功");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function save(): Promise<void> {
    setBusy(true);
    setMessage("");
    try {
      await window.desktop!.saveDeepSeek(input);
      onDone();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="setup-shell">
      <form
        className="setup-card"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <h1>连接 DeepSeek</h1>
        <p>正常使用只需要一个 API Key。Key 会加密保存在本机，不会写入日志。</p>
        <label>
          API Key
          <input
            aria-label="API Key"
            type="password"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
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
        <div className="setup-actions">
          <button type="button" onClick={() => void test()} disabled={busy || !apiKey}>
            测试连接
          </button>
          <button type="submit" className="primary" disabled={busy || !apiKey}>
            保存并开始
          </button>
        </div>
        {message && <p className="setup-message">{message}</p>}
      </form>
    </div>
  );
}
