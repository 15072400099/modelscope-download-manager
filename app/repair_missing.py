"""补齐缺失的模型分片。

为什么需要它：ModelScope 官方下载库在“大文件 + 磁盘上已有半截文件”的续传场景
下会卡住（任务一直显示下载中，但一个字节都不写）。本脚本绕开它，用最原始的
HTTP Range 续传，只补缺的差额，已下好的文件不会重下。

用法：先跑 app/verify_models.py 生成报告，再跑 python app/repair_missing.py
"""

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
REPORT = DATA / "verify-report.json"
CHUNK = 4 * 1024 * 1024
WORKERS = 3
MAX_ATTEMPTS = 6
ACTIVE_WINDOW = 60  # 60 秒内还在变大的半截文件说明有任务在下载，跳过

_lock = threading.Lock()
_print_lock = threading.Lock()


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def fetch_one(repo: str, revision: str, rel: str, target: Path, expect: int):
    part = Path(str(target) + ".incomplete")
    if target.exists() and target.stat().st_size == expect:
        return rel, True, "已存在，跳过"
    part.parent.mkdir(parents=True, exist_ok=True)

    url = f"https://www.modelscope.cn/api/v1/models/{repo}/repo"
    params = {"Revision": revision, "FilePath": rel}

    for attempt in range(1, MAX_ATTEMPTS + 1):
        have = part.stat().st_size if part.exists() else 0
        if have >= expect:
            break
        headers = {"User-Agent": "modelscope-download-manager/1.0",
                   "Range": f"bytes={have}-{expect - 1}"}
        window_start, window_bytes = time.time(), 0
        try:
            with requests.get(url, params=params, headers=headers, stream=True, timeout=(15, 60)) as resp:
                if resp.status_code not in (200, 206):
                    raise RuntimeError(f"HTTP {resp.status_code}")
                if resp.status_code == 200 and have > 0:
                    have = 0  # 服务端不支持断点，只能整份重来
                    part.unlink(missing_ok=True)
                with open(part, "ab") as fh:
                    for chunk in resp.iter_content(CHUNK):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        window_bytes += len(chunk)
                        if time.time() - window_start > 8:
                            speed = window_bytes / (time.time() - window_start)
                            with _print_lock:
                                done = have + window_bytes
                                print(f"    {rel[-40:]:40} {done}/{expect} "
                                      f"({done / expect * 100:5.1f}%) {human(speed)}/s", flush=True)
                            window_start, window_bytes = time.time(), 0
        except BaseException as exc:  # noqa: BLE001
            size_now = part.stat().st_size if part.exists() else 0
            with _print_lock:
                print(f"    [!] {rel} 第 {attempt} 次中断（{type(exc).__name__}: {str(exc)[:60]}），"
                      f"已有 {human(size_now)}，稍后续传", flush=True)
            time.sleep(2 * attempt)
            continue

    size = part.stat().st_size if part.exists() else 0
    if size == expect:
        part.replace(target)
        return rel, True, "补齐"
    if size > expect:
        return rel, False, f"半截文件比应有大小还大（{size} > {expect}），需人工处理"
    return rel, False, f"仍然缺 {human(expect - size)}"


def main() -> int:
    if not REPORT.exists():
        print("找不到校验报告，请先运行：python app/verify_models.py")
        return 1
    report = json.loads(REPORT.read_text(encoding="utf-8"))

    jobs = []
    for entry in report:
        if entry.get("error") or not entry.get("missing"):
            continue
        for item in entry["missing"]:
            target = Path(entry["dir"]) / item["path"]
            expect = int(item["size"])
            if target.exists() and target.stat().st_size == expect:
                continue
            part = Path(str(target) + ".incomplete")
            if part.exists() and (time.time() - part.stat().st_mtime) < ACTIVE_WINDOW:
                print(f"    （跳过，正在下载中）{item['path']}", flush=True)
                continue
            jobs.append((entry["repo"], entry.get("revision") or "master",
                         item["path"], target, expect))

    if not jobs:
        print("没有需要补的文件。")
        return 0

    print(f"待补齐 {len(jobs)} 个文件（只补差额）\n", flush=True)
    ok_count = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(fetch_one, *job) for job in jobs]
        for fut in futures:
            rel, ok, msg = fut.result()
            ok_count += 1 if ok else 0
            print(f"  [{'OK' if ok else 'FAIL'}] {rel}: {msg}", flush=True)

    print(f"\n完成：{ok_count}/{len(jobs)} 个文件补齐", flush=True)
    return 0 if ok_count == len(jobs) else 2


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    raise SystemExit(main())
