"""给「Demo 6.6 Style Walk Map」的 6 个 CTU 区域补建 storage 库位节点。

该 demo 拓扑只有 6 个 CTU 区域（zone-ctu … zone-ctu-6），没有任何 storage 节点，
导致 build_inventory 铺不进货、3D 编辑器里 CTU 库是空的。此脚本按「一格一库位」
在每个 CTU 区域的每个格子补一个 storage 节点（capacity=17 层），随后 goods 自动铺满。

用法（仓库根目录）：
  ./web/.venv/Scripts/python.exe web/scripts/seed_demo_ctu_storage.py

幂等：先清掉该拓扑已有的 storage 节点再重建，改前自动备份 .db.bak。
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # WMS/
DB = ROOT / "web" / "backend" / "app_data" / "agv_platform.db"
TOPOLOGY_ID = "topology-demo-01"
GROUND_FLOOR = "1F"
CAPACITY = 17  # 与 CTU 货架层数（CTU_RACK_LAYERS）一致


def main() -> None:
    if not DB.exists():
        raise SystemExit(f"找不到数据库 {DB}")

    backup = DB.with_suffix(".db.bak")
    shutil.copy2(DB, backup)

    conn = sqlite3.connect(DB)
    row = conn.execute("SELECT payload FROM topologies WHERE id=?", (TOPOLOGY_ID,)).fetchone()
    if row is None:
        conn.close()
        raise SystemExit(f"找不到拓扑 {TOPOLOGY_ID}")
    payload = json.loads(row[0])

    before = len(payload["nodes"])
    payload["nodes"] = [n for n in payload["nodes"] if n.get("type") != "storage"]
    removed = before - len(payload["nodes"])

    zones = sorted(
        [z for z in payload["zones"] if z.get("type") == "ctu"],
        key=lambda z: (z["y"], z["x"]),
    )

    used_ids = {n["id"] for n in payload["nodes"]}
    storage_n = 1
    assigned = 0
    for z in zones:
        for r in range(z["y"], z["y"] + z["height"]):
            for c in range(z["x"], z["x"] + z["width"]):
                while f"storage-{storage_n:02d}" in used_ids:
                    storage_n += 1
                nid = f"storage-{storage_n:02d}"
                used_ids.add(nid)
                storage_n += 1
                payload["nodes"].append(
                    {
                        "id": nid,
                        "type": "storage",
                        "floor": GROUND_FLOOR,
                        "row": r,
                        "col": c,
                        "zoneId": z["id"],
                        "capacity": CAPACITY,
                        "label": f"CTU-{z['label']}-{r}-{c}",
                    }
                )
                assigned += 1

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE topologies SET payload=?, updated_at=? WHERE id=?",
        (json.dumps(payload, ensure_ascii=False), now, TOPOLOGY_ID),
    )
    conn.commit()
    conn.close()

    per_zone: dict[str, int] = {}
    for n in payload["nodes"]:
        if n.get("type") == "storage":
            per_zone[n["zoneId"]] = per_zone.get(n["zoneId"], 0) + 1

    print("=== 完成 ===")
    print(f"CTU 区域数: {len(zones)}")
    print(f"移除旧 storage 节点: {removed}")
    print(f"新建 storage 节点: {assigned}")
    print(f"分区节点数: {per_zone}")
    print(f"备份: {backup.name}")


if __name__ == "__main__":
    main()
