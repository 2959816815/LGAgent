"""模型注册表：集中管理本地 Ollama、DeepSeek、DashScope 三个模型配置。"""

from __future__ import annotations

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


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def list_profiles() -> list[ModelProfile]:
    """每次读取环境变量，便于测试和运行时切换后读取最新 .env。"""
    return [
        ModelProfile(
            key="local",
            label="本地 qwen3.5",
            provider="ollama",
            model=_env("OLLAMA_MODEL", "qwen3.5:2b"),
            base_url=_env("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"),
            api_key="ollama",
            key_env=None,
            timeout=120,
            max_retries=1,
            supports_vision=False,
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
        ),
    ]


def resolve_selector(selector: str) -> str:
    """把数字、key 或别名归一化为 local/deepseek/qwen。"""
    raw = selector.strip().lower()
    if not raw:
        raise ModelConfigError("未知模型: 空；输入 /model 查看可用模型。")
    try:
        return _ALIASES[raw]
    except KeyError:
        raise ModelConfigError(
            f"未知模型: {selector}；输入 /model 查看可用模型。"
        ) from None


def get_profile(key: str) -> ModelProfile:
    canonical = resolve_selector(key)
    for profile in list_profiles():
        if profile.key == canonical:
            return profile
    raise ModelConfigError(f"未知模型: {key}；输入 /model 查看可用模型。")


def initial_model_key() -> str:
    raw = os.getenv("LLM_PROVIDER", "").strip().lower()
    return _PROVIDER_KEYS.get(raw, DEFAULT_MODEL_KEY)


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
