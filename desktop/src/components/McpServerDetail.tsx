import { useEffect, useState } from "react";
import {
  getMcpPrompt,
  getMcpPrompts,
  getMcpResources,
  readMcpResource,
} from "../api";
import type {
  McpPromptInfo,
  McpPromptResult,
  McpResourceInfo,
  McpResourceTemplateInfo,
  McpServerStatus,
} from "../types";

export function McpServerDetail(props: {
  server: McpServerStatus;
  onInsert: (text: string) => void;
}) {
  const [resources, setResources] = useState<McpResourceInfo[]>([]);
  const [templates, setTemplates] = useState<McpResourceTemplateInfo[]>([]);
  const [prompts, setPrompts] = useState<McpPromptInfo[]>([]);
  const [content, setContent] = useState("");
  const [promptArgs, setPromptArgs] = useState<Record<string, string>>({});
  const [promptResult, setPromptResult] = useState<McpPromptResult | null>(null);
  const [error, setError] = useState("");
  const remote =
    props.server.transport === "streamable-http" || props.server.transport === "sse";

  useEffect(() => {
    if (!remote) return;
    let cancelled = false;
    void (async () => {
      try {
        const resourceData = await getMcpResources(props.server.id);
        if (cancelled) return;
        setResources(resourceData.resources);
        setTemplates(resourceData.templates);
        const promptData = await getMcpPrompts(props.server.id);
        if (!cancelled) setPrompts(promptData.prompts);
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause));
      }
    })();
    return () => { cancelled = true; };
  }, [props.server.id, remote]);

  async function read(uri: string): Promise<void> {
    try {
      const data = await readMcpResource(props.server.id, uri);
      setContent(data.text);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function insertResource(resource: McpResourceInfo): Promise<void> {
    try {
      const data = await readMcpResource(props.server.id, resource.uri);
      props.onInsert(`[资源: ${resource.name} (${resource.uri})]\n${data.text}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function loadPrompt(prompt: McpPromptInfo): Promise<void> {
    try {
      const args = Object.fromEntries(
        prompt.arguments.map((arg) => [arg.name, promptArgs[arg.name] ?? ""]),
      );
      const result = await getMcpPrompt(props.server.id, prompt.name, args);
      setPromptResult(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  function insertPrompt(): void {
    if (!promptResult) return;
    const lines = [`[Prompt: ${promptResult.name}]`];
    for (const message of promptResult.messages) {
      lines.push(`[${message.role}]`, message.text);
    }
    props.onInsert(lines.join("\n"));
  }

  return (
    <div className="mcp-detail">
      <div className="mcp-detail-section">
        <strong>工具</strong>
        <div>{(props.server.tools ?? []).join("、") || "无"}</div>
      </div>
      {remote && (
        <>
          <div className="mcp-detail-section">
            <strong>资源（{resources.length}）</strong>
            {resources.map((resource) => (
              <div className="mcp-resource" key={resource.uri}>
                <span>{resource.name} · {resource.uri}</span>
                <button type="button" onClick={() => void read(resource.uri)}>读取</button>
                <button type="button" onClick={() => void insertResource(resource)}>插入</button>
              </div>
            ))}
            {resources.length === 0 && <div className="empty-hint">没有资源</div>}
            {content && <pre className="mcp-resource-content">{content}</pre>}
            {templates.length > 0 && (
              <div className="mcp-templates">
                {templates.map((template) => (
                  <div key={template.uri_template}>{template.name} · {template.uri_template}</div>
                ))}
              </div>
            )}
          </div>
          <div className="mcp-detail-section">
            <strong>Prompts（{prompts.length}）</strong>
            {prompts.map((prompt) => (
              <div className="mcp-prompt" key={prompt.name}>
                <div>{prompt.name} {prompt.description}</div>
                {prompt.arguments.map((arg) => (
                  <input
                    key={arg.name}
                    placeholder={`${arg.name}${arg.required ? "（必填）" : ""}`}
                    value={promptArgs[arg.name] ?? ""}
                    onChange={(event) => setPromptArgs((current) => ({ ...current, [arg.name]: event.target.value }))}
                  />
                ))}
                <button type="button" onClick={() => void loadPrompt(prompt)}>获取</button>
              </div>
            ))}
            {promptResult && (
              <div className="mcp-prompt-preview">
                <pre>{promptResult.messages.map((message) => `[${message.role}] ${message.text}`).join("\n")}</pre>
                <button type="button" onClick={insertPrompt}>插入输入框</button>
              </div>
            )}
          </div>
        </>
      )}
      {error && <div className="mcp-error">{error}</div>}
    </div>
  );
}
