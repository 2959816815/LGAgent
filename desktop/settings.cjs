"use strict";

const fs = require("node:fs");
const path = require("node:path");

const DEFAULTS = {
  baseUrl: "https://api.deepseek.com",
  model: "deepseek-flash",
};

function createSettingsStore({ filePath, encrypt, decrypt }) {
  const empty = () => ({ version: 1, deepseek: null });

  function read() {
    try {
      const data = JSON.parse(fs.readFileSync(filePath, "utf8"));
      return data && typeof data === "object" ? data : empty();
    } catch {
      return empty();
    }
  }

  function write(data) {
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    const temp = `${filePath}.tmp`;
    fs.writeFileSync(temp, JSON.stringify(data, null, 2), "utf8");
    fs.renameSync(temp, filePath);
  }

  function getDeepSeek() {
    const data = read();
    if (!data.deepseek || !data.deepseek.key_encrypted) return null;
    try {
      return {
        baseUrl: data.deepseek.base_url || DEFAULTS.baseUrl,
        model: data.deepseek.model || DEFAULTS.model,
        apiKey: decrypt(data.deepseek.key_encrypted),
      };
    } catch {
      return null;
    }
  }

  function needsSetup() {
    return getDeepSeek() === null;
  }

  function saveDeepSeek({ apiKey, baseUrl = DEFAULTS.baseUrl, model = DEFAULTS.model }) {
    const trimmed = String(apiKey || "").trim();
    if (!trimmed) throw new Error("API Key 不能为空");
    write({
      version: 1,
      deepseek: {
        base_url: String(baseUrl || DEFAULTS.baseUrl).trim(),
        model: String(model || DEFAULTS.model).trim(),
        key_encrypted: encrypt(trimmed),
      },
    });
  }

  return { filePath, read, needsSetup, getDeepSeek, saveDeepSeek };
}

module.exports = { createSettingsStore, DEFAULTS };
