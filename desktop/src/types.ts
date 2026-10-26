export type ModelKey = "local" | "deepseek" | "qwen";

export interface ModelInfo {
  key: ModelKey;
  label: string;
  model: string;
  available: boolean;
  supports_vision: boolean;
  context_window: number;
  reason?: string;
}

export interface Attachment {
  type: "image";
  data_url: string;
  name?: string;
}

export interface SessionMeta {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  model_key: ModelKey;
}

export interface Message {
  role: "user" | "assistant" | "tool" | "system";
  content: string;
  attachments?: Attachment[];
  tool_calls?: { id: string; name: string; args: Record<string, unknown> }[];
  tool_call_id?: string;
  name?: string;
}

export interface SessionDetail extends SessionMeta {
  messages: Message[];
}

export interface AgentRecord {
  id: string;
  task: string;
  status: "running" | "succeeded" | "failed";
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  result?: string | null;
  error?: string | null;
}

export interface ToolEvent {
  tool: string;
  call_id: string;
  args?: Record<string, unknown>;
  status?: string;
  duration_ms?: number;
}

export interface ApprovalRequest {
  run_id: string;
  session_id: string;
  approval_id: string;
  tool: string;
  title: string;
  path: string;
  diff: string;
  detail: string;
}

export interface FileEntry {
  name: string;
  path: string;
  type: "file" | "dir";
  size?: number;
}

export interface SessionSearchResult {
  session_id: string;
  title: string;
  role: "title" | "human" | "ai" | "tool" | "system" | string;
  message_index: number;
  snippet: string;
}

export interface McpServerStatus {
  id: string;
  name: string;
  status: "starting" | "running" | "ready" | "failed" | "stopped" | "disabled" | string;
  enabled: boolean;
  permission: "auto" | "ask" | "deny" | string;
  tool_count: number;
  tools?: string[];
  error?: string | null;
  started_at?: string | null;
}

export interface McpCatalogField {
  key: string;
  label: string;
  default: string;
  required: boolean;
  secret: boolean;
}

export interface McpCatalogItem {
  id: string;
  name: string;
  description: string;
  command: string[];
  cwd: string;
  env: Record<string, string>;
  fields: McpCatalogField[];
  permission: "auto" | "ask" | "deny" | string;
  docs_url?: string;
  installed: boolean;
}
