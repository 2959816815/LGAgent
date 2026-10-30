import type { AgentRecord, Attachment, Message, SessionMeta } from "./types";

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function groupSessions(sessions: SessionMeta[]) {
  const now = new Date();
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const startYesterday = startToday - 86_400_000;
  const groups: { label: string; items: SessionMeta[] }[] = [
    { label: "今天", items: [] },
    { label: "昨天", items: [] },
    { label: "更早", items: [] },
  ];
  for (const session of sessions) {
    const time = new Date(session.updated_at).getTime();
    if (time >= startToday) groups[0].items.push(session);
    else if (time >= startYesterday) groups[1].items.push(session);
    else groups[2].items.push(session);
  }
  return groups.filter((group) => group.items.length > 0);
}

export function timeLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

export function statusLabel(status: AgentRecord["status"]): string {
  if (status === "running") return "运行中";
  if (status === "succeeded") return "已完成";
  if (status === "cancelled") return "已取消";
  return "失败";
}

/** 最后一条助手消息的下标；如果末尾是用户消息（还没有回复）则返回 -1。 */
export function lastAssistantIndex(messages: Message[]): number {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const role = messages[index].role;
    if (role === "assistant") return index;
    if (role === "user") return -1;
  }
  return -1;
}

export function formatCount(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}m`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(Math.max(0, Math.round(value)));
}

export function estimateContext(messages: Message[]): number {
  const chars = messages.reduce(
    (sum, message) =>
      sum + message.content.length + (message.attachments?.length ?? 0) * 4_000,
    0,
  );
  return Math.round(chars / 4);
}

export function readImageFile(file: File): Promise<Attachment> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () =>
      resolve({
        type: "image",
        data_url: String(reader.result || ""),
        name: file.name,
      });
    reader.onerror = () => reject(new Error(`读取图片失败：${file.name}`));
    reader.readAsDataURL(file);
  });
}
