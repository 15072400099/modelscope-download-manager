"""ModelScope 访问封装（纯 HTTP，零第三方依赖）。

只做四件事：搜索模型、读取详情、列出文件、列出版本。
所有请求都通过 transport（背后是系统 curl）发出。

为什么不用官方 modelscope 库：
1. 它会拖进 60MB+ 依赖（modelscope-hub / cryptography / requests …）
2. 它的续传路径在大文件 + 半截文件时会卡死（E1020），我们自己实现反而可控
"""

from __future__ import annotations

import threading
import time
from urllib.parse import quote

import transport

ENDPOINT = "https://www.modelscope.cn"
OPENAPI = "https://modelscope.cn/openapi/v1/models"

SORTS = {
    "downloads": "下载量最多",
    "default": "综合排序",
    "likes": "点赞最多",
    "last_modified": "最近更新",
}

_lock = threading.RLock()
_cache: dict[tuple, tuple[float, object]] = {}
_CACHE_TTL = 60.0

settings: dict = {"proxy": ""}


def configure(cfg: dict) -> None:
    with _lock:
        settings["proxy"] = (cfg.get("proxy") or "").strip()
        _cache.clear()


def proxy() -> str:
    return (settings.get("proxy") or "").strip()


def _get(url: str, params: dict | None = None, timeout: int = 60):
    return transport.get_json(url, params, timeout=timeout, proxy=proxy())


def _cached(key: tuple, producer):
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    value = producer()
    _cache[key] = (now, value)
    if len(_cache) > 400:
        for k in list(_cache)[:200]:
            _cache.pop(k, None)
    return value


def explain_error(exc: BaseException) -> str:
    """把底层异常翻译成用户能看懂的一句话。"""
    text = str(exc)
    low = text.lower()

    if "http 404" in low:
        return "找不到这个模型，请检查模型 ID 是否写对（形如 Qwen/Qwen2.5-7B-Instruct）"
    if "http 401" in low or "http 403" in low:
        return "这个模型需要登录授权，暂时无法直接下载"
    if "http 429" in low:
        return "请求太频繁被限流了，等一会儿再试"
    if "unihub" in low or "获取模型目录树失败" in text:
        return "魔搭服务器当前故障（他们的后台数据库出错），不是你电脑或网络的问题，过一段时间再试"
    if "http 5" in low:
        return "魔搭服务器返回错误（5xx），通常是他们那边的问题，过一会儿再试"
    if "resolve host" in low or "could not resolve" in low:
        return "域名解析失败，检查网络后重试"
    if "connection refused" in low or "connection reset" in low:
        return "连接被拒绝或重置，检查网络或代理设置"
    if "timed out" in low or "timeout" in low:
        return "连接超时，请检查网络后重试"
    if "curl 失败" in text:
        return f"网络请求失败：{text[:160]}"
    if "不是 json" in text:
        return "服务器返回的不是模型数据，可能接口变了"
    return f"{type(exc).__name__}: {text[:200]}"


# ---------------------------------------------------------------------------
# 搜索
# ---------------------------------------------------------------------------
def search_models(query: str, page: int = 1, page_size: int = 12, sort: str = "downloads") -> dict:
    query = (query or "").strip()
    sort = sort if sort in SORTS else "downloads"
    page = max(1, int(page))
    key = ("search", query, page, page_size, sort)

    def produce():
        params = {"page_number": page, "page_size": page_size, "sort": sort}
        if query:
            params["search"] = query
        payload = _get(OPENAPI, params) or {}
        data = payload.get("data") or {}
        rows = data.get("models") or []
        total = int(data.get("total_count") or 0)
        items = []
        for row in rows:
            repo_id = row.get("id") or ""
            if not repo_id:
                continue
            tasks = row.get("tasks")
            if isinstance(tasks, str):
                tasks = [t for t in tasks.split(",") if t] if tasks else []
            items.append({
                "repo_id": repo_id,
                "display_name": row.get("display_name") or repo_id.split("/")[-1],
                "downloads": row.get("downloads") or 0,
                "likes": row.get("likes") or 0,
                "tasks": tasks or [],
                "license": row.get("license") or "",
                "description": (row.get("description") or "").strip(),
                "file_size": row.get("file_size") or 0,
                "last_modified": row.get("last_modified"),
            })
        return {"items": items, "page": page, "page_size": page_size,
                "has_more": page * page_size < total}

    try:
        return _cached(key, produce)
    except Exception as exc:  # noqa: BLE001
        return {"items": [], "page": page, "page_size": page_size, "has_more": False,
                "error": explain_error(exc)}


# ---------------------------------------------------------------------------
# 详情 / 文件列表 / 版本
# ---------------------------------------------------------------------------
def model_detail(repo_id: str) -> dict:
    repo_id = normalize_repo_id(repo_id)
    if not repo_id:
        return {"error": "请输入模型 ID，形如 Qwen/Qwen2.5-7B-Instruct"}

    def produce():
        payload = _get(f"{ENDPOINT}/api/v1/models/{repo_id}") or {}
        data = payload.get("Data") or {}
        tasks = data.get("Tasks") or []
        if isinstance(tasks, str):
            tasks = [tasks]
        return {
            "repo_id": repo_id,
            "display_name": data.get("display_name") or repo_id.split("/")[-1],
            "description": (data.get("Description") or "").strip(),
            "downloads": data.get("Downloads") or 0,
            "likes": data.get("Likes") or 0,
            "license": data.get("License") or "",
            "tags": data.get("Tags") or [],
            "tasks": tasks,
            "file_size": data.get("file_size") or 0,
            "gated": bool(data.get("gated")),
        }

    try:
        return _cached(("detail", repo_id), produce)
    except Exception as exc:  # noqa: BLE001
        return {"error": explain_error(exc)}


def list_files(repo_id: str, revision: str = "master") -> dict:
    repo_id = normalize_repo_id(repo_id)
    revision = (revision or "master").strip()

    def produce():
        payload = _get(f"{ENDPOINT}/api/v1/models/{repo_id}/repo/files",
                       {"Revision": revision, "Recursive": "true"}) or {}
        data = payload.get("Data") or {}
        files, total = [], 0
        for item in data.get("Files") or []:
            path = item.get("Path") or item.get("Name") or ""
            if not path or item.get("Type") == "tree":
                continue
            size = int(item.get("Size") or 0)
            files.append({"path": path, "size": size})
            total += size
        files.sort(key=lambda f: f["path"])
        return {"repo_id": repo_id, "revision": revision, "files": files,
                "total_files": len(files), "total_size": total}

    try:
        return _cached(("files", repo_id, revision), produce)
    except Exception as exc:  # noqa: BLE001
        return {"error": explain_error(exc), "files": [], "total_files": 0, "total_size": 0}


def list_revisions(repo_id: str) -> dict:
    repo_id = normalize_repo_id(repo_id)

    def produce():
        payload = _get(f"{ENDPOINT}/api/v1/models/{repo_id}/revisions") or {}
        data = payload.get("Data") or {}
        revision_map = data.get("RevisionMap") or {}
        names = ["master"]
        for key in ("Branches", "Tags"):
            for row in revision_map.get(key) or []:
                name = row.get("Revision")
                if name and name not in names:
                    names.append(name)
        return names

    try:
        return {"revisions": _cached(("refs", repo_id), produce)}
    except Exception as exc:  # noqa: BLE001
        return {"revisions": ["master"], "error": explain_error(exc)}


def file_url(repo_id: str, revision: str, path: str) -> str:
    return (f"{ENDPOINT}/api/v1/models/{repo_id}/repo"
            f"?Revision={quote(revision or 'master')}&FilePath={quote(path)}")


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def normalize_repo_id(text: str) -> str:
    """接受模型 ID、模型页面链接，统一成 owner/name。"""
    text = (text or "").strip().strip('"').strip("'")
    if not text:
        return ""
    lowered = text.lower()
    for marker in ("/models/", "/model/"):
        if marker in lowered:
            text = text[lowered.index(marker) + len(marker):]
            break
    text = text.split("?")[0].split("#")[0].replace("\\", "/").strip("/")
    parts = [p for p in text.split("/") if p]
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return ""


def looks_like_repo_id(text: str) -> bool:
    text = (text or "").strip()
    if "modelscope" in text.lower():
        return True
    return "/" in text and len(text.split("/")) == 2 and " " not in text
