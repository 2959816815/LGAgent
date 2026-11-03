import { useState } from "react";
import type { McpRemoteConfig, McpTestResult } from "../types";

interface HeaderRow {
  name: string;
  value: string;
  secret: boolean;
}

export function McpRemoteForm(props: {
  onSave: (config: McpRemoteConfig) => Promise<void>;
  onTest: (config: McpRemoteConfig) => Promise<McpTestResult>;
  onCancel: () => void;
}) {
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [transport, setTransport] = useState<"streamable-http" | "sse">("streamable-http");
  const [headers, setHeaders] = useState<HeaderRow[]>([{ name: "", value: "", secret: true }]);
  const [verifyTls, setVerifyTls] = useState(true);
  const [timeout, setTimeout] = useState(60);
  const [permission, setPermission] = useState("ask");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [test, setTest] = useState<McpTestResult | null>(null);

  function build(): McpRemoteConfig {
    const filled = headers.filter((row) => row.name.trim());
    const values: Record<string, string> = {};
    const secretHeaders: string[] = [];
    for (const row of filled) {
      values[row.name.trim()] = row.value;
      if (row.secret) secretHeaders.push(row.name.trim());
    }
    return {
      id: id.trim(),
      name: name.trim() || id.trim(),
      url: url.trim(),
      transport,
      headers: values,
      secret_headers: secretHeaders,
      verify_tls: verifyTls,
      timeout,
      permission,
    };
  }

  async function runTest(): Promise<void> {
    setBusy(true);
    setMessage("");
    try {
      setTest(await props.onTest(build()));
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
      await props.onSave(build());
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mcp-remote-form">
      <label>ID<input value={id} onChange={(event) => setId(event.target.value)} placeholder="remote_demo" /></label>
      <label>名称<input value={name} onChange={(event) => setName(event.target.value)} /></label>
      <label>URL<input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://example.com/mcp" /></label>
      <label>
        传输
        <select value={transport} onChange={(event) => setTransport(event.target.value as "streamable-http" | "sse")}>
          <option value="streamable-http">Streamable HTTP</option>
          <option value="sse">SSE</option>
        </select>
      </label>
      <label className="mcp-checkbox">
        <input type="checkbox" checked={verifyTls} onChange={(event) => setVerifyTls(event.target.checked)} />
        校验 TLS 证书
      </label>
      <label>超时（秒）<input type="number" value={timeout} onChange={(event) => setTimeout(Number(event.target.value))} /></label>
      <label>
        权限
        <select value={permission} onChange={(event) => setPermission(event.target.value)}>
          <option value="ask">每次询问</option>
          <option value="auto">自动执行</option>
          <option value="deny">禁止执行</option>
        </select>
      </label>
      <div className="mcp-headers">
        <strong>Headers</strong>
        {headers.map((row, index) => (
          <div className="mcp-header-row" key={index}>
            <input placeholder="Header 名" value={row.name} onChange={(event) => setHeaders((current) => current.map((item, i) => i === index ? { ...item, name: event.target.value } : item))} />
            <input placeholder="值，可用 ${ENV}" value={row.value} onChange={(event) => setHeaders((current) => current.map((item, i) => i === index ? { ...item, value: event.target.value } : item))} />
            <label className="mcp-checkbox"><input type="checkbox" checked={row.secret} onChange={(event) => setHeaders((current) => current.map((item, i) => i === index ? { ...item, secret: event.target.checked } : item))} />密钥</label>
          </div>
        ))}
        <button type="button" onClick={() => setHeaders((current) => [...current, { name: "", value: "", secret: true }])}>＋ 添加 Header</button>
      </div>
      {url.startsWith("http://") && <div className="mcp-warning">未使用 TLS，Header 和资源内容可能被窃听。</div>}
      {!verifyTls && <div className="mcp-warning">已关闭 TLS 证书校验。</div>}
      {test && (
        <div className={test.ok ? "mcp-test-ok" : "mcp-error"}>
          {test.ok
            ? `连接成功：工具 ${test.tools}，资源 ${test.resources}，Prompt ${test.prompts}`
            : `连接失败：${test.error}`}
        </div>
      )}
      <div className="mcp-actions">
        <button type="button" onClick={() => void runTest()} disabled={busy}>测试连接</button>
        <button type="button" className="primary" onClick={() => void save()} disabled={busy}>保存</button>
        <button type="button" onClick={props.onCancel} disabled={busy}>取消</button>
      </div>
      {message && <div className="mcp-error">{message}</div>}
    </div>
  );
}
