import { describe, expect, it } from "vitest";
import { estimateContext } from "./helpers";
import type { Message } from "./types";

describe("estimateContext", () => {
  it("counts text at about one token per four characters", () => {
    const messages: Message[] = [{ role: "user", content: "字".repeat(400) }];

    expect(estimateContext(messages)).toBe(100);
  });

  it("counts each image at roughly 1000 tokens", () => {
    const textOnly: Message[] = [{ role: "user", content: "字".repeat(400) }];
    const withImage: Message[] = [
      {
        role: "user",
        content: "字".repeat(400),
        attachments: [{ type: "image", data_url: "data:image/png;base64,AAAA" }],
      },
    ];

    expect(estimateContext(withImage) - estimateContext(textOnly)).toBeGreaterThanOrEqual(
      900,
    );
  });
});
