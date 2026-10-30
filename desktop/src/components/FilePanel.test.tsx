import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const listFiles = vi.fn();
const searchFiles = vi.fn();

vi.mock("../api", () => ({
  listFiles: (...args: unknown[]) => listFiles(...args),
  searchFiles: (...args: unknown[]) => searchFiles(...args),
}));

import { FilePanel } from "./RightPanel";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("FilePanel", () => {
  it("reloads the tree when refresh is clicked", async () => {
    listFiles
      .mockResolvedValueOnce([{ name: "a.txt", path: "a.txt", type: "file", size: 1 }])
      .mockResolvedValueOnce([
        { name: "a.txt", path: "a.txt", type: "file", size: 1 },
        { name: "b.txt", path: "b.txt", type: "file", size: 2 },
      ]);

    render(<FilePanel onInsert={vi.fn()} />);
    expect(await screen.findByText("a.txt")).toBeTruthy();

    fireEvent.click(screen.getByText("刷新"));

    await waitFor(() => expect(listFiles).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("b.txt")).toBeTruthy();
  });
});
