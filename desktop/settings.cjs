"use strict";

const fs = require("node:fs");
const path = require("node:path");

const DEFAULTS = {
  baseUrl: "https://api.deepseek.com",
  model: "deepseek-flash",
};

const LOCAL_DEFAULTS = {
  baseUrl: "http://127.0.0.1:11434/v1",
  model: "qwen3.5:2b",
};

const MODEL_ID = /^[a-z0-9][a-z0-9_-]{0,31}$/;
const KINDS = new Set(["api", "local"]);

function createSettingsStore({ filePath, encrypt, decrypt }) {
  function defaultModels() {
    return [
      {
        id: "deepseek",
        kind: "api",
        label: "deepseek-v4.1-flash",
        base_url: DEFAULTS.baseUrl,
        model: DEFAULTS.model,
        supports_vision: false,
        key_encrypted: null,
      },
      {
        id: "local",
        kind: "local",
        label: "本地 qwen3.5",
        base_url: LOCAL_DEFAULTS.baseUrl,
        model: LOCAL_DEFAULTS.model,
        supports_vision: false,
        key_encrypted: null,
      },
    ];
  }

  function empty() {
    return { version: 2, models: defaultModels() };
  }

  function sanitizeStored(item) {
    if (!item || typeof item !== "object") return null;
    const id = String(item.id || "").trim().toLowerCase();
    if (!MODEL_ID.test(id)) return null;
    const kind = KINDS.has(item.kind) ? item.kind : "api";
    const baseUrl = String(item.base_url || "").trim();
    const model = String(item.model || "").trim();
    if (!baseUrl || !model) return null;
    return {
      id,
      kind,
      label: String(item.label || id).trim() || id,
      base_url: baseUrl,
      model,
      supports_vision: Boolean(item.supports_vision),
      key_encrypted:
        typeof item.key_encrypted === "string" ? item.key_encrypted : null,
    };
  }

  function read() {
    let raw;
    try {
      raw = JSON.parse(fs.readFileSync(filePath, "utf8"));
    } catch {
      return empty();
    }
    if (!raw || typeof raw !== "object") return empty();

    if (Array.isArray(raw.models)) {
      const models = raw.models.map(sanitizeStored).filter(Boolean);
      return { version: 2, models: models.length ? models : defaultModels() };
    }

    // v1 格式：只有单个 deepseek 块，迁移成模型列表
    const models = defaultModels();
    const legacy = raw.deepseek;
    if (legacy && typeof legacy === "object") {
      const entry = models.find((item) => item.id === "deepseek");
      entry.base_url = String(legacy.base_url || DEFAULTS.baseUrl).trim();
      entry.model = String(legacy.model || DEFAULTS.model).trim();
      entry.key_encrypted =
        typeof legacy.key_encrypted === "string" ? legacy.key_encrypted : null;
    }
    return { version: 2, models };
  }

  function write(data) {
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    const temp = `${filePath}.tmp`;
    fs.writeFileSync(temp, JSON.stringify(data, null, 2), "utf8");
    fs.renameSync(temp, filePath);
  }

  function safeDecrypt(value) {
    if (!value) return "";
    try {
      return decrypt(value);
    } catch {
      return "";
    }
  }

  function getModels() {
    return read().models.map(({ key_encrypted, ...rest }) => ({
      ...rest,
      hasKey: Boolean(key_encrypted),
    }));
  }

  /** 主进程传给 sidecar 的完整配置（含明文 Key，仅存在于进程环境）。 */
  function getModelsForSidecar() {
    return read().models.map((item) => ({
      id: item.id,
      kind: item.kind,
      label: item.label,
      base_url: item.base_url,
      model: item.model,
      supports_vision: Boolean(item.supports_vision),
      api_key:
        item.kind === "api" && item.key_encrypted
          ? safeDecrypt(item.key_encrypted)
          : "",
    }));
  }

  function saveModels(models) {
    if (!Array.isArray(models) || models.length === 0) {
      throw new Error("至少保留一个模型");
    }
    const current = read().models;
    const seen = new Set();
    const next = models.map((item) => {
      const id = String((item && item.id) || "").trim().toLowerCase();
      if (!MODEL_ID.test(id)) {
        throw new Error("模型 id 只能是小写字母、数字、下划线和短横线");
      }
      if (seen.has(id)) throw new Error(`模型 id 重复: ${id}`);
      seen.add(id);

      const kind = String((item && item.kind) || "api");
      if (!KINDS.has(kind)) throw new Error("模型类型只能是 api 或 local");
      const baseUrl = String((item && item.base_url) || "").trim();
      if (!/^https?:\/\//i.test(baseUrl)) throw new Error(`模型地址无效: ${id}`);
      const model = String((item && item.model) || "").trim();
      if (!model) throw new Error(`模型名称不能为空: ${id}`);
      const label = String((item && item.label) || id).trim() || id;

      const previous = current.find((entry) => entry.id === id);
      const provided = String((item && item.api_key) || "").trim();
      let keyEncrypted = previous ? previous.key_encrypted : null;
      if (kind === "local") keyEncrypted = null;
      else if (provided) keyEncrypted = encrypt(provided);

      const supportsVision =
        kind === "local"
          ? false
          : item.supports_vision === undefined
            ? (previous && previous.supports_vision) || false
            : Boolean(item.supports_vision);

      return {
        id,
        kind,
        label,
        base_url: baseUrl,
        model,
        supports_vision: supportsVision,
        key_encrypted: keyEncrypted,
      };
    });
    write({ version: 2, models: next });
  }

  function getDeepSeek() {
    const entry = read().models.find(
      (item) => item.id === "deepseek" && item.key_encrypted,
    );
    if (!entry) return null;
    const apiKey = safeDecrypt(entry.key_encrypted);
    if (!apiKey) return null;
    return { baseUrl: entry.base_url, model: entry.model, apiKey };
  }

  function needsSetup() {
    return !read().models.some(
      (item) => item.kind === "api" && item.key_encrypted,
    );
  }

  function saveDeepSeek({ apiKey, baseUrl = DEFAULTS.baseUrl, model = DEFAULTS.model }) {
    const trimmed = String(apiKey || "").trim();
    if (!trimmed) throw new Error("API Key 不能为空");
    const models = read().models;
    const existing = models.find((item) => item.id === "deepseek");
    const entry = {
      id: "deepseek",
      kind: "api",
      label: existing ? existing.label : "deepseek-v4.1-flash",
      base_url: String(baseUrl || DEFAULTS.baseUrl).trim(),
      model: String(model || DEFAULTS.model).trim(),
      supports_vision: existing ? Boolean(existing.supports_vision) : false,
      key_encrypted: encrypt(trimmed),
    };
    const next = existing
      ? models.map((item) => (item.id === "deepseek" ? entry : item))
      : [entry, ...models];
    write({ version: 2, models: next });
  }

  return {
    filePath,
    read,
    needsSetup,
    getDeepSeek,
    saveDeepSeek,
    getModels,
    getModelsForSidecar,
    saveModels,
  };
}

module.exports = { createSettingsStore, DEFAULTS, LOCAL_DEFAULTS };
