"""核对本地模型目录与魔搭远端清单是否一致：找出缺失文件和大小不符的文件。

用法：python app/verify_models.py
结果同时写到 data/verify-report.json，供 repair_missing.py 使用。
"""

import json
import os
from pathlib import Path

os.environ.setdefault("TQDM_DISABLE", "1")

BASE = Path(__file__).resolve().parent.parent  # 程序根目录
DATA = BASE / "data"
REPORT = DATA / "verify-report.json"


def download_root() -> Path:
    settings = DATA / "settings.json"
    if settings.exists():
        try:
            configured = json.loads(settings.read_text(encoding="utf-8")).get("download_dir")
            if configured:
                return Path(configured)
        except Exception:
            pass
    return BASE / "models"


ROOT = download_root()


def repo_info(model_dir: Path):
    meta_path = model_dir / ".download-meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("repo_id"):
                return meta["repo_id"], meta.get("revision") or "master"
        except Exception:
            pass
    try:
        parts = model_dir.relative_to(ROOT).parts
    except ValueError:
        return None, None
    if len(parts) == 2:
        return f"{parts[0]}/{parts[1]}", "master"
    return None, None


def main():
    from modelscope.hub.api import HubApi

    api = HubApi()
    results = []
    print(f"下载目录：{ROOT}\n")
    if not ROOT.exists():
        print("目录不存在，先下载一个模型吧。")
        return 1

    model_dirs = sorted(d for d in ROOT.glob("*/*") if d.is_dir())
    for model_dir in model_dirs:
        repo, revision = repo_info(model_dir)
        if not repo:
            continue
        entry = {"repo": repo, "revision": revision, "dir": str(model_dir)}
        try:
            files = api.list_repo_files(repo, "model", revision=revision, recursive=True)
        except BaseException as exc:
            entry["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            results.append(entry)
            print(f"[{repo}] 读取远端清单失败：{entry['error'][:80]}")
            continue

        missing, bad, ok, total = [], [], 0, 0
        for info in files:
            path = getattr(info, "path", "")
            size = int(getattr(info, "size", 0) or 0)
            if not path or path.endswith("/"):
                continue
            total += size
            target = model_dir / path
            if not target.exists():
                missing.append({"path": path, "size": size})
                continue
            try:
                actual = target.stat().st_size
            except OSError:
                missing.append({"path": path, "size": size})
                continue
            if size and actual != size:
                bad.append({"path": path, "size": size, "actual": actual})
            else:
                ok += 1

        entry.update({"remote_files": len(files), "remote_total": total,
                      "ok": ok, "missing": missing, "bad": bad})
        results.append(entry)
        flag = "OK" if not missing and not bad else "缺失"
        print(f"[{flag}] {repo}: 正常 {ok}，缺失 {len(missing)}，大小不符 {len(bad)}")

    DATA.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    total_missing = sum(len(r.get("missing") or []) for r in results)
    total_bad = sum(len(r.get("bad") or []) for r in results)
    print(f"\n共 {len(results)} 个模型：缺失 {total_missing} 个文件，大小不符 {total_bad} 个")
    if total_missing or total_bad:
        print("运行 `python app/repair_missing.py` 可以自动补齐。")
    else:
        print("全部完整。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
