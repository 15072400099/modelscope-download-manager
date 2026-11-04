"""下载任务管理：队列、并发、进度、速度、暂停/续传、本地模型库。

设计要点：
* 每个任务一个工作线程；任务内部用线程池并行下载多个文件。
* 暂停＝给当前任务发取消信号，模型库会保留 .incomplete 半截文件，下次继续从断点续传。
* 任务状态写入 data/tasks.json，程序重启后仍能看到历史任务（正在下载的会变成“已暂停”）。
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from modelscope.hub.api import HubApi
from modelscope_hub import ProgressCallback

import ms_client

INCOMPLETE_SUFFIX = ".incomplete"
META_NAME = ".download-meta.json"

STATUS_TEXT = {
    "queued": "排队中",
    "running": "下载中",
    "pausing": "正在暂停",
    "paused": "已暂停",
    "canceling": "正在取消",
    "canceled": "已取消",
    "done": "已完成",
    "error": "出错",
}


def _now() -> float:
    return time.time()


def _safe_dir_name(name: str) -> str:
    cleaned = "".join(ch for ch in name if ch not in '\\/:*?"<>|').strip()
    return cleaned or "model"


class DownloadManager:
    def __init__(self, data_dir: Path, settings: dict):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_path = self.data_dir / "tasks.json"
        self.settings = settings

        self._lock = threading.RLock()
        self._tasks: list[dict] = []
        self._events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._dirty = False
        self._library_cache: tuple[float, list] | None = None

        self._load()
        threading.Thread(target=self._tick_loop, name="scheduler", daemon=True).start()

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def _load(self) -> None:
        if not self.tasks_path.exists():
            return
        try:
            raw = json.loads(self.tasks_path.read_text(encoding="utf-8"))
        except Exception:
            return
        with self._lock:
            for item in raw if isinstance(raw, list) else []:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                if item.get("status") in ("running", "pausing", "canceling"):
                    item["status"] = "paused"
                item["speed"] = 0
                item.setdefault("files", [])
                item.setdefault("bytes_done", 0)
                item.setdefault("error", None)
                self._tasks.append(item)
        # 重启后按磁盘实际情况校正进度：进度条要显示真实已下多少，而不是“0%”吓人
        for item in self._tasks:
            if item.get("status") != "done":
                self._refresh_progress_from_disk(item)

    def _mark_dirty(self) -> None:
        self._dirty = True

    def _refresh_progress_from_disk(self, task: dict) -> None:
        """按磁盘上真实的文件大小重算进度。

        程序被强行关掉时，内存里的进度会落后于磁盘（比如已经下了 90MB，记录里只有
        17KB）。重启后如果直接展示旧数字，会让人误以为要从头再来。这里以磁盘为准。
        """
        files = task.get("files") or []
        if not files or not task.get("local_dir"):
            return
        out_dir = Path(task["local_dir"])
        done_bytes = 0
        done_files = 0
        try:
            for item in files:
                size = int(item.get("size") or 0)
                target = out_dir / item["path"]
                part = Path(str(target) + INCOMPLETE_SUFFIX)
                try:
                    if target.exists():
                        actual = target.stat().st_size
                        if size <= 0 or actual == size:
                            item["state"] = "done"
                            done_bytes += actual
                            done_files += 1
                            continue
                    if part.exists():
                        item["state"] = "partial"
                        done_bytes += part.stat().st_size
                        continue
                except OSError:
                    continue
                if item.get("state") == "done":
                    # 记录说下完了，但文件已经不在了（被手工删掉）
                    item["state"] = "pending"
        except OSError:
            return
        task["bytes_done"] = done_bytes
        task["files_done"] = done_files

    def _save(self, force: bool = False) -> None:
        with self._lock:
            if not (self._dirty or force):
                return
            self._dirty = False
            payload = []
            for task in self._tasks:
                item = {k: v for k, v in task.items() if not k.startswith("_") and k != "speed"}
                payload.append(item)
            text = json.dumps(payload, ensure_ascii=False, indent=1)
        tmp = self.tasks_path.with_suffix(".json.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.tasks_path)

    def save_now(self) -> None:
        self._save(force=True)

    # ------------------------------------------------------------------
    # 调度
    # ------------------------------------------------------------------
    def _tick_loop(self) -> None:
        last_save = time.monotonic()
        while True:
            time.sleep(1.0)
            try:
                self._sample_speeds()
                self._schedule()
                if time.monotonic() - last_save >= 3:
                    self._save()
                    last_save = time.monotonic()
            except Exception:  # noqa: BLE001 - 调度线程不能死
                pass

    def _sample_speeds(self) -> None:
        now = time.monotonic()
        with self._lock:
            for task in self._tasks:
                if task.get("status") == "running":
                    last_t = task.get("_lt") or now
                    last_b = task.get("_lb") or 0
                    dt = now - last_t
                    if dt >= 0.8:
                        delta = max(0, task.get("bytes_done", 0) - last_b)
                        task["speed"] = delta / dt if dt else 0
                        task["_lt"] = now
                        task["_lb"] = task.get("bytes_done", 0)
                else:
                    task["speed"] = 0
                    task["_lt"] = now
                    task["_lb"] = task.get("bytes_done", 0)

    def _schedule(self) -> None:
        limit = max(1, int(self.settings.get("max_parallel_models", 2)))
        with self._lock:
            running = sum(1 for t in self._tasks if t.get("status") in ("running", "pausing", "canceling"))
            if running >= limit:
                return
            for task in self._tasks:
                if running >= limit:
                    break
                if task.get("status") != "queued":
                    continue
                if task["id"] in self._threads and self._threads[task["id"]].is_alive():
                    continue
                self._start_task(task)
                running += 1

    def _start_task(self, task: dict) -> None:
        event = threading.Event()
        self._events[task["id"]] = event
        task["status"] = "running"
        task["error"] = None
        task["_lt"] = time.monotonic()
        task["_lb"] = task.get("bytes_done", 0)
        thread = threading.Thread(target=self._run_task, args=(task, event), name=f"task-{task['id'][:8]}", daemon=True)
        self._threads[task["id"]] = thread
        thread.start()
        self._mark_dirty()

    # ------------------------------------------------------------------
    # 真正的下载
    # ------------------------------------------------------------------
    def _run_task(self, task: dict, event: threading.Event) -> None:
        task_id = task["id"]
        try:
            api = HubApi()
            out_dir = Path(task["local_dir"])
            out_dir.mkdir(parents=True, exist_ok=True)

            files = task.get("files") or []
            if not files:
                listing = ms_client.list_files(task["repo_id"], task.get("revision") or "master")
                if listing.get("error"):
                    raise RuntimeError(listing["error"])
                files = [{"path": f["path"], "size": f["size"], "state": "pending"} for f in listing["files"]]
                task["files"] = files
                task["total_files"] = len(files)
                task["bytes_total"] = sum(int(f["size"]) for f in files)

            pending: list[dict] = []
            done_bytes = 0
            for item in files:
                if item.get("state") == "done":
                    done_bytes += int(item.get("size") or 0)
                    continue
                target = out_dir / item["path"]
                part = Path(str(target) + INCOMPLETE_SUFFIX)
                size = int(item.get("size") or 0)
                if target.exists():
                    try:
                        actual = target.stat().st_size
                    except OSError:
                        actual = -1
                    if size <= 0 or actual == size:
                        item["state"] = "done"
                        done_bytes += actual if actual > 0 else 0
                        continue
                if part.exists():
                    try:
                        done_bytes += part.stat().st_size
                        item["state"] = "partial"
                    except OSError:
                        pass
                pending.append(item)

            with self._lock:
                task["bytes_done"] = done_bytes
                task["_lb"] = done_bytes
                task["_lt"] = time.monotonic()
                task["files_done"] = sum(1 for f in files if f.get("state") == "done")
            self._mark_dirty()

            # 开跑前先探一下魔搭的下载通道：整站故障时立刻说人话，别让人对着 0% 干等
            if pending:
                problem = self._probe_download_channel(api, task, pending[0]["path"])
                if problem:
                    with self._lock:
                        task["status"] = "error"
                        task["error"] = problem
                        task["updated_at"] = _now()
                    self._mark_dirty()
                    return

            failures: list[tuple[str, str]] = []
            workers = max(1, int(self.settings.get("files_per_model", 3)))

            def worker(item: dict) -> None:
                self._download_one(api, task, event, out_dir, item)

            if pending:
                with ThreadPoolExecutor(max_workers=min(workers, len(pending))) as pool:
                    futures = {pool.submit(worker, item): item for item in pending}
                    for future in as_completed(futures):
                        item = futures[future]
                        try:
                            future.result()
                        except BaseException as exc:  # noqa: BLE001
                            if event.is_set():
                                continue
                            failures.append((item["path"], ms_client.explain_error(exc)))
                            with self._lock:
                                item["state"] = "error"
                        if event.is_set():
                            # 尽快停止排队中的任务
                            for f in futures:
                                f.cancel()

            with self._lock:
                status = task.get("status")
                if event.is_set():
                    # 只有用户明确“暂停/取消”时才改变状态；如果期间已经被“继续”，保持排队等下一轮调度
                    if status == "canceling":
                        task["status"] = "canceled"
                    elif status == "pausing":
                        task["status"] = "paused"
                elif failures:
                    task["status"] = "error"
                    first = failures[0]
                    task["error"] = f"{len(failures)} 个文件下载失败，例如 {first[0]}：{first[1]}"
                else:
                    task["status"] = "done"
                    task["bytes_done"] = task.get("bytes_total") or task.get("bytes_done", 0)
                    task["files_done"] = len(files)
                task["updated_at"] = _now()
                task.pop("_lt", None)
                task.pop("_lb", None)
            self._mark_dirty()

            if task["status"] == "done":
                self._write_meta(task, out_dir)
            elif task["status"] == "canceled":
                self._remove_partials(out_dir)
        except BaseException as exc:  # noqa: BLE001 - 任何异常都要落成可见状态
            with self._lock:
                if event.is_set():
                    if task.get("status") == "canceling":
                        task["status"] = "canceled"
                    elif task.get("status") == "pausing":
                        task["status"] = "paused"
                else:
                    task["status"] = "error"
                    task["error"] = ms_client.explain_error(exc)
                task["updated_at"] = _now()
            self._mark_dirty()
        finally:
            self._events.pop(task_id, None)

    def _download_one(self, api: HubApi, task: dict, event: threading.Event, out_dir: Path, item: dict) -> None:
        path = item["path"]
        size = int(item.get("size") or 0)
        if event.is_set():
            return

        with self._lock:
            item["state"] = "downloading"

        def make_callback():
            # 注意：内层类的 self 是回调实例，外层管理器要另取名字，否则会被覆盖
            manager = self

            class _CB(ProgressCallback):
                def __init__(self, filename, file_size):
                    super().__init__(filename, file_size)

                def update(self, n: int) -> None:
                    with manager._lock:
                        total = task.get("bytes_total") or 0
                        value = task.get("bytes_done", 0) + int(n)
                        task["bytes_done"] = min(value, total) if total else value

                def end(self) -> None:
                    pass

            return _CB

        kwargs = dict(
            repo_id=task["repo_id"],
            repo_type="model",
            file_path=path,
            revision=task.get("revision") or "master",
            local_dir=out_dir,
            progress_callbacks=[make_callback()],
            _cancel_event=event,
        )
        try:
            api.downloader.download_file(file_size=size or None, **kwargs)
        except Exception as exc:  # noqa: BLE001
            # 索引里的文件大小偶尔和服务端不一致，这时放宽大小校验重试一次
            if type(exc).__name__ == "FileSizeMismatchError":
                api.downloader.download_file(file_size=None, **kwargs)
            elif ms_client.is_server_outage(exc):
                # 魔搭服务端整体故障，重试多少次都没用：立刻停下并给出人话
                with self._lock:
                    task["status"] = "error"
                    task["error"] = ms_client.explain_error(exc)
                    task["updated_at"] = _now()
                event.set()  # 让同一任务里其他文件的下载一起收手
                raise
            else:
                raise

        with self._lock:
            item["state"] = "done"
            task["files_done"] = sum(1 for f in task.get("files", []) if f.get("state") == "done")

    # ------------------------------------------------------------------
    # 任务操作
    # ------------------------------------------------------------------
    def add_task(self, repo_id: str, revision: str = "master", files: list[dict] | None = None,
                 display_name: str = "") -> dict:
        repo_id = ms_client.normalize_repo_id(repo_id)
        if not repo_id:
            raise ValueError("模型 ID 不对，应该形如 Qwen/Qwen2.5-7B-Instruct")
        revision = (revision or "master").strip() or "master"

        owner, _, name = repo_id.partition("/")
        root = Path(self.settings["download_dir"])
        local_dir = root / _safe_dir_name(owner) / _safe_dir_name(name)

        with self._lock:
            for existing in self._tasks:
                if (existing["repo_id"] == repo_id and existing.get("revision") == revision
                        and existing.get("status") in ("queued", "running", "paused", "pausing")):
                    return existing

        task = {
            "id": uuid.uuid4().hex,
            "repo_id": repo_id,
            "revision": revision,
            "display_name": display_name or name,
            "local_dir": str(local_dir),
            "status": "queued",
            "error": None,
            "created_at": _now(),
            "updated_at": _now(),
            "total_files": len(files or []),
            "files_done": 0,
            "bytes_total": sum(int(f.get("size") or 0) for f in (files or [])),
            "bytes_done": 0,
            "speed": 0,
            "files": [{"path": f["path"], "size": int(f.get("size") or 0), "state": "pending"} for f in (files or [])],
        }
        with self._lock:
            self._tasks.insert(0, task)
        self._mark_dirty()
        return task

    def _find(self, task_id: str) -> dict:
        for task in self._tasks:
            if task["id"] == task_id:
                return task
        raise KeyError("任务不存在")

    def pause(self, task_id: str) -> dict:
        with self._lock:
            task = self._find(task_id)
            if task["status"] == "queued":
                task["status"] = "paused"
            elif task["status"] == "running":
                task["status"] = "pausing"
                event = self._events.get(task_id)
                if event:
                    event.set()
            task["updated_at"] = _now()
        self._mark_dirty()
        return task

    def resume(self, task_id: str) -> dict:
        with self._lock:
            task = self._find(task_id)
            if task["status"] in ("error", "canceled", "done"):
                task["error"] = None
            if task["status"] == "done":
                return task
            if task["status"] in ("queued", "running"):
                return task
            # 重新计算已完成进度，避免续传时数字错乱
            task["status"] = "queued"
            task["updated_at"] = _now()
        self._mark_dirty()
        return task

    def cancel(self, task_id: str) -> dict:
        with self._lock:
            task = self._find(task_id)
            if task["status"] in ("running", "pausing"):
                task["status"] = "canceling"
                event = self._events.get(task_id)
                if event:
                    event.set()
            else:
                task["status"] = "canceled"
                self._remove_partials(Path(task["local_dir"]))
            task["updated_at"] = _now()
        self._mark_dirty()
        return task

    def retry(self, task_id: str) -> dict:
        with self._lock:
            task = self._find(task_id)
            for item in task.get("files", []):
                if item.get("state") in ("error", "downloading"):
                    item["state"] = "pending"
            task["error"] = None
            task["status"] = "queued"
            task["updated_at"] = _now()
        self._mark_dirty()
        return task

    def remove(self, task_id: str, delete_files: bool = False) -> None:
        with self._lock:
            task = self._find(task_id)
            event = self._events.get(task_id)
            if event:
                event.set()
            if task["status"] in ("running", "pausing", "queued"):
                task["status"] = "canceling"
            self._tasks = [t for t in self._tasks if t["id"] != task_id]
        if task.get("local_dir") and delete_files:
            target = Path(task["local_dir"])
            root = Path(self.settings["download_dir"]).resolve()
            try:
                resolved = target.resolve()
                if str(resolved).lower().startswith(str(root).lower()) and resolved != root:
                    shutil.rmtree(resolved, ignore_errors=True)
            except OSError:
                pass
        self._mark_dirty()
        self._library_cache = None

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    def _probe_download_channel(self, api, task: dict, sample_path: str) -> str | None:
        """开跑前用一次轻量请求探一下魔搭下载通道。

        魔搭整站故障时（例如后台数据库报错），底层库会在内部反复重试，
        界面上会长时间停在 0%，看着像本地坏了。这里提前十几秒就能判断出来。
        """
        import requests

        endpoint = (getattr(api, "endpoint", None) or "https://www.modelscope.cn").rstrip("/")
        url = f"{endpoint}/api/v1/models/{task['repo_id']}/repo"
        params = {"Revision": task.get("revision") or "master", "FilePath": sample_path}
        last_reason = ""
        for attempt in (1, 2):
            try:
                resp = requests.get(
                    url, params=params, stream=True, timeout=(5, 12),
                    headers={"User-Agent": "modelscope-studio/1.0"},
                )
                code = resp.status_code
                resp.close()
                if code >= 500:
                    last_reason = f"返回 {code}"
                else:
                    return None  # 2xx/3xx/4xx 都说明通道是通的，交给正常流程
            except requests.Timeout:
                last_reason = "长时间无响应"
            except Exception:  # noqa: BLE001 - 连不上也可能是本地网络，不拦截
                return None
            if attempt == 1:
                time.sleep(2)
        return f"魔搭服务器当前故障（{last_reason}），不是你电脑或网络的问题，过一段时间再试"

    def _write_meta(self, task: dict, out_dir: Path) -> None:
        try:
            meta = {
                "repo_id": task["repo_id"],
                "revision": task.get("revision") or "master",
                "files": len(task.get("files") or []),
                "size": int(task.get("bytes_total") or 0),
                "finished_at": _now(),
            }
            (out_dir / META_NAME).write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def _remove_partials(self, out_dir: Path) -> None:
        try:
            if not out_dir.exists():
                return
            for path in out_dir.rglob(f"*{INCOMPLETE_SUFFIX}"):
                try:
                    path.unlink()
                except OSError:
                    pass
        except OSError:
            pass

    def state(self) -> dict:
        with self._lock:
            tasks = []
            for task in self._tasks:
                item = {k: v for k, v in task.items() if not k.startswith("_")}
                total = int(item.get("bytes_total") or 0)
                done = int(item.get("bytes_done") or 0)
                speed = float(item.get("speed") or 0)
                item["percent"] = round(done / total * 100, 1) if total else 0.0
                item["speed"] = round(speed, 1)
                item["eta"] = int((total - done) / speed) if speed > 0 and total > done else 0
                item["status_text"] = STATUS_TEXT.get(item.get("status", ""), item.get("status", ""))
                tasks.append(item)
            running = sum(1 for t in tasks if t["status"] in ("running", "pausing", "canceling"))
            queued = sum(1 for t in tasks if t["status"] == "queued")
            return {"tasks": tasks, "running": running, "queued": queued, "total": len(tasks)}

    # ------------------------------------------------------------------
    # 本地模型库
    # ------------------------------------------------------------------
    def library(self, force: bool = False) -> dict:
        now = time.monotonic()
        if self._library_cache and not force and now - self._library_cache[0] < 5:
            cached = self._library_cache[1]
            return {"items": cached, "total_size": sum(item["size"] for item in cached)}

        root = Path(self.settings["download_dir"])
        items = []
        if root.exists():
            try:
                owners = [p for p in root.iterdir() if p.is_dir()]
            except OSError:
                owners = []
            for owner_dir in owners:
                try:
                    models = [p for p in owner_dir.iterdir() if p.is_dir()]
                except OSError:
                    continue
                for model_dir in models:
                    entry = self._describe_dir(model_dir)
                    if entry:
                        entry["owner"] = owner_dir.name
                        if not entry.get("repo_id"):
                            entry["repo_id"] = f"{owner_dir.name}/{model_dir.name}"
                        items.append(entry)
            # 直接放在根目录下的模型（用户手工放的）也列出来
            for model_dir in owners:
                if (model_dir / "config.json").exists() or (model_dir / "model.safetensors").exists():
                    entry = self._describe_dir(model_dir)
                    if entry:
                        entry["owner"] = ""
                        if not entry.get("repo_id"):
                            entry["repo_id"] = model_dir.name
                        items.append(entry)

        items.sort(key=lambda x: x.get("finished_at") or x.get("mtime") or 0, reverse=True)
        self._library_cache = (now, items)
        total_size = sum(item["size"] for item in items)
        return {"items": items, "total_size": total_size}

    def _describe_dir(self, model_dir: Path) -> dict | None:
        """统计一个模型目录的真实占用。

        注意：正在下载的 `.incomplete` 半截文件也算进大小里——
        否则下到 700MB 时这里还显示 1.6MB，看起来就像“页面没更新”。
        """
        try:
            entries = list(os.scandir(model_dir))
        except OSError:
            return None
        size = 0
        count = 0
        partial = 0
        mtime = 0.0
        for entry in entries:
            try:
                if entry.is_file():
                    if entry.name == META_NAME:
                        continue
                    stat = entry.stat()
                    size += stat.st_size
                    if entry.name.endswith(INCOMPLETE_SUFFIX):
                        partial += stat.st_size
                    else:
                        count += 1
                    mtime = max(mtime, stat.st_mtime)
                elif entry.is_dir():
                    for sub in os.scandir(entry.path):
                        if not sub.is_file():
                            continue
                        stat = sub.stat()
                        size += stat.st_size
                        if sub.name.endswith(INCOMPLETE_SUFFIX):
                            partial += stat.st_size
                        else:
                            count += 1
                        mtime = max(mtime, stat.st_mtime)
            except OSError:
                continue

        meta = {}
        meta_path = model_dir / META_NAME
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                meta = {}
        if count == 0 and partial == 0:
            return None
        return {
            "path": str(model_dir),
            "name": model_dir.name,
            "repo_id": meta.get("repo_id") or "",
            "revision": meta.get("revision") or "master",
            "size": size,
            "files": count,
            "partial_size": partial,
            "finished_at": meta.get("finished_at"),
            "mtime": mtime,
            "complete": bool(meta),
        }
