import type {
  AgentRecord,
  Attachment,
  ContextStats,
  FileEntry,
  KnowledgeSearchResponse,
  KnowledgeStatus,
  McpCatalogItem,
  McpImportPreview,
  McpPromptInfo,
  McpPromptResult,
  McpRemoteConfig,
  McpResourceContent,
  McpResourceInfo,
  McpResourceTemplateInfo,
  McpServerStatus,
  McpTestResult,
  ModelHealth,
  ModelInfo,
  SessionDetail,
  SessionMeta,
  SessionSearchResult,
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

export class ApiError extends Error {
  status: number;
  runId?: string;

  constructor(message: string, status: number, runId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.runId = runId;
  }
}

async function toApiError(response: Response): Promise<ApiError> {
  let detail: unknown = response.statusText;
  try {
    const body = await response.json();
    detail = body.detail ?? detail;
  } catch {
    // keep status text
  }
  let message = response.statusText || `HTTP ${response.status}`;
  let runId: string | undefined;
  if (typeof detail === "string" && detail) {
    message = detail;
  } else if (detail && typeof detail === "object") {
    const record = detail as Record<string, unknown>;
    if (typeof record.message === "string" && record.message) {
      message = record.message;
    }
    if (typeof record.run_id === "string" && record.run_id) {
      runId = record.run_id;
    }
  }
  return new ApiError(message, response.status, runId);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    headers: { ...headers(), ...(init.headers || {}) },
  });
  if (!response.ok) {
    throw await toApiError(response);
  }
  return response.json() as Promise<T>;
}

export async function listSessions(): Promise<SessionMeta[]> {
  const data = await request<{ sessions: SessionMeta[] }>("/api/sessions");
  return data.sessions;
}

export async function searchSessions(query: string): Promise<SessionSearchResult[]> {
  const data = await request<{ results: SessionSearchResult[] }>(
    `/api/sessions/search?q=${encodeURIComponent(query)}`,
  );
  return data.results;
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

export interface SessionPageOptions {
  limit?: number;
  before?: number;
}

export async function getSession(
  id: string,
  options: SessionPageOptions = {},
): Promise<SessionDetail> {
  const params = new URLSearchParams();
  if (typeof options.limit === "number") params.set("limit", String(options.limit));
  if (typeof options.before === "number") params.set("before", String(options.before));
  const query = params.toString();
  return request<SessionDetail>(`/api/sessions/${id}${query ? `?${query}` : ""}`);
}

export async function deleteSession(id: string): Promise<void> {
  await request(`/api/sessions/${id}`, { method: "DELETE" });
}

export async function renameSession(
  id: string,
  title: string,
): Promise<{ id: string; title: string }> {
  return request(`/api/sessions/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

export async function patchSessionModel(
  id: string,
  modelKey: string,
): Promise<{ model_key: string; label: string; context?: ContextStats }> {
  return request(`/api/sessions/${id}/model`, {
    method: "PATCH",
    body: JSON.stringify({ model_key: modelKey }),
  });
}

export async function getModelHealth(key: string): Promise<ModelHealth> {
  return request<ModelHealth>(`/api/models/${encodeURIComponent(key)}/health`);
}

export async function listModels(): Promise<ModelInfo[]> {
  const data = await request<{ models: ModelInfo[] }>("/api/models");
  return data.models;
}

export async function listAgents(sessionId?: string): Promise<AgentRecord[]> {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
  const data = await request<{ agents: AgentRecord[] }>(`/api/agents${query}`);
  return data.agents;
}

export async function cancelAgent(id: string): Promise<void> {
  await request(`/api/agents/${encodeURIComponent(id)}/cancel`, { method: "POST" });
}

export async function listFiles(path = "."): Promise<FileEntry[]> {
  const data = await request<{ entries: FileEntry[] }>(
    `/api/files?path=${encodeURIComponent(path)}`,
  );
  return data.entries;
}

export async function searchFiles(query: string): Promise<FileEntry[]> {
  const data = await request<{ results: FileEntry[] }>(
    `/api/files/search?q=${encodeURIComponent(query)}`,
  );
  return data.results;
}

export async function cancelRun(runId: string): Promise<void> {
  await request(`/api/runs/${runId}/cancel`, { method: "POST" });
}

export async function resolveApproval(
  runId: string,
  approvalId: string,
  approved: boolean,
): Promise<void> {
  await request(`/api/runs/${runId}/approvals/${approvalId}`, {
    method: "POST",
    body: JSON.stringify({ approved }),
  });
}

export async function undoSession(sessionId: string): Promise<{ restored: string }> {
  return request(`/api/sessions/${sessionId}/undo`, { method: "POST" });
}

export async function compactSession(id: string): Promise<SessionDetail> {
  return request<SessionDetail>(`/api/sessions/${id}/compact`, { method: "POST" });
}

export async function listMcpServers(): Promise<McpServerStatus[]> {
  const data = await request<{ servers: McpServerStatus[] }>("/api/mcp/servers");
  return data.servers;
}

export async function listMcpCatalog(): Promise<McpCatalogItem[]> {
  const data = await request<{ presets: McpCatalogItem[] }>("/api/mcp/catalog");
  return data.presets;
}

export async function addMcpServer(presetId: string, env: Record<string, string>): Promise<void> {
  await request("/api/mcp/servers", {
    method: "POST",
    body: JSON.stringify({ preset_id: presetId, env }),
  });
}

export async function removeMcpServer(id: string): Promise<void> {
  await request(`/api/mcp/servers/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function setMcpPermission(id: string, permission: string): Promise<void> {
  await request(`/api/mcp/servers/${encodeURIComponent(id)}/permission`, {
    method: "POST",
    body: JSON.stringify({ permission }),
  });
}

export async function restartMcpServer(id: string): Promise<void> {
  await request(`/api/mcp/servers/${encodeURIComponent(id)}/restart`, { method: "POST" });
}

export async function setMcpEnabled(id: string, enabled: boolean): Promise<void> {
  await request(`/api/mcp/servers/${encodeURIComponent(id)}/enable`, {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });
}

export async function addRemoteMcpServer(config: McpRemoteConfig): Promise<void> {
  await request("/api/mcp/servers", {
    method: "POST",
    body: JSON.stringify({ kind: "remote", ...config }),
  });
}

export async function testMcpServer(
  config: Omit<McpRemoteConfig, "id" | "name"> & { id?: string; name?: string },
): Promise<McpTestResult> {
  return request<McpTestResult>("/api/mcp/servers/test", {
    method: "POST",
    body: JSON.stringify(config),
  });
}

export async function importMcpServers(
  payload: string | Record<string, unknown>,
  confirm: boolean,
): Promise<McpImportPreview | { imported: { id: string; transport: string }[] }> {
  return request("/api/mcp/import", {
    method: "POST",
    body: JSON.stringify({ payload, confirm }),
  });
}

export async function getMcpResources(id: string): Promise<{
  resources: McpResourceInfo[];
  templates: McpResourceTemplateInfo[];
}> {
  return request(`/api/mcp/servers/${encodeURIComponent(id)}/resources`);
}

export async function readMcpResource(
  id: string,
  uri: string,
): Promise<McpResourceContent> {
  return request(
    `/api/mcp/servers/${encodeURIComponent(id)}/resources/read?uri=${encodeURIComponent(uri)}`,
  );
}

export async function getMcpPrompts(id: string): Promise<{ prompts: McpPromptInfo[] }> {
  return request(`/api/mcp/servers/${encodeURIComponent(id)}/prompts`);
}

export async function getMcpPrompt(
  id: string,
  name: string,
  args: Record<string, string>,
): Promise<McpPromptResult> {
  return request(`/api/mcp/servers/${encodeURIComponent(id)}/prompts/get`, {
    method: "POST",
    body: JSON.stringify({ name, arguments: args }),
  });
}

export interface StreamEvent {
  event: string;
  data: Record<string, unknown>;
}

export interface StreamOptions {
  mode?: "regenerate";
  editIndex?: number;
}

export async function* streamMessage(
  sessionId: string,
  text: string,
  signal: AbortSignal,
  attachments: Attachment[] = [],
  options: StreamOptions = {},
): AsyncGenerator<StreamEvent> {
  const body: Record<string, unknown> = { text, attachments };
  if (options.mode) body.mode = options.mode;
  if (typeof options.editIndex === "number") body.edit_index = options.editIndex;
  const response = await fetch(`${baseUrl}/api/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    throw await toApiError(response);
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

export async function getKnowledgeStatus(): Promise<KnowledgeStatus> {
  return request<KnowledgeStatus>("/api/knowledge/status");
}

export async function addKnowledgeSource(path: string): Promise<KnowledgeStatus> {
  return request<KnowledgeStatus>("/api/knowledge/sources", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
}

export async function removeKnowledgeSource(id: number): Promise<KnowledgeStatus> {
  return request<KnowledgeStatus>(`/api/knowledge/sources/${id}`, {
    method: "DELETE",
  });
}

export async function startKnowledgeIndex(
  rebuild = false,
): Promise<KnowledgeStatus> {
  return request<KnowledgeStatus>("/api/knowledge/index", {
    method: "POST",
    body: JSON.stringify({ rebuild }),
  });
}

export async function cancelKnowledgeIndex(): Promise<KnowledgeStatus> {
  return request<KnowledgeStatus>("/api/knowledge/cancel", { method: "POST" });
}

export async function setKnowledgeEnabled(enabled: boolean): Promise<KnowledgeStatus> {
  return request<KnowledgeStatus>("/api/knowledge/settings", {
    method: "PATCH",
    body: JSON.stringify({ enabled }),
  });
}

export async function searchKnowledge(
  q: string,
  topK = 5,
): Promise<KnowledgeSearchResponse> {
  const params = new URLSearchParams({ q, top_k: String(topK) });
  return request<KnowledgeSearchResponse>(`/api/knowledge/search?${params}`);
}
