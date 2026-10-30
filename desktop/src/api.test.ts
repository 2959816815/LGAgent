import { afterEach, describe, expect, it, vi } from "vitest";
import { cancelAgent, getSession, listAgents, setMcpEnabled, streamMessage } from "./api";

interface Call {
  url: string;
  method: string;
}

function stubFetch(): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), method: String(init?.method || "GET") });
      return { ok: true, status: 200, json: async () => ({ agents: [] }) } as unknown as Response;
    }),
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("setMcpEnabled", () => {
  it("posts to the backend /enable route", async () => {
    const calls = stubFetch();

    await setMcpEnabled("playwright", false);

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toContain("/api/mcp/servers/playwright/enable");
    expect(calls[0].url).not.toContain("/enabled");
  });
});

describe("listAgents", () => {
  it("filters by session when an id is given", async () => {
    const calls = stubFetch();

    await listAgents("20261030-120000-abcd");

    expect(calls[0].url).toContain("/api/agents?session_id=20261030-120000-abcd");
  });

  it("lists all agents without a session filter", async () => {
    const calls = stubFetch();

    await listAgents();

    expect(calls[0].url).toContain("/api/agents");
    expect(calls[0].url).not.toContain("session_id");
  });
});

describe("getSession", () => {
  it("asks for an earlier page with limit and before", async () => {
    const calls = stubFetch();

    await getSession("s1", { limit: 50, before: 120 });

    expect(calls[0].url).toContain("/api/sessions/s1?limit=50&before=120");
  });

  it("loads the whole session without paging options", async () => {
    const calls = stubFetch();

    await getSession("s1");

    expect(calls[0].url).toContain("/api/sessions/s1");
    expect(calls[0].url).not.toContain("limit=");
  });
});

describe("streamMessage", () => {
  it("passes regenerate and edit options to the backend", async () => {
    const bodies: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        bodies.push(JSON.parse(String(init?.body)));
        return new Response("event: done\ndata: {}\n\n", { status: 200 });
      }),
    );
    const signal = new AbortController().signal;

    for await (const _ of streamMessage("s1", "", signal, [], { mode: "regenerate" })) {
      // drain
    }
    for await (const _ of streamMessage("s1", "改后的问题", signal, [], { editIndex: 2 })) {
      // drain
    }

    expect(bodies[0]).toEqual({ text: "", attachments: [], mode: "regenerate" });
    expect(bodies[1]).toEqual({ text: "改后的问题", attachments: [], edit_index: 2 });
  });
});

describe("cancelAgent", () => {
  it("posts to the agent cancel route", async () => {
    const calls = stubFetch();

    await cancelAgent("agent-20260101-000000-abcd");

    expect(calls[0].method).toBe("POST");
    expect(calls[0].url).toContain("/api/agents/agent-20260101-000000-abcd/cancel");
  });
});
