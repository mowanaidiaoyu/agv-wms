"""把 tb_basic_storage_bin 里的 CTU(HAI) 库位分配到拓扑「666」的 CTU 区域，生成 storage 节点。

用法（在仓库根目录）：
  ./web/.venv/Scripts/python.exe web/scripts/import_storage_bins.py

行为：
- 读取 data/tb_basic_storage_bin（库位数据）.xlsx，筛出 HAI-巷道-列-层@1 的 CTU 库位。
- 按 (巷道, 列) 归并成「库位列」，每列 17 层 → 折叠为 1 个 storage 节点（capacity=17）。
- 库位列按 (巷道, 列) 排序；CTU 区域按 (y, x) 排序；顺序就近配对，一格一节点。
- 节点 label 用列编码 HAI-巷道-列（如 HAI-001-001），id 沿用 storage-NN。
- 写回 app_data/agv_platform.db 的 topology-6ceb4920（改前自动备份 .db.bak）。

可重复运行：会先清掉该拓扑里已有的 storage 节点再重建，保证幂等。
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # WMS/
XLSX = ROOT / "data" / "tb_basic_storage_bin（库位数据）.xlsx"
DB = ROOT / "web" / "backend" / "app_data" / "agv_platform.db"
TOPOLOGY_ID = "topology-6ceb4920"
GROUND_FLOOR = "1F"

HAI_RE = re.compile(r"HAI-(\d+)-(\d+)-(\d+)@1")


def load_hai_columns() -> "OrderedDict[tuple[int, int], int]":
    """返回 (巷道, 列) -> 层数 的有序字典（按文件顺序，调用处再排序）。"""
    import openpyxl

    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    ws = wb["tb_basic_storage_bin"]
    cols: "OrderedDict[tuple[int, int], int]" = OrderedDict()
    for row in ws.iter_rows(min_row=2, values_only=True):
        bid = row[0]
        if not bid:
            continue
        m = HAI_RE.match(str(bid))
        if m:
            lane, col, layer = int(m.group(1)), int(m.group(2)), int(m.group(3))
            cols[(lane, col)] = cols.get((lane, col), 0) + 1
    wb.close()
    return cols


def main() -> None:
    columns = load_hai_columns()
    ordered_columns = sorted(columns.items())  # [( (巷道,列), 层数 ), ...]

    if not Path(DB).exists():
        raise SystemExit(f"找不到数据库 {DB}")

    backup = DB.with_suffix(".db.bak")
    shutil.copy2(DB, backup)

    conn = sqlite3.connect(DB)
    row = conn.execute("SELECT payload FROM topologies WHERE id=?", (TOPOLOGY_ID,)).fetchone()
    if row is None:
        conn.close()
        raise SystemExit(f"找不到拓扑 {TOPOLOGY_ID}")
    payload = json.loads(row[0])

    # 清掉已有 storage 节点（幂等），保留其它节点
    before_storage = len(payload["nodes"])
    payload["nodes"] = [n for n in payload["nodes"] if n.get("type") != "storage"]
    removed = before_storage - len(payload["nodes"])

    zones = sorted(
        [z for z in payload["zones"] if z.get("type") == "ctu"],
        key=lambda z: (z["y"], z["x"]),
    )

    # 已占格（现有节点），避免在同一 (row,col) 叠放
    occupied = {(n["row"], n["col"]) for n in payload["nodes"] if n.get("floor", GROUND_FLOOR) == GROUND_FLOOR}

    # 展开区域 -> 有序格子列表（row-major）
    cells: list[tuple[dict, int, int]] = []
    for z in zones:
        for r in range(z["y"], z["y"] + z["height"]):
            for c in range(z["x"], z["x"] + z["width"]):
                cells.append((z, r, c))

    used_ids = {n["id"] for n in payload["nodes"]}
    storage_n = 1
    assigned = 0
    skipped_occupied = 0

    col_iter = iter(ordered_columns)
    for z, r, c in cells:
        if (r, c) in occupied:
            skipped_occupied += 1
            continue
        try:
            (lane, col), layers = next(col_iter)
        except StopIteration:
            break  # 列已分完，剩余格子留空
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
                "capacity": layers,
                "label": f"HAI-{lane:03d}-{col:03d}",
            }
        )
        assigned += 1

    payload_str = json.dumps(payload, ensure_ascii=False)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE topologies SET payload=?, updated_at=? WHERE id=?",
        (payload_str, now, TOPOLOGY_ID),
    )
    conn.commit()
    conn.close()

    # 汇总
    per_zone: dict[str, int] = {}
    for n in payload["nodes"]:
        if n.get("type") == "storage":
            per_zone[n["zoneId"]] = per_zone.get(n["zoneId"], 0) + 1

    print("=== 导入完成 ===")
    print(f"库位列总数: {len(ordered_columns)}")
    print(f"CTU 区域数: {len(zones)} | 可用格数: {len(cells)}")
    print(f"移除旧 storage 节点: {removed}")
    print(f"新建 storage 节点: {assigned}")
    print(f"跳过(已占格): {skipped_occupied} | 留空格: {len(cells) - assigned - skipped_occupied}")
    print(f"备份: {backup.name}")
    print("样例节点:", [n["label"] for n in payload["nodes"] if n.get("type") == "storage"][:6])
    print("分区节点数(前 10):", list(per_zone.items())[:10])


if __name__ == "__main__":
    main()
