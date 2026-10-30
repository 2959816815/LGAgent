import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsDialog } from "./SettingsDialog";

const getSettings = vi.fn();
const saveSettings = vi.fn();
const testSettings = vi.fn();

beforeEach(() => {
  getSettings.mockResolvedValue({
    baseUrl: "https://api.deepseek.com",
    model: "deepseek-flash",
    hasApiKey: true,
  });
  saveSettings.mockResolvedValue({ baseUrl: "http://127.0.0.1:1", token: "t" });
  testSettings.mockResolvedValue({ ok: true });
  (window as unknown as { desktop: unknown }).desktop = {
    getSettings,
    saveSettings,
    testSettings,
  };
});

afterEach(() => {
  cleanup();
  delete (window as unknown as { desktop?: unknown }).desktop;
  vi.clearAllMocks();
});

describe("SettingsDialog", () => {
  it("shows the saved configuration without exposing the key", async () => {
    render(<SettingsDialog onClose={vi.fn()} onSaved={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByLabelText("Base URL")).toHaveValue("https://api.deepseek.com");
    });
    expect(screen.getByLabelText("模型")).toHaveValue("deepseek-flash");
    expect(screen.getByPlaceholderText("留空表示不修改")).toBeTruthy();
  });

  it("saves changes without re-entering the key", async () => {
    const onSaved = vi.fn();
    render(<SettingsDialog onClose={vi.fn()} onSaved={onSaved} />);

    await waitFor(() => {
      expect(screen.getByLabelText("Base URL")).toHaveValue("https://api.deepseek.com");
    });
    fireEvent.change(screen.getByLabelText("模型"), { target: { value: "deepseek-v4-pro" } });
    fireEvent.click(screen.getByText("保存"));

    await waitFor(() => {
      expect(saveSettings).toHaveBeenCalledWith({
        apiKey: "",
        baseUrl: "https://api.deepseek.com",
        model: "deepseek-v4-pro",
      });
    });
    expect(onSaved).toHaveBeenCalled();
  });

  it("tests the connection with the stored key", async () => {
    render(<SettingsDialog onClose={vi.fn()} onSaved={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByLabelText("Base URL")).toHaveValue("https://api.deepseek.com");
    });
    fireEvent.click(screen.getByText("测试连接"));

    expect(await screen.findByText("连接成功")).toBeTruthy();
    expect(testSettings).toHaveBeenCalledWith({
      apiKey: "",
      baseUrl: "https://api.deepseek.com",
      model: "deepseek-flash",
    });
  });
});
