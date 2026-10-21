r"""清理已删除货架后残留的 storage 库位节点。

背景：画 CTU 区域时会自动生成「一格一库位」的 storage 节点。旧版本删除区域只删 zone、
把节点的 zoneId 置空，节点本身留在拓扑里；后端再按这些残留库位合成库存，于是出现
「货架已经删了，货物/库位还在」。前端与后端逻辑已修复，但数据库里历史保存的拓扑
仍需清理一次。

保留规则：
1. zoneId 指向现有任意区域的 storage 节点；
2. 没有 zoneId（或指向已删除区域），但位置落在现有 ctu / shuttle 区域矩形内的
   storage 节点（兼容旧数据库位）。
其余 storage 节点视为货架删除后的残留并移除。

用法（仓库根目录）：
  web/.venv/Scripts/python.exe web/scripts/cleanup_orphan_storage.py           # 预览
  web/.venv/Scripts/python.exe web/scripts/cleanup_orphan_storage.py --apply   # 写回（先备份 .db.bak）

幂等：可重复运行；没有残留时不写库。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parents[1]  # web/
DB = WEB_ROOT / "backend" / "app_data" / "agv_platform.db"
RACK_ZONE_TYPES = {"ctu", "shuttle"}


def _inside_zone(node: dict, zone: dict) -> bool:
    row = int(node.get("row", -1))
    col = int(node.get("col", -1))
    return (
        zone["y"] <= row < zone["y"] + zone["height"]
        and zone["x"] <= col < zone["x"] + zone["width"]
    )


def _is_orphan(node: dict, zone_ids: set[str], rack_zones: list[dict]) -> bool:
    zone_id = node.get("zoneId")
    if zone_id in zone_ids:
        return False
    return not any(_inside_zone(node, zone) for zone in rack_zones)


def main() -> None:
    parser = argparse.ArgumentParser(description="清理已删除货架后残留的 storage 库位节点")
    parser.add_argument("--apply", action="store_true", help="写回数据库（默认只预览）")
    args = parser.parse_args()

    if not DB.exists():
        raise SystemExit(f"找不到数据库 {DB}")

    conn = sqlite3.connect(DB)
    rows = conn.execute("SELECT id, name, payload FROM topologies ORDER BY updated_at DESC").fetchall()

    pending: list[tuple[str, str, dict]] = []
    total_removed = 0
    for topology_id, name, raw in rows:
        payload = json.loads(raw)
        zones = payload.get("zones", [])
        zone_ids = {zone["id"] for zone in zones}
        rack_zones = [zone for zone in zones if zone.get("type") in RACK_ZONE_TYPES]
        nodes = payload.get("nodes", [])
        kept = [
            node
            for node in nodes
            if node.get("type") != "storage" or not _is_orphan(node, zone_ids, rack_zones)
        ]
        removed = len(nodes) - len(kept)
        if removed:
            total_removed += removed
            pending.append((topology_id, name, {**payload, "nodes": kept}))
            print(f"[清理] {topology_id:20} {name[:20]:20} 移除残留库位 {removed} 个")
        else:
            print(f"[保留] {topology_id:20} {name[:20]:20} 无残留库位")

    if not total_removed:
        print("没有需要清理的残留库位。")
        return

    if not args.apply:
        print(f"预览模式：共将移除 {total_removed} 个残留库位；加 --apply 写回数据库。")
        return

    backup = DB.with_suffix(".db.bak")
    shutil.copy2(DB, backup)
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        for topology_id, _name, payload in pending:
            conn.execute(
                "UPDATE topologies SET payload = ?, updated_at = ? WHERE id = ?",
                (json.dumps(payload, ensure_ascii=False), now, topology_id),
            )
    print(f"已清理 {total_removed} 个残留库位，涉及 {len(pending)} 个拓扑；数据库备份：{backup}")


if __name__ == "__main__":
    main()
