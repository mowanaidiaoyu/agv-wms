"""一次性数据迁移：剔除已删除的旧类型，并给 Demo 6.6 补一个「输送线出入口」port 区域。

模型精简后（CTU 库 → CTU → 输送线出入口；四向车库 → 四向车 → 输送线），后端 schema 的
ZoneType/NodeType 收窄为：

  ZoneType = ctu / shuttle / port
  NodeType = storage / ctu_robot / shuttle_robot

旧拓扑里还残留的节点类型（intersection/buffer/station/lift/entry/conveyor_gate/
lift_transfer/turn_transfer）和区域类型（conveyor/buffer/station/mixed）会导致 Pydantic
校验 422，必须剔除；同时把已删除的 `resources` 字段一并清掉。

用法（仓库根目录）：
  ./web/.venv/Scripts/python.exe web/scripts/migrate_drop_legacy_types.py

幂等：可重复运行；改前自动备份 .db.bak；port 区域仅在缺失时补建。
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # WMS/
DB = ROOT / "web" / "backend" / "app_data" / "agv_platform.db"

LEGACY_NODE_TYPES = {
    "intersection",
    "buffer",
    "station",
    "lift",
    "entry",
    "conveyor_gate",
    "lift_transfer",
    "turn_transfer",
}
LEGACY_ZONE_TYPES = {"conveyor", "buffer", "station", "mixed"}

# 给 Demo 6.6 补的输送线出入口（下方入、上方出）。落在 CTU 列区右侧的空地上（CTU 列区 x 最大到 11）。
DEMO_PORT_ZONE = {
    "id": "zone-port",
    "label": "输送线出入口",
    "type": "port",
    "x": 15,
    "y": 3,
    "width": 3,
    "height": 2,
}


def main() -> None:
    if not DB.exists():
        raise SystemExit(f"找不到数据库 {DB}")

    backup = DB.with_suffix(".db.bak")
    shutil.copy2(DB, backup)

    conn = sqlite3.connect(DB)
    rows = conn.execute("SELECT id, payload FROM topologies").fetchall()
    now = datetime.now(timezone.utc).isoformat()

    summary: list[tuple[str, str]] = []
    for tid, raw in rows:
        payload = json.loads(raw)

        n_nodes = len(payload.get("nodes", []))
        n_zones = len(payload.get("zones", []))
        payload["nodes"] = [n for n in payload.get("nodes", []) if n.get("type") not in LEGACY_NODE_TYPES]
        payload["zones"] = [z for z in payload.get("zones", []) if z.get("type") not in LEGACY_ZONE_TYPES]
        removed_nodes = n_nodes - len(payload["nodes"])
        removed_zones = n_zones - len(payload["zones"])

        # 删除已废弃的 resources 字段（字段已从 schema 移除，旧数据不再需要）
        had_resources = "resources" in payload
        payload.pop("resources", None)

        extra = ""
        if tid == "topology-demo-01":
            zone_ids = {z["id"] for z in payload["zones"]}
            if DEMO_PORT_ZONE["id"] not in zone_ids:
                payload["zones"].append(dict(DEMO_PORT_ZONE))
                extra = "补 port 区域 zone-port"

        conn.execute(
            "UPDATE topologies SET payload=?, updated_at=? WHERE id=?",
            (json.dumps(payload, ensure_ascii=False), now, tid),
        )
        summary.append(
            f"{tid}: 节点 -{removed_nodes} / 区域 -{removed_zones} / resources {'移除' if had_resources else '无'} {extra}"
        )

    conn.commit()
    conn.close()

    print("=== 迁移完成 ===")
    for line in summary:
        print(line)
    print(f"备份: {backup.name}")


if __name__ == "__main__":
    main()
