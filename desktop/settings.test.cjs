"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createSettingsStore, DEFAULTS } = require("./settings.cjs");

function tempStore() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "lg-settings-"));
  const file = path.join(dir, "settings.json");
  return createSettingsStore({
    filePath: file,
    encrypt: (value) => Buffer.from(value, "utf8").toString("base64"),
    decrypt: (value) => Buffer.from(value, "base64").toString("utf8"),
  });
}

test("needs setup when no settings file", () => {
  assert.equal(tempStore().needsSetup(), true);
});

test("saves and reads DeepSeek settings without plaintext key", () => {
  const store = tempStore();
  store.saveDeepSeek({ apiKey: "sk-test", baseUrl: DEFAULTS.baseUrl, model: DEFAULTS.model });
  assert.equal(store.needsSetup(), false);
  assert.equal(store.getDeepSeek().apiKey, "sk-test");
  assert.equal(fs.readFileSync(store.filePath, "utf8").includes("sk-test"), false);
});

test("rejects empty api key", () => {
  const store = tempStore();
  assert.throws(() => store.saveDeepSeek({ apiKey: "  " }), /API Key/);
});
