import { useEffect, useRef, useState } from "react";
import {
  addKnowledgeSource,
  cancelKnowledgeIndex,
  getKnowledgeStatus,
  removeKnowledgeSource,
  setKnowledgeEnabled,
  startKnowledgeIndex,
} from "../api";
import { errorText, formatCount } from "../helpers";
import type { KnowledgeStatus } from "../types";

export function KnowledgePanel() {
  const [status, setStatus] = useState<KnowledgeStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const timerRef = useRef<number | null>(null);

  async function refresh(): Promise<void> {
    try {
      setStatus(await getKnowledgeStatus());
    } catch (cause) {
      setMessage(errorText(cause));
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  useEffect(() => {
    if (timerRef.current !== null) window.clearInterval(timerRef.current);
    const interval = status?.indexing ? 1_000 : 10_000;
    timerRef.current = window.setInterval(() => void refresh(), interval);
    return () => {
      if (timerRef.current !== null) window.clearInterval(timerRef.current);
    };
  }, [status?.indexing]);

  async function run(action: () => Promise<KnowledgeStatus>, success = ""): Promise<void> {
    setBusy(true);
    setMessage("");
    try {
      setStatus(await action());
      if (success) setMessage(success);
    } catch (cause) {
      setMessage(errorText(cause));
    } finally {
      setBusy(false);
    }
  }

  async function addFolder(): Promise<void> {
    const path = await window.desktop?.pickFolder?.();
    if (!path) return;
    await run(() => addKnowledgeSource(path), "已添加，正在建立索引");
  }

  if (!status) {
    return <div className="knowledge-panel">{message || "正在读取知识库状态…"}</div>;
  }

  const progress = status.progress;
  const total = progress.total_files || 0;
  const done = progress.processed_files || 0;
  const percent = total > 0 ? Math.min(100, (done / total) * 100) : 0;
  const embedding = status.embedding;

  return (
    <div className="knowledge-panel">
      <label className="model-checkbox">
        <input
          type="checkbox"
          checked={status.enabled}
          disabled={busy}
          onChange={(event) =>
            void run(() => setKnowledgeEnabled(event.target.checked))
          }
        />
        启用知识库（Agent 会自动检索）
      </label>

      <div className={`knowledge-status ${embedding.available ? "ok" : "error"}`}>
        嵌入模型：{embedding.model} ·{" "}
        {embedding.available ? "已就绪" : embedding.reason || "不可用"}
      </div>
      {embedding.warning && <div className="knowledge-warning">{embedding.warning}</div>}
      <div className="knowledge-note">
        使用云端模型时，检索到的资料会作为上下文发送给云端模型；需要完全离线请切换到本地模型。
      </div>

      <div className="knowledge-actions">
        <button type="button" onClick={() => void addFolder()} disabled={busy}>
          添加文件夹
        </button>
        <button
          type="button"
          onClick={() => void run(() => startKnowledgeIndex(false), "已开始更新索引")}
          disabled={busy || status.indexing}
        >
          更新索引
        </button>
        <button
          type="button"
          onClick={() => {
            if (!window.confirm("清空并重建整个知识库索引？")) return;
            void run(() => startKnowledgeIndex(true), "已开始重建索引");
          }}
          disabled={busy || status.indexing}
        >
          重建索引
        </button>
        {status.indexing && (
          <button
            type="button"
            onClick={() => void run(() => cancelKnowledgeIndex(), "已请求取消")}
            disabled={busy}
          >
            取消
          </button>
        )}
      </div>

      {status.sources.length === 0 && (
        <div className="empty-hint">还没有添加文件夹</div>
      )}
      {status.sources.map((source) => (
        <div className="knowledge-source" key={source.id}>
          <div>
            <strong>{source.path}</strong>
            <span>{source.document_count} 个文档</span>
          </div>
          <button
            type="button"
            className="danger"
            disabled={busy || status.indexing}
            title={status.indexing ? "索引进行中，先取消再移除" : undefined}
            onClick={() => {
              if (!window.confirm(`移除 ${source.path}？`)) return;
              void run(() => removeKnowledgeSource(source.id));
            }}
          >
            移除
          </button>
        </div>
      ))}

      {status.indexing && (
        <div className="knowledge-progress">
          <div className="knowledge-progress-bar">
            <span style={{ width: `${percent}%` }} />
          </div>
          <div className="knowledge-progress-text">
            {progress.current_file || "准备中"} · {done}/{total} 个文件
            {progress.total_chunks
              ? ` · 已嵌入 ${progress.embedded_chunks ?? 0}/${progress.total_chunks} 块`
              : ""}
          </div>
        </div>
      )}
      <div className="knowledge-counts">
        <span>文档 {formatCount(status.documents)}</span>
        <span>分块 {formatCount(status.chunks)}</span>
        <span>失败 {progress.failed_documents}</span>
        <span>最后索引 {status.last_index_at || "从未"}</span>
      </div>
      {progress.message && <div className="knowledge-warning">{progress.message}</div>}
      {message && <div className="setup-message">{message}</div>}
    </div>
  );
}
