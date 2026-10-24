import type {
  AgentRecord,
  ModelInfo,
  SessionDetail,
  SessionMeta,
} from "./types";

let baseUrl = "http://127.0.0.1:8765";
let token = "";

export async function initApi(): Promise<void> {
  if (!window.desktop) return;
  const config = await window.desktop.getConfig();
  baseUrl = config.baseUrl;
  token = config.token;
}

function headers(): Record<string, string> {
  const result: Record<string, string> = { "Content-Type": "application/json" };
  if (token) result["X-Desktop-Token"] = token;
  return result;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    headers: { ...headers(), ...(init.headers || {}) },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch {
      // keep status text
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export async function listSessions(): Promise<SessionMeta[]> {
  const data = await request<{ sessions: SessionMeta[] }>("/api/sessions");
  return data.sessions;
}

export async function createSession(
  title = "新会话",
  modelKey?: string,
): Promise<SessionDetail> {
  return request<SessionDetail>("/api/sessions", {
    method: "POST",
    body: JSON.stringify({ title, model_key: modelKey }),
  });
}

export async function getSession(id: string): Promise<SessionDetail> {
  return request<SessionDetail>(`/api/sessions/${id}`);
}

export async function deleteSession(id: string): Promise<void> {
  await request(`/api/sessions/${id}`, { method: "DELETE" });
}

export async function patchSessionModel(
  id: string,
  modelKey: string,
): Promise<{ model_key: string; label: string }> {
  return request(`/api/sessions/${id}/model`, {
    method: "PATCH",
    body: JSON.stringify({ model_key: modelKey }),
  });
}

export async function listModels(): Promise<ModelInfo[]> {
  const data = await request<{ models: ModelInfo[] }>("/api/models");
  return data.models;
}

export async function listAgents(): Promise<AgentRecord[]> {
  const data = await request<{ agents: AgentRecord[] }>("/api/agents");
  return data.agents;
}

export async function cancelRun(runId: string): Promise<void> {
  await request(`/api/runs/${runId}/cancel`, { method: "POST" });
}

export interface StreamEvent {
  event: string;
  data: Record<string, unknown>;
}

export async function* streamMessage(
  sessionId: string,
  text: string,
  signal: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const response = await fetch(`${baseUrl}/api/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ text }),
    signal,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch {
      // keep status text
    }
    throw new Error(detail);
  }
  if (!response.body) throw new Error("服务端没有返回流");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let index = buffer.indexOf("\n\n");
    while (index >= 0) {
      const block = buffer.slice(0, index);
      buffer = buffer.slice(index + 2);
      let event = "message";
      const dataLines: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (dataLines.length) {
        yield { event, data: JSON.parse(dataLines.join("\n")) };
      }
      index = buffer.indexOf("\n\n");
    }
  }
}
