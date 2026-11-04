"""补齐缺失的模型分片。

先跑 app/verify_models.py 生成报告，再跑 `python app/repair_missing.py`。
它复用主程序同一个下载实现（HTTP Range 续传），只补差额，已下好的不会重下。

这个脚本当初就是为了绕开官方库的续传卡死问题写的；现在它已经是主程序本体的下载路径。
"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
REPORT = DATA / "verify-report.json"

sys.path.insert(0, str(BASE / "app"))

import ms_client  # noqa: E402
from download_manager import download_file_to  # noqa: E402

WORKERS = 3


def load_settings() -> dict:
    path = DATA / "settings.json"
    settings = {"download_dir": str(BASE / "models"), "proxy": ""}
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            for key in settings:
                if key in stored:
                    settings[key] = stored[key]
        except Exception:
            pass
    return settings


def main() -> int:
    if not REPORT.exists():
        print("找不到校验报告，请先运行：python app/verify_models.py")
        return 1

    settings = load_settings()
    ms_client.configure(settings)
    proxy = ms_client.proxy()

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
            jobs.append((entry["repo"], entry.get("revision") or "master",
                         item["path"], target, expect))

    if not jobs:
        print("没有需要补的文件。")
        return 0

    print(f"待补齐 {len(jobs)} 个文件（只补差额）\n", flush=True)

    def run(job):
        repo, revision, path, target, expect = job
        url = ms_client.file_url(repo, revision, path)

        def on_progress(delta):
            if delta > 0:
                print(f"    {path[-50:]:50} +{delta / 1048576:.0f} MB", flush=True)

        try:
            download_file_to(url, target, expect, proxy=proxy, on_progress=on_progress)
            return path, True, "补齐"
        except BaseException as exc:  # noqa: BLE001
            return path, False, ms_client.explain_error(exc)

    ok_count = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for path, ok, msg in pool.map(run, jobs):
            ok_count += 1 if ok else 0
            print(f"  [{'OK' if ok else 'FAIL'}] {path}: {msg}", flush=True)

    print(f"\n完成：{ok_count}/{len(jobs)} 个文件补齐")
    return 0 if ok_count == len(jobs) else 2


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    raise SystemExit(main())
