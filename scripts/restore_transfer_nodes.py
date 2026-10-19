"""从迁移前的 .db.bak 恢复 666 拓扑里的「升降移载机 / 转向移载机」节点。

migrate_drop_legacy_types.py 精简类型时把 lift_transfer（升降移载机）和 turn_transfer
（转向移载机）当作遗留设备一并删掉了，后来用户要求把这两个类型恢复回来。类型/工具/
渲染已在代码里恢复，本脚本负责把 666 拓扑里原本的这 33 个设备节点（9 升降 + 24 转向）
从迁移前的备份 .db.bak 重新插回当前库。

说明：
- 源数据取自 agv_platform.db.bak（保留着迁移前的节点），因此本脚本不做 .db.bak 覆盖，
  而是把「当前库」先备份成 agv_platform.db.pre_restore.bak。
- 原标签在历史上从 xlsx 导入时已是乱码，这里按 id 后缀重建为正确的「升降移载机-NN /
  转向移载机-NN」。
- 幂等：先删掉目标拓扑里同 id 的这两个类型节点再重建，可重复运行。

用法（仓库根目录）：
  ./web/.venv/Scripts/python.exe web/scripts/restore_transfer_nodes.py
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # WMS/
DB = ROOT / "web" / "backend" / "app_data" / "agv_platform.db"
BAK = DB.with_suffix(".db.bak")  # 迁移前备份，含被删节点
PRE_RESTORE_BAK = DB.with_name("agv_platform.db.pre_restore.bak")

TOPOLOGY_ID = "topology-6ceb4920"
TRANSFER_TYPES = {"lift_transfer", "turn_transfer"}
LABELS = {"lift_transfer": "升降移载机", "turn_transfer": "转向移载机"}


def load_payload(conn: sqlite3.Connection, tid: str) -> dict | None:
    row = conn.execute("SELECT payload FROM topologies WHERE id=?", (tid,)).fetchone()
    return json.loads(row[0]) if row else None


def main() -> None:
    if not DB.exists():
        raise SystemExit(f"找不到数据库 {DB}")
    if not BAK.exists():
        raise SystemExit(f"找不到迁移前备份 {BAK}（恢复源）")

    src = sqlite3.connect(BAK)
    src_payload = load_payload(src, TOPOLOGY_ID)
    src.close()
    if src_payload is None:
        raise SystemExit(f"备份里找不到拓扑 {TOPOLOGY_ID}")

    transfer_nodes = [n for n in src_payload.get("nodes", []) if n.get("type") in TRANSFER_TYPES]
    if not transfer_nodes:
        raise SystemExit("备份里没有 lift_transfer / turn_transfer 节点，无需恢复。")

    # 重建正确标签
    restored = []
    for n in transfer_nodes:
        suffix = n["id"].split("-")[-1]
        restored.append({**n, "label": f"{LABELS[n['type']]}-{suffix}"})

    # 备份当前库（不覆盖 .db.bak，它仍是恢复源）
    shutil.copy2(DB, PRE_RESTORE_BAK)

    conn = sqlite3.connect(DB)
    payload = load_payload(conn, TOPOLOGY_ID)
    if payload is None:
        conn.close()
        raise SystemExit(f"当前库找不到拓扑 {TOPOLOGY_ID}")

    before = len(payload["nodes"])
    ids = {n["id"] for n in restored}
    payload["nodes"] = [n for n in payload["nodes"] if not (n.get("id") in ids and n.get("type") in TRANSFER_TYPES)]
    removed = before - len(payload["nodes"])
    payload["nodes"].extend(restored)

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE topologies SET payload=?, updated_at=? WHERE id=?",
        (json.dumps(payload, ensure_ascii=False), now, TOPOLOGY_ID),
    )
    conn.commit()
    conn.close()

    lift = sum(1 for n in restored if n["type"] == "lift_transfer")
    turn = sum(1 for n in restored if n["type"] == "turn_transfer")
    print("=== 恢复完成 ===")
    print(f"拓扑: {TOPOLOGY_ID}")
    print(f"升降移载机 lift_transfer: {lift}")
    print(f"转向移载机 turn_transfer: {turn}")
    print(f"清除同 id 旧节点: {removed}")
    print(f"当前库备份: {PRE_RESTORE_BAK.name}")


if __name__ == "__main__":
    main()
