"""模型注册表：集中管理本地 Ollama、DeepSeek、DashScope 三个模型配置。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from langchain_openai import ChatOpenAI

DEFAULT_MODEL_KEY = "local"

_PROVIDER_KEYS = {
    "ollama": "local",
    "deepseek": "deepseek",
    "dashscope": "qwen",
    "qwen": "qwen",
}

_ALIASES = {
    "1": "local",
    "local": "local",
    "ollama": "local",
    "qwen3.5": "local",
    "本地": "local",
    "2": "deepseek",
    "deepseek": "deepseek",
    "deepseek-flash": "deepseek",
    "deepseek-v4.1-flash": "deepseek",
    "v4.1-flash": "deepseek",
    "3": "qwen",
    "qwen": "qwen",
    "qwen3-vl": "qwen",
    "qwen3-vl-plus": "qwen",
    "qwen-vl": "qwen",
    "千问": "qwen",
}


class ModelConfigError(RuntimeError):
    """模型配置缺失或未知。"""


@dataclass(frozen=True)
class ModelProfile:
    key: str
    label: str
    provider: str
    model: str
    base_url: str
    api_key: str | None
    key_env: str | None
    timeout: float
    max_retries: int
    supports_vision: bool
    context_window: int


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _dynamic_profiles() -> list[ModelProfile] | None:
    """解析桌面端传来的 DSH_MODELS_JSON（用户在设置页配置的模型）。

    解析失败或没有有效条目时返回 None，调用方回退到内置三模型。
    """
    raw = os.getenv("DSH_MODELS_JSON", "").strip()
    if not raw:
        return None
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(items, list) or not items:
        return None

    profiles: list[ModelProfile] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or "").strip()
        model = str(item.get("model") or "").strip()
        base_url = str(item.get("base_url") or "").strip()
        if not key or not model or not base_url:
            continue
        kind = str(item.get("kind") or "api").strip().lower()
        label = str(item.get("label") or key).strip()
        if kind == "local":
            profiles.append(
                ModelProfile(
                    key=key,
                    label=label,
                    provider="ollama",
                    model=model,
                    base_url=base_url,
                    api_key="ollama",
                    key_env=None,
                    timeout=float(_env("OLLAMA_TIMEOUT", "300")),
                    max_retries=0,
                    supports_vision=False,
                    context_window=int(_env("OLLAMA_CONTEXT_WINDOW", "4096")),
                )
            )
        else:
            profiles.append(
                ModelProfile(
                    key=key,
                    label=label,
                    provider="custom",
                    model=model,
                    base_url=base_url,
                    api_key=str(item.get("api_key") or ""),
                    key_env=None,
                    timeout=float(item.get("timeout") or 60),
                    max_retries=0,
                    supports_vision=bool(item.get("supports_vision")),
                    context_window=int(item.get("context_window") or 64_000),
                )
            )
    return profiles or None


def list_profiles() -> list[ModelProfile]:
    """每次读取环境变量，便于测试和运行时切换后读取最新 .env。"""
    dynamic = _dynamic_profiles()
    if dynamic is not None:
        return dynamic
    return [
        ModelProfile(
            key="local",
            label="本地 qwen3.5",
            provider="ollama",
            model=_env("OLLAMA_MODEL", "qwen3.5:2b"),
            base_url=_env("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"),
            api_key="ollama",
            key_env=None,
            timeout=float(_env("OLLAMA_TIMEOUT", "300")),
            max_retries=0,
            supports_vision=False,
            context_window=int(_env("OLLAMA_CONTEXT_WINDOW", "4096")),
        ),
        ModelProfile(
            key="deepseek",
            label="deepseek-v4.1-flash",
            provider="deepseek",
            model=_env("DEEPSEEK_MODEL", "deepseek-flash"),
            base_url=_env("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            api_key=_env("DEEPSEEK_API_KEY", ""),
            key_env="DEEPSEEK_API_KEY",
            timeout=60,
            max_retries=2,
            supports_vision=False,
            context_window=128_000,
        ),
        ModelProfile(
            key="qwen",
            label="千问多模态",
            provider="dashscope",
            model=_env("QWEN_MODEL", "qwen3-vl-plus"),
            base_url=_env(
                "DASHSCOPE_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            api_key=_env("DASHSCOPE_API_KEY", ""),
            key_env="DASHSCOPE_API_KEY",
            timeout=60,
            max_retries=2,
            supports_vision=True,
            context_window=262_144,
        ),
    ]


def resolve_selector(selector: str) -> str:
    """把数字、key 或别名归一化为 local/deepseek/qwen。"""
    raw = selector.strip().lower()
    if not raw:
        raise ModelConfigError("未知模型: 空；输入 /model 查看可用模型。")
    # 已配置模型的 id 优先：用户可能拿 "qwen3-vl-plus" 这类内置别名当 id
    if any(profile.key == raw for profile in list_profiles()):
        return raw
    try:
        return _ALIASES[raw]
    except KeyError:
        pass
    raise ModelConfigError(f"未知模型: {selector}；输入 /model 查看可用模型。")


def get_profile(key: str) -> ModelProfile:
    canonical = resolve_selector(key)
    for profile in list_profiles():
        if profile.key == canonical:
            return profile
    raise ModelConfigError(f"未知模型: {key}；输入 /model 查看可用模型。")


def initial_model_key() -> str:
    raw = os.getenv("LLM_PROVIDER", "").strip().lower()
    candidate = _PROVIDER_KEYS.get(raw, DEFAULT_MODEL_KEY)
    keys = [profile.key for profile in list_profiles()]
    if candidate in keys:
        return candidate
    return keys[0] if keys else DEFAULT_MODEL_KEY


def build_chat_model(key: str) -> ChatOpenAI:
    profile = get_profile(key)
    if profile.key != "local" and not profile.api_key:
        raise ModelConfigError(
            f"未找到 {profile.key_env}，无法切换到 {profile.label}。请检查 .env。"
        )
    return ChatOpenAI(
        model=profile.model,
        api_key=profile.api_key or "ollama",
        base_url=profile.base_url,
        temperature=0,
        timeout=profile.timeout,
        max_retries=profile.max_retries,
    )


def model_table(current_key: str) -> str:
    current = get_profile(current_key)
    lines = [f"[模型] 当前: {current.key} ({current.label})"]
    for index, profile in enumerate(list_profiles(), 1):
        lines.append(
            f"  {index}. {profile.key} | {profile.label} | "
            f"{profile.model} | {profile.base_url}"
        )
    return "\n".join(lines)
