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

test("fresh store ships a local Ollama model", () => {
  const models = tempStore().getModels();
  const local = models.find((item) => item.id === "local");
  assert.ok(local);
  assert.equal(local.kind, "local");
  assert.match(local.base_url, /11434/);
});

test("saves and reads DeepSeek settings without plaintext key", () => {
  const store = tempStore();
  store.saveDeepSeek({ apiKey: "sk-test", baseUrl: DEFAULTS.baseUrl, model: DEFAULTS.model });
  assert.equal(store.needsSetup(), false);
  assert.equal(store.getDeepSeek().apiKey, "sk-test");
  assert.equal(fs.readFileSync(store.filePath, "utf8").includes("sk-test"), false);
});

test("getModels never exposes the key", () => {
  const store = tempStore();
  store.saveDeepSeek({ apiKey: "sk-secret" });
  const models = store.getModels();
  const deepseek = models.find((item) => item.id === "deepseek");
  assert.equal(deepseek.hasKey, true);
  assert.equal(JSON.stringify(models).includes("sk-secret"), false);
});

test("saveModels keeps the existing key when api_key is omitted", () => {
  const store = tempStore();
  store.saveDeepSeek({ apiKey: "sk-old" });
  const models = store.getModels();
  const deepseek = models.find((item) => item.id === "deepseek");
  models.push({
    id: "myapi",
    kind: "api",
    label: "我的 API",
    base_url: "https://example.com/v1",
    model: "foo-1",
    api_key: "",
  });
  store.saveModels(models);
  const sidecar = store.getModelsForSidecar();
  assert.equal(sidecar.find((item) => item.id === "deepseek").api_key, "sk-old");
  assert.equal(sidecar.find((item) => item.id === "myapi").api_key, "");
});

test("saveModels stores a new key encrypted", () => {
  const store = tempStore();
  const models = store.getModels();
  models.push({
    id: "myapi",
    kind: "api",
    label: "我的 API",
    base_url: "https://example.com/v1",
    model: "foo-1",
    api_key: "sk-new",
  });
  store.saveModels(models);
  assert.equal(store.getModelsForSidecar().find((item) => item.id === "myapi").api_key, "sk-new");
  assert.equal(fs.readFileSync(store.filePath, "utf8").includes("sk-new"), false);
});

test("keeps supports_vision for vision models", () => {
  const store = tempStore();
  const models = store.getModels();
  models.push({
    id: "qwenvl",
    kind: "api",
    label: "千问 VL",
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    model: "qwen3-vl-plus",
    api_key: "sk-x",
    supports_vision: true,
  });
  store.saveModels(models);
  assert.equal(
    store.getModelsForSidecar().find((item) => item.id === "qwenvl").supports_vision,
    true,
  );
  assert.equal(
    store.getModels().find((item) => item.id === "qwenvl").supports_vision,
    true,
  );
});

test("saveModels rejects invalid entries", () => {
  const store = tempStore();
  assert.throws(
    () => store.saveModels([{ id: "BAD ID", kind: "api", base_url: "https://x", model: "m", label: "x" }]),
    /id/,
  );
  assert.throws(
    () => store.saveModels([{ id: "ok", kind: "api", base_url: "ftp://x", model: "m", label: "x" }]),
    /地址/,
  );
});

test("migrates the legacy deepseek block", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "lg-settings-"));
  const file = path.join(dir, "settings.json");
  fs.writeFileSync(
    file,
    JSON.stringify({
      version: 1,
      deepseek: {
        base_url: "https://api.deepseek.com",
        model: "deepseek-flash",
        key_encrypted: Buffer.from("sk-legacy", "utf8").toString("base64"),
      },
    }),
  );
  const store = createSettingsStore({
    filePath: file,
    encrypt: (value) => Buffer.from(value, "utf8").toString("base64"),
    decrypt: (value) => Buffer.from(value, "base64").toString("utf8"),
  });
  const deepseek = store.getModels().find((item) => item.id === "deepseek");
  assert.equal(deepseek.hasKey, true);
  assert.equal(store.getDeepSeek().apiKey, "sk-legacy");
});
