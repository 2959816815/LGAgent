import { useEffect, useState } from "react";

import { searchSessions } from "../api";
import { groupSessions, timeLabel } from "../helpers";
import type { SessionMeta, SessionSearchResult } from "../types";

export function Sidebar(props: {
  sessions: SessionMeta[];
  activeId: string | null;
  query: string;
  onQuery: (value: string) => void;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onSettings: () => void;
}) {
  const [hits, setHits] = useState<SessionSearchResult[]>([]);
  useEffect(() => {
    const query = props.query.trim();
    if (query.length < 2) {
      setHits([]);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      searchSessions(query)
        .then((results) => {
          if (!cancelled) setHits(results);
        })
        .catch(() => {
          if (!cancelled) setHits([]);
        });
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [props.query]);

  const filtered = props.sessions.filter((session) =>
    session.title.toLowerCase().includes(props.query.trim().toLowerCase()),
  );
  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-dot">◆</span>
        <span className="brand-name">LangGraph Agent</span>
        <span className="brand-version">v{__APP_VERSION__}</span>
      </div>
      <button className="new-session" onClick={props.onNew}>
        ＋ 新会话
      </button>
      <input
        className="search"
        value={props.query}
        onChange={(event) => props.onQuery(event.target.value)}
        placeholder="搜索会话"
      />
      {props.query.trim().length >= 2 && hits.length > 0 && (
        <div className="search-hits">
          <div className="group-label">消息命中</div>
          {hits.map((hit) => (
            <button
              key={`${hit.session_id}-${hit.message_index}`}
              className="search-hit"
              onClick={() => {
                props.onSelect(hit.session_id);
                props.onQuery("");
              }}
            >
              <span className="search-hit-title">
                {hit.role === "title" ? "标题" : hit.title}
              </span>
              <span className="search-hit-snippet">{hit.snippet}</span>
            </button>
          ))}
        </div>
      )}
      <div className="session-scroll">
        {groupSessions(filtered).map((group) => (
          <section key={group.label} className="session-group">
            <div className="group-label">{group.label}</div>
            {group.items.map((session) => (
              <div
                key={session.id}
                className={`session-row ${session.id === props.activeId ? "active" : ""}`}
                onClick={() => props.onSelect(session.id)}
              >
                <div className="session-main">
                  <div className="session-title">{session.title || "新会话"}</div>
                  <div className="session-meta">
                    {timeLabel(session.updated_at)} · {session.message_count} 条
                  </div>
                </div>
                <button
                  className="session-delete"
                  title="删除会话"
                  onClick={(event) => {
                    event.stopPropagation();
                    props.onDelete(session.id);
                  }}
                >
                  ×
                </button>
              </div>
            ))}
          </section>
        ))}
        {filtered.length === 0 && <div className="empty-hint">没有匹配的会话</div>}
      </div>
      <div className="sidebar-footer">
        <span className="status-dot" /> 本地服务已连接
        <button className="diag-button" type="button" onClick={props.onSettings}>
          设置
        </button>
        <button
          className="diag-button"
          type="button"
          title="导出日志与配置诊断信息（不含 API Key）"
          onClick={() => {
            void window.desktop?.exportDiagnostics();
          }}
        >
          导出诊断
        </button>
      </div>
    </aside>
  );
}
