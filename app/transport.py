"""HTTP 传输层：优先用系统自带的 curl.exe。

为什么不用 requests：实测本机环境下 Python 的 TLS 握手会被对端重置
（hf-mirror、pypi 都是 0/5 失败），而 curl 同样的地址 5/5 成功。
这是 TLS 指纹层面的差异，换证书校验、换代理都绕不开，所以直接改用 curl 做传输。

curl 在 Windows 10 1803 之后是系统自带的（C:\\Windows\\System32\\curl.exe），
所以这不会引入额外安装步骤。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

_CURL = shutil.which("curl") or r"C:\Windows\System32\curl.exe"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def curl_path() -> str:
    return _CURL


def available() -> bool:
    return bool(_CURL) and Path(_CURL).exists()


def _proxy_args(proxy: str | None) -> list[str]:
    proxy = (proxy or "").strip()
    if not proxy:
        return []
    if "://" not in proxy:
        proxy = "http://" + proxy
    return ["-x", proxy]


def get_json(url: str, params: dict | None = None, timeout: int = 45,
             proxy: str | None = None):
    """GET 一个 JSON 接口。失败时抛出带可读信息的异常。"""
    if params:
        query = urlencode({k: str(v) for k, v in params.items() if v is not None})
        url = f"{url}{'&' if '?' in url else '?'}{query}"
    cmd = [
        _CURL, "-sS", "-L",
        "--connect-timeout", "10",
        "--max-time", str(timeout),
        "-w", "\n%{http_code}",
        *_proxy_args(proxy),
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, creationflags=CREATE_NO_WINDOW)
    if proc.returncode != 0:
        message = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"curl 失败（{proc.returncode}）：{message[:200] or '网络不可达'}")
    raw = proc.stdout.decode("utf-8", "replace")
    body, _, code = raw.rpartition("\n")
    code = code.strip()
    if not code.isdigit():
        raise RuntimeError(f"无法解析响应：{raw[:120]}")
    status = int(code)
    if status >= 400:
        raise RuntimeError(f"HTTP {status}: {body[:200]}")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"返回的不是 JSON：{body[:120]}") from exc


def download_to(url: str, part: Path, expect: int, proxy: str | None = None,
                event: threading.Event | None = None, on_progress=None,
                connect_timeout: int = 15) -> str:
    """把 url 续传到 part 文件（curl -C - 自动从已有长度续传）。

    返回 "ok" / "canceled" / "restart"（服务端不支持断点，需要整份重来）。
    其它失败抛异常。
    """
    part.parent.mkdir(parents=True, exist_ok=True)
    before = part.stat().st_size if part.exists() else 0

    cmd = [
        _CURL, "-sS", "-L", "--fail",
        "--connect-timeout", str(connect_timeout),
        "--retry", "0",
        "-o", str(part),
        "-w", "%{http_code}",
        *_proxy_args(proxy),
    ]
    if before:
        cmd += ["-C", "-"]
    cmd.append(url)

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=CREATE_NO_WINDOW)
    seen = before

    def poll() -> None:
        nonlocal seen
        while proc.poll() is None:
            try:
                size = part.stat().st_size if part.exists() else seen
            except OSError:
                size = seen
            if on_progress and size > seen:
                on_progress(size - seen)
                seen = size
            time.sleep(0.3)
        try:
            size = part.stat().st_size if part.exists() else seen
        except OSError:
            size = seen
        if on_progress and size > seen:
            on_progress(size - seen)
            seen = size

    watcher = threading.Thread(target=poll, daemon=True)
    watcher.start()

    while proc.poll() is None:
        if event is not None and event.is_set():
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            watcher.join(timeout=2)
            return "canceled"
        time.sleep(0.2)

    watcher.join(timeout=3)
    out, err = proc.communicate()
    code = out.decode("utf-8", "replace").strip()
    if proc.returncode != 0:
        message = err.decode("utf-8", "replace").strip()
        # 33 = 服务端不支持 Range，34 = 请求区间无效：都退化成整份重下
        if proc.returncode in (33, 34) or code.startswith("416"):
            part.unlink(missing_ok=True)
            return "restart"
        raise RuntimeError(f"下载中断（curl {proc.returncode}）：{message[:200] or '未知错误'}")
    return "ok"


def ping(url: str, proxy: str | None = None, timeout: int = 20) -> tuple[bool, int, str]:
    """探测服务地址是否可用，返回 (ok, 毫秒, 说明)。"""
    started = time.time()
    try:
        data = get_json(url, {"search": "qwen", "limit": 1}, timeout=timeout, proxy=proxy)
        cost = int((time.time() - started) * 1000)
        if not isinstance(data, list):
            return False, cost, "这个地址返回的不是模型列表，可能不是 HuggingFace 镜像"
        return True, cost, f"连接正常（{cost} 毫秒）"
    except BaseException as exc:  # noqa: BLE001
        return False, int((time.time() - started) * 1000), str(exc)[:200]
