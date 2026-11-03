import { useState } from "react";
import type { McpImportPreview } from "../types";

export function McpImportForm(props: {
  onPreview: (payload: string) => Promise<McpImportPreview>;
  onConfirm: (payload: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<McpImportPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function runPreview(): Promise<void> {
    setBusy(true);
    setMessage("");
    try {
      setPreview(await props.onPreview(text));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function confirm(): Promise<void> {
    setBusy(true);
    setMessage("");
    try {
      await props.onConfirm(text);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mcp-import-form">
      <textarea
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder='粘贴 {"mcpServers": {...}}'
        rows={8}
      />
      {preview && (
        <div className="mcp-import-preview">
          {preview.entries.map((entry) => (
            <div key={entry.id}>
              {entry.id} · {entry.transport}
              {entry.conflict ? " · 将自动改名" : ""}
            </div>
          ))}
          {preview.errors.map((error) => <div className="mcp-error" key={error}>{error}</div>)}
        </div>
      )}
      <div className="mcp-actions">
        <button type="button" onClick={() => void runPreview()} disabled={busy || !text.trim()}>预览</button>
        <button type="button" className="primary" onClick={() => void confirm()} disabled={busy || !preview || preview.errors.length > 0}>确认导入</button>
        <button type="button" onClick={props.onCancel} disabled={busy}>取消</button>
      </div>
      {message && <div className="mcp-error">{message}</div>}
    </div>
  );
}
