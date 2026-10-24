export type ModelKey = "local" | "deepseek" | "qwen";

export interface ModelInfo {
  key: ModelKey;
  label: string;
  model: string;
  available: boolean;
  reason?: string;
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
