"""核对本地模型目录和魔搭远端清单是否一致。

用法：python app/verify_models.py
结果同时写到 data/verify-report.json，供 repair_missing.py 使用。
"""

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
REPORT = DATA / "verify-report.json"

sys.path.insert(0, str(BASE / "app"))

import ms_client  # noqa: E402


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


def repo_info(model_dir: Path, root: Path):
    meta_path = model_dir / ".download-meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("repo_id"):
                return meta["repo_id"], meta.get("revision") or "master"
        except Exception:
            pass
    try:
        parts = model_dir.relative_to(root).parts
    except ValueError:
        return None, None
    if len(parts) == 2:
        return f"{parts[0]}/{parts[1]}", "master"
    return None, None


def main() -> int:
    settings = load_settings()
    ms_client.configure(settings)
    root = Path(settings["download_dir"])
    print(f"下载目录：{root}")
    print(f"服务地址：{ms_client.ENDPOINT}\n")
    if not root.exists():
        print("目录不存在，先下载一个模型吧。")
        return 1

    results = []
    for model_dir in sorted(d for d in root.glob("*/*") if d.is_dir()):
        repo, revision = repo_info(model_dir, root)
        if not repo:
            continue
        entry = {"repo": repo, "revision": revision, "dir": str(model_dir)}
        listing = ms_client.list_files(repo, revision)
        if listing.get("error"):
            entry["error"] = listing["error"]
            results.append(entry)
            print(f"[跳过] {repo}: {listing['error'][:60]}")
            continue

        missing, bad, ok, total = [], [], 0, 0
        for item in listing["files"]:
            path, size = item["path"], int(item["size"] or 0)
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

        entry.update({"remote_files": len(listing["files"]), "remote_total": total,
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
