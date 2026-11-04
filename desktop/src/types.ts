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
  has_more?: boolean;
  next_before?: number | null;
  summary?: string;
  summary_count?: number;
  summary_upto?: number;
  context?: ContextStats;
}

export interface ContextStats {
  raw_tokens: number;
  view_tokens: number;
  window: number;
  tool_tokens: number;
  system_tokens: number;
  summary_tokens: number;
  recent_turns: number;
  dropped_turns: number;
  images_dropped: number;
  fits: boolean;
}

export interface ModelHealth {
  key: string;
  reachable: boolean;
  reason?: string;
}

export interface AgentRecord {
  id: string;
  task: string;
  session_id?: string;
  status: "running" | "succeeded" | "failed" | "cancelled";
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
  last_error?: string | null;
  started_at?: string | null;
  transport?: "stdio" | "streamable-http" | "sse";
  resource_count?: number;
  prompt_count?: number;
  capabilities?: { tools?: boolean; resources?: boolean; prompts?: boolean };
  server_info?: { name?: string; version?: string };
  warnings?: string[];
}

export interface McpRemoteConfig {
  id: string;
  name: string;
  url: string;
  transport: "streamable-http" | "sse";
  headers: Record<string, string>;
  secret_headers?: string[];
  verify_tls: boolean;
  timeout: number;
  permission: string;
}

export interface McpTestResult {
  ok: boolean;
  transport: string;
  server_info: Record<string, string>;
  capabilities: Record<string, boolean>;
  tools: number;
  resources: number;
  prompts: number;
  error: string;
}

export interface McpResourceInfo {
  uri: string;
  name: string;
  description: string;
  mime_type: string;
}

export interface McpResourceTemplateInfo {
  uri_template: string;
  name: string;
  description: string;
  mime_type: string;
}

export interface McpResourceContent {
  uri: string;
  text: string;
  binary: boolean;
  size: number;
}

export interface McpPromptArgumentInfo {
  name: string;
  description: string;
  required: boolean;
}

export interface McpPromptInfo {
  name: string;
  description: string;
  arguments: McpPromptArgumentInfo[];
}

export interface McpPromptMessageInfo {
  role: string;
  text: string;
}

export interface McpPromptResult {
  name: string;
  description: string;
  messages: McpPromptMessageInfo[];
}

export interface McpImportPreview {
  entries: { id: string; name: string; transport: string; conflict: boolean }[];
  errors: string[];
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

export interface KnowledgeSource {
  id: number;
  path: string;
  document_count: number;
  enabled: boolean;
}

export interface KnowledgeProgress {
  total_files: number;
  processed_files: number;
  indexed_documents: number;
  failed_documents: number;
  current_file: string;
  embedded_chunks?: number;
  total_chunks?: number;
  started_at: string;
  finished_at: string;
  message: string;
}

export interface KnowledgeStatus {
  enabled: boolean;
  indexing: boolean;
  state: string;
  started?: boolean;
  progress: KnowledgeProgress;
  sources: KnowledgeSource[];
  documents: number;
  chunks: number;
  last_index_at: string;
  embedding: {
    model: string;
    available: boolean;
    reason: string;
    warning: string;
    dim: number;
  };
}

export interface KnowledgeSearchResult {
  chunk_id: number;
  path: string;
  absolute_path: string;
  heading: string;
  page: number | null;
  sheet: string;
  slide: number | null;
  score: number;
  text: string;
}

export interface KnowledgeSearchResponse {
  query: string;
  enabled: boolean;
  embedding_used: boolean;
  warning: string;
  results: KnowledgeSearchResult[];
  reason: string;
}
