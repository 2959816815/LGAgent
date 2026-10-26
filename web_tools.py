"""HTTP browsing and file-download tools for the LangGraph agent.

This module deliberately uses plain HTTP instead of a visible browser:
- `web_fetch` retrieves a public HTML/text page and converts it to readable text.
- `download_file` streams a public URL to disk (software, archives, documents, ...).

Private/loopback/link-local addresses are rejected by default to avoid SSRF.
Set `WEB_TOOLS_ALLOW_PRIVATE=1` only for local development/testing.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import socket
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests
from langchain_core.tools import tool

_DEFAULT_MAX_CHARS = 20_000
_MIN_MAX_CHARS = 100
_MAX_MAX_CHARS = 50_000
_MAX_FETCH_BYTES = 5_000_000
_MAX_DOWNLOAD_BYTES = 500_000_000
_TIMEOUT = (10, 60)
_MAX_REDIRECTS = 6
_USER_AGENT = "LangGraph-Agent/1.0 (+https://localhost)"
_TEXT_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml+xml",
    "application/javascript",
    "application/x-javascript",
)
_BLOCK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "header",
    "footer",
    "main",
    "aside",
    "li",
    "tr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "pre",
    "blockquote",
}
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}


def _allow_private() -> bool:
    return os.getenv("WEB_TOOLS_ALLOW_PRIVATE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _resolve_host(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses: list[str] = []
    for info in infos:
        address = info[4][0]
        if address not in addresses:
            addresses.append(address)
    return addresses


def _ensure_public_url(url: str) -> str:
    """Validate an HTTP(S) URL and reject local/private targets."""
    text = (url or "").strip()
    if not text:
        raise ValueError("URL 不能为空。")
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("只支持 http/https URL。")
    if not parsed.hostname:
        raise ValueError("URL 缺少主机名。")
    if parsed.username or parsed.password:
        raise ValueError("URL 不能包含用户名或密码。")
    if _allow_private():
        return text
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = _resolve_host(parsed.hostname, port)
    except OSError as exc:
        raise ValueError(f"无法解析域名: {parsed.hostname}") from exc
    if not addresses:
        raise ValueError(f"无法解析域名: {parsed.hostname}")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("出于安全考虑，不能访问本机、内网或保留地址。")
    return text


def _open_response(url: str, *, stream: bool = True) -> requests.Response:
    """Open a URL with manual redirect handling and SSRF validation."""
    current = _ensure_public_url(url)
    for _ in range(_MAX_REDIRECTS + 1):
        response = requests.get(
            current,
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT,
            stream=stream,
            allow_redirects=False,
        )
        if response.is_redirect:
            location = response.headers.get("Location", "")
            response.close()
            if not location:
                raise ValueError("重定向缺少 Location。")
            current = _ensure_public_url(urljoin(current, location))
            continue
        return response
    raise ValueError("重定向次数过多。")


def _iter_bytes(response: requests.Response, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > limit:
            raise ValueError(f"内容超过限制（最多 {limit} 字节）。")
        chunks.append(chunk)
    return b"".join(chunks)


def _decode(raw: bytes, response: requests.Response) -> str:
    encoding = response.encoding
    if not encoding or encoding.lower() in {"iso-8859-1", "latin-1"}:
        try:
            from charset_normalizer import from_bytes

            best = from_bytes(raw).best()
            if best is not None and best.encoding:
                encoding = best.encoding
        except Exception:
            pass
    encoding = encoding or "utf-8"
    try:
        return raw.decode(encoding, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _coerce_max_chars(value: object) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _DEFAULT_MAX_CHARS
    return max(_MIN_MAX_CHARS, min(_MAX_MAX_CHARS, number))


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[已截断]"


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag == "br":
            self.parts.append("\n")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append(" | ")
        elif tag == "li":
            self.parts.append("\n- ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "title":
            self._in_title = False
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        else:
            self.parts.append(data)

    def text(self) -> tuple[str, str]:
        title = " ".join("".join(self.title_parts).split())
        raw = "".join(self.parts)
        lines = []
        for line in raw.splitlines():
            cleaned = re.sub(r"[ \t\u00a0]+", " ", line).strip()
            if cleaned:
                lines.append(cleaned)
        return title, "\n".join(lines)


def _html_to_text(html: str) -> tuple[str, str]:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # Last-resort fallback for malformed markup.
        text = re.sub(r"<[^>]+>", " ", html)
        return "", " ".join(text.split())
    return parser.text()


def _content_type(response: requests.Response) -> str:
    return response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()


def _error(message: str) -> str:
    return f"[错误] {message}"


@tool
def web_fetch(url: str, max_chars: int = _DEFAULT_MAX_CHARS) -> str:
    """读取网页并返回可读文本。

    url 必须是公开的 http/https 地址；max_chars 默认 20000，范围 100-50000。
    HTML 会提取标题和正文，脚本/样式会被忽略。需要下载二进制文件时用 download_file。
    """
    limit = _coerce_max_chars(max_chars)
    try:
        response = _open_response(url)
    except ValueError as exc:
        return _error(str(exc))
    except requests.exceptions.Timeout:
        return _error(f"请求超时（{_TIMEOUT[1]} 秒）。")
    except requests.exceptions.RequestException as exc:
        return _error(f"请求失败: {str(exc)[:200]}")

    try:
        if response.status_code >= 400:
            return _error(f"网页返回 HTTP {response.status_code}。")
        content_type = _content_type(response)
        if content_type and not any(
            content_type.startswith(prefix) or content_type == prefix
            for prefix in _TEXT_CONTENT_TYPES
        ):
            return _error(
                f"该地址返回 {content_type or '未知类型'}，不是文本网页；"
                "如果是软件/压缩包/图片，请用 download_file。"
            )
        try:
            raw = _iter_bytes(response, _MAX_FETCH_BYTES)
        except ValueError as exc:
            return _error(str(exc))
        text = _decode(raw, response)
        if "html" in content_type or "<html" in text[:2000].lower():
            title, body = _html_to_text(text)
        else:
            title, body = "", text
        final_url = str(response.url)
    finally:
        response.close()

    header = f"[网页] {title or final_url}\nURL: {final_url}"
    if body:
        return _truncate(f"{header}\n\n{body}", limit)
    return f"{header}\n\n(页面没有提取到正文)"


def _download_name(url: str, response: requests.Response) -> str:
    name = unquote(Path(urlparse(response.url).path).name)
    if not name:
        name = "download.bin"
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return (name or "download.bin")[:180]


def _download_target(path: str, url: str, response: requests.Response) -> Path:
    name = _download_name(url, response)
    raw = (path or "").strip()
    if not raw:
        return Path(__file__).resolve().parent / "downloads" / name
    expanded = os.path.expandvars(os.path.expanduser(raw))
    target = Path(expanded)
    if not target.is_absolute():
        target = Path.cwd() / target
    if target.is_dir() or raw.endswith(("/", "\\")):
        target = target / name
    return target


@tool
def download_file(
    url: str,
    path: str = "",
    overwrite: bool = False,
    max_bytes: int = _MAX_DOWNLOAD_BYTES,
) -> str:
    """从公开 URL 下载文件到磁盘。

    url 必须是公开 http/https 地址；path 为空时保存到项目的 downloads/ 目录。
    path 可以是文件路径或已存在目录；overwrite=true 才允许覆盖已有文件。
    max_bytes 默认 500MB，最大 2GB。返回保存路径、大小、SHA256 和内容类型。
    """
    try:
        limit = int(max_bytes)
    except (TypeError, ValueError):
        limit = _MAX_DOWNLOAD_BYTES
    limit = max(1, min(limit, 2_000_000_000))

    try:
        response = _open_response(url)
    except ValueError as exc:
        return _error(str(exc))
    except requests.exceptions.Timeout:
        return _error(f"下载请求超时（{_TIMEOUT[1]} 秒）。")
    except requests.exceptions.RequestException as exc:
        return _error(f"下载请求失败: {str(exc)[:200]}")

    temp: Path | None = None
    try:
        if response.status_code >= 400:
            return _error(f"下载地址返回 HTTP {response.status_code}。")
        target = _download_target(path, url, response)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not overwrite:
            return _error(f"目标文件已存在: {target}（覆盖请设置 overwrite=true）")

        declared = response.headers.get("Content-Length", "")
        if declared.isdigit() and int(declared) > limit:
            return _error(f"文件过大（Content-Length {declared}，上限 {limit} 字节）。")

        temp = target.with_name(target.name + ".part")
        digest = hashlib.sha256()
        total = 0
        with temp.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=256 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > limit:
                    return _error(f"文件超过上限（最多 {limit} 字节），已取消下载。")
                digest.update(chunk)
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
        temp = None
        content_type = _content_type(response) or "未知"
        return (
            f"[下载] {target}\n"
            f"[大小] {total} 字节\n"
            f"[SHA256] {digest.hexdigest()}\n"
            f"[类型] {content_type}"
        )
    except ValueError as exc:
        return _error(str(exc))
    except OSError as exc:
        return _error(f"写入文件失败: {str(exc)[:200]}")
    except requests.exceptions.RequestException as exc:
        return _error(f"下载中断: {str(exc)[:200]}")
    finally:
        response.close()
        if temp is not None:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
