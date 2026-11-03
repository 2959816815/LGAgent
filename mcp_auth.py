"""Auth provider seam for remote MCP servers."""

from __future__ import annotations

from typing import Protocol


class RemoteAuthProvider(Protocol):
    def prepare(self, config: dict) -> dict[str, str]:
        ...

    def on_auth_error(self, status_code: int, body: str) -> None:
        ...


class StaticHeaderAuthProvider:
    """Uses the headers already resolved by the manager."""

    def prepare(self, config: dict) -> dict[str, str]:
        return {
            str(key): str(value)
            for key, value in (config.get("headers") or {}).items()
        }

    def on_auth_error(self, status_code: int, body: str) -> None:
        return None
