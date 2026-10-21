r"""把本地数据库里的拓扑导出成 JSON 文件（import_topology.py 的逆操作）。

用法（在 web/ 目录下，用虚拟环境的 python）：
    ..\.venv\Scripts\python.exe scripts\export_topology.py --list          # 列出库里的拓扑
    ..\.venv\Scripts\python.exe scripts\export_topology.py 666             # 按 name 或 id 导出
    ..\.venv\Scripts\python.exe scripts\export_topology.py topology-6ceb4920
    ..\.venv\Scripts\python.exe scripts\export_topology.py 666 -o D:\tmp\666.json
    ..\.venv\Scripts\python.exe scripts\export_topology.py --all           # 全部导出

导出后需要提交时：
    git add docs/topologies/<name>.json
    git commit -m "docs: 更新 <name> 拓扑"
    git push origin main

说明：直接读 SQLite，不需要启动后端。默认输出到 docs/topologies/<name>.json，
文件名里的非法字符会被替换成下划线。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parents[1]          # web/
DB = WEB_ROOT / "backend" / "app_data" / "agv_platform.db"
DEFAULT_OUT_DIR = WEB_ROOT / "docs" / "topologies"


def _sanitize(name: str) -> str:
    """把拓扑名转成安全的文件名。"""
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    return cleaned or "topology"


def _load_rows() -> list[sqlite3.Row]:
    if not DB.exists():
        raise SystemExit(f"找不到数据库 {DB}\n（先在 web/backend 下启动一次后端以生成 app_data/agv_platform.db）")
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT id, name, payload, updated_at FROM topologies ORDER BY updated_at DESC"
        ).fetchall()
    finally:
        conn.close()


def _match(rows: list[sqlite3.Row], query: str) -> list[sqlite3.Row]:
    """先按 id 精确匹配，再按 name 做不区分大小写的部分匹配。"""
    q = query.strip()
    exact = [r for r in rows if r["id"] == q]
    if exact:
        return exact
    return [r for r in rows if q.lower() in r["name"].lower()]


def _write_json(row: sqlite3.Row, out_path: Path) -> int:
    payload = json.loads(row["payload"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return out_path.stat().st_size


def _print_list(rows: list[sqlite3.Row]) -> None:
    print(f"数据库: {DB}")
    print(f"{'id':<22} {'name':<28} {'updated_at':<28} payload_len")
    print("-" * 96)
    for r in rows:
        print(f"{r['id']:<22} {r['name']:<28} {r['updated_at']:<28} {len(r['payload'])}")
    print(f"\n共 {len(rows)} 个拓扑。导出单个：python scripts/export_topology.py <name|id>")


def main() -> int:
    parser = argparse.ArgumentParser(description="导出数据库里的拓扑为 JSON 文件")
    parser.add_argument("query", nargs="?", help="拓扑 id 或 name（name 支持部分匹配）")
    parser.add_argument("-o", "--output", help="输出文件路径（默认 docs/topologies/<name>.json）")
    parser.add_argument("--list", action="store_true", help="列出数据库里所有拓扑")
    parser.add_argument("--all", action="store_true", help="导出全部拓扑到 docs/topologies/")
    args = parser.parse_args()

    rows = _load_rows()

    # 没给任何参数时等同于 --list，方便当查询用
    if args.list or (not args.query and not args.all):
        _print_list(rows)
        return 0

    if args.all:
        for r in rows:
            out = DEFAULT_OUT_DIR / f"{_sanitize(r['name'])}.json"
            size = _write_json(r, out)
            print(f"导出 {r['name']} -> {out}（{size} 字节）")
        return 0

    hits = _match(rows, args.query)
    if not hits:
        print(f"没找到匹配 {args.query!r} 的拓扑，用 --list 查看全部。")
        return 1
    if len(hits) > 1:
        print(f"{args.query!r} 匹配到 {len(hits)} 个拓扑，请用 id 精确指定：")
        for r in hits:
            print(f"  {r['id']}  {r['name']}")
        return 1

    row = hits[0]
    out = Path(args.output) if args.output else DEFAULT_OUT_DIR / f"{_sanitize(row['name'])}.json"
    size = _write_json(row, out)
    print(f"导出成功: id={row['id']} name={row['name']}")
    print(f"  -> {out}（{size} 字节，库内更新时间 {row['updated_at']}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
