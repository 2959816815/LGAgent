import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SetupWizard } from "./SetupWizard";

describe("SetupWizard", () => {
  it("tests then saves the key", async () => {
    const saveDeepSeek = vi.fn().mockResolvedValue({ baseUrl: "x", token: "t" });
    const testDeepSeek = vi.fn().mockResolvedValue({ ok: true });
    (window as any).desktop = { saveDeepSeek, testDeepSeek };
    const onDone = vi.fn();

    render(
      <SetupWizard
        initialState={{ baseUrl: "https://api.deepseek.com", model: "deepseek-flash" }}
        onDone={onDone}
      />,
    );

    fireEvent.change(screen.getByLabelText("API Key"), { target: { value: "sk-test" } });
    fireEvent.click(screen.getByText("测试连接"));
    await waitFor(() => expect(testDeepSeek).toHaveBeenCalled());

    fireEvent.click(screen.getByText("保存并开始"));
    await waitFor(() => expect(saveDeepSeek).toHaveBeenCalled());
    expect(onDone).toHaveBeenCalled();
  });
});
