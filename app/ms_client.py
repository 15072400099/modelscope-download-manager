"""ModelScope 访问封装。

只做四件事：搜索模型、读取模型详情、列出模型文件、列出可用版本。
所有网络异常都会转换成中文提示，方便界面直接展示。
"""

from __future__ import annotations

import os

# tqdm 的进度条会刷到服务器控制台，先把它的自动输出关掉（必须在导入 tqdm 之前设置）
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

import threading
import time

from modelscope.hub.api import HubApi

_lock = threading.RLock()
_api = None
_cache: dict[tuple, tuple[float, object]] = {}
_CACHE_TTL = 60.0

SORTS = {
    "default": "综合排序",
    "downloads": "下载量最多",
    "likes": "点赞最多",
    "last_modified": "最近更新",
}


def _client() -> HubApi:
    """复用一个 HubApi（内部持有连接池），首次调用时创建。"""
    global _api
    if _api is None:
        _api = HubApi()
    return _api


def _cached(key: tuple, producer):
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    value = producer()
    _cache[key] = (now, value)
    # 简单清理，避免缓存无限增长
    if len(_cache) > 400:
        for k in list(_cache)[:200]:
            _cache.pop(k, None)
    return value


def explain_error(exc: BaseException) -> str:
    """把底层异常翻译成用户能看懂的一句话。"""
    name = type(exc).__name__
    text = str(exc)
    low = text.lower()

    # 魔搭服务端自己的故障（数据库表缺失、unihub 401 等），跟本地无关
    if "unihub" in low or "获取模型目录树失败" in text or "e1002" in low:
        return "魔搭服务器当前故障（他们的后台数据库出错），不是你电脑或网络的问题，过一段时间再试就好"
    if "doesn't exist" in low and "table" in low:
        return "魔搭服务器当前故障（他们的后台数据库出错），过一段时间再试就好"

    mapping = (
        ("NotExistError", "找不到这个模型，请检查模型 ID 是否写对（形如 Qwen/Qwen2.5-7B-Instruct）"),
        ("AuthenticationError", "这个模型需要登录授权，暂时无法直接下载"),
        ("PermissionDeniedError", "没有权限下载这个模型（可能需要申请授权）"),
        ("InvalidParameter", "请求参数不对：" + text[:200]),
        ("FileSizeMismatchError", "服务器返回的文件大小异常，可稍后重试，或换一个版本"),
    )
    for key, message in mapping:
        if name == key:
            return message
    if "timed out" in low or "timeout" in low:
        return "连接超时，请检查网络后重试"
    if "connection" in low or "network" in low or "ssl" in low or "proxy" in low:
        return "网络连接失败，请检查网络或代理设置"
    if "500" in text or "servererror" in low:
        return "魔搭服务器返回错误（500），通常是他们那边的问题，过一会儿再试"
    return f"{name}: {text[:300]}"


def is_server_outage(exc: BaseException) -> bool:
    """判断是不是魔搭服务端整体故障：这种情况重试也没用，应该立刻停下并提示。"""
    text = str(exc)
    low = text.lower()
    return (
        "unihub" in low
        or "获取模型目录树失败" in text
        or "e1002" in low
        or ("doesn't exist" in low and "table" in low)
        or "e2020" in low
    )


# ---------------------------------------------------------------------------
# 搜索
# ---------------------------------------------------------------------------
def search_models(query: str, page: int = 1, page_size: int = 12, sort: str = "default") -> dict:
    query = (query or "").strip()
    sort = sort if sort in SORTS else "default"
    key = ("search", query, page, page_size, sort)

    def produce():
        with _lock:
            api = _client()
            result = api.list_repos(
                "model",
                search=query or None,
                page_number=page,
                page_size=page_size,
                sort=sort,
            )
            raw_items = list(result.items)
            items = []
            for raw in raw_items:
                data = raw.to_dict() if hasattr(raw, "to_dict") else dict(raw)
                repo_id = data.get("id") or data.get("repo_id") or ""
                if not repo_id:
                    continue
                items.append(
                    {
                        "repo_id": repo_id,
                        "display_name": data.get("display_name") or repo_id,
                        "name": repo_id.split("/")[-1],
                        "downloads": data.get("downloads") or 0,
                        "likes": data.get("likes") or 0,
                        "tasks": data.get("tasks") or [],
                        "license": data.get("license") or "",
                        "description": (data.get("description") or "").strip(),
                        "file_size": data.get("file_size") or 0,
                        "last_modified": data.get("last_modified"),
                    }
                )
            return {
                "items": items,
                "page": page,
                "page_size": page_size,
                "has_more": len(items) == page_size,
            }

    try:
        return _cached(key, produce)
    except Exception as exc:  # noqa: BLE001 - 交给界面显示
        return {"items": [], "page": page, "page_size": page_size, "has_more": False, "error": explain_error(exc)}


# ---------------------------------------------------------------------------
# 详情 / 文件列表 / 版本
# ---------------------------------------------------------------------------
def model_detail(repo_id: str) -> dict:
    repo_id = normalize_repo_id(repo_id)
    if not repo_id:
        return {"error": "请输入模型 ID，形如 Qwen/Qwen2.5-7B-Instruct"}

    def produce():
        with _lock:
            data = _client().get_model(repo_id)
        return {
            "repo_id": repo_id,
            "display_name": data.get("display_name") or repo_id,
            "description": (data.get("Description") or "").strip(),
            "downloads": data.get("Downloads") or 0,
            "likes": data.get("Likes") or 0,
            "license": data.get("License") or "",
            "tags": data.get("Tags") or [],
            "tasks": data.get("tasks") or [],
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
        with _lock:
            infos = _client().list_repo_files(repo_id, "model", revision=revision, recursive=True)
        files = []
        total = 0
        for info in infos:
            path = getattr(info, "path", "")
            if not path or path.endswith("/"):
                continue
            size = int(getattr(info, "size", 0) or 0)
            if path.startswith(".git/"):
                continue
            files.append({"path": path, "size": size})
            total += size
        files.sort(key=lambda f: f["path"])
        return {"repo_id": repo_id, "revision": revision, "files": files, "total_files": len(files), "total_size": total}

    try:
        return _cached(("files", repo_id, revision), produce)
    except Exception as exc:  # noqa: BLE001
        return {"error": explain_error(exc), "files": [], "total_files": 0, "total_size": 0}


def list_revisions(repo_id: str) -> dict:
    repo_id = normalize_repo_id(repo_id)

    def produce():
        with _lock:
            rows = _client().list_repo_revisions(repo_id, "model")
        out = []
        for row in rows or []:
            rev = row.get("Revision") or row.get("revision")
            if rev:
                out.append(rev)
        return {"revisions": out or ["master"]}

    try:
        return _cached(("revisions", repo_id), produce)
    except Exception as exc:  # noqa: BLE001
        return {"revisions": ["master"], "error": explain_error(exc)}


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def normalize_repo_id(text: str) -> str:
    """接受模型 ID、模型页面链接，统一转成 owner/name。"""
    text = (text or "").strip().strip('"').strip("'")
    if not text:
        return ""
    lowered = text.lower()
    for marker in ("/models/", "/model/"):
        if marker in lowered:
            text = text[lowered.index(marker) + len(marker):]
            break
    text = text.split("?")[0].split("#")[0]
    text = text.replace("\\", "/").strip("/")
    parts = [p for p in text.split("/") if p]
    if len(parts) >= 2 and "modelscope.cn" in lowered:
        # 链接里常带 revision 之类的多余层级，只取前两段
        return f"{parts[0]}/{parts[1]}"
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return ""


def looks_like_repo_id(text: str) -> bool:
    text = (text or "").strip()
    if "modelscope.cn" in text.lower():
        return True
    return "/" in text and len(text.split("/")) == 2 and " " not in text
