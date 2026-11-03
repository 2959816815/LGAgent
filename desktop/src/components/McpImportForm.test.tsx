import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { McpImportForm } from "./McpImportForm";

afterEach(cleanup);

describe("McpImportForm", () => {
  it("previews before confirming", async () => {
    const onPreview = vi.fn(async () => ({
      entries: [{ id: "demo", name: "demo", transport: "stdio", conflict: false }],
      errors: [],
    }));
    const onConfirm = vi.fn(async () => undefined);
    render(<McpImportForm onPreview={onPreview} onConfirm={onConfirm} onCancel={vi.fn()} />);

    fireEvent.change(screen.getByPlaceholderText(/粘贴/), { target: { value: '{"mcpServers":{}}' } });
    fireEvent.click(screen.getByText("预览"));
    await waitFor(() => expect(onPreview).toHaveBeenCalled());
    expect(await screen.findByText(/demo · stdio/)).toBeTruthy();
    fireEvent.click(screen.getByText("确认导入"));
    await waitFor(() => expect(onConfirm).toHaveBeenCalled());
  });
});
