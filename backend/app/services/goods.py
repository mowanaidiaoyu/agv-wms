from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from .pathfinding import cell_id, cell_position, node_position

# 固定库存 seed：3D 编辑器预览的「货」与仿真里 CTU 取到的「货」保持一致，
# 与场景 seed（每次运行不同）解耦。
INVENTORY_SEED = 20261016
# 镜像 simulation.CTU_RACK_LAYERS，避免 goods -> simulation 的循环导入。
CTU_LAYERS = 17

# 商品目录静态 JSON（由 scripts/extract_sku_catalog.py 从真实 SKU xlsx 抽取）。
BACKEND_DIR = Path(__file__).resolve().parents[2]  # web/backend
CATALOG_PATH = BACKEND_DIR.parent / "data" / "sku_catalog.json"  # web/data

# 订单行 / 库存槽共用的 SKU 字段（透传进仿真任务，前端据此展示商品属性）。
SKU_FIELDS = (
    "skuId",
    "skuName",
    "largeCategory",
    "subclass",
    "spec",
    "grade",
    "material",
    "surfaceTreatment",
)


def load_catalog() -> dict[str, Any]:
    """读取商品目录，缺失或损坏时返回空目录（调用方自行回退）。"""
    try:
        data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("skus"), list):
            return {"version": data.get("version", 1), "skus": data["skus"]}
    except (OSError, ValueError):
        pass
    return {"version": 1, "skus": []}


def ctu_zone_ids(topology: dict[str, Any]) -> set[str]:
    """收集拓扑里所有 `type == "ctu"` 的 zone id。

    真实 666 拓扑的 CTU 库被拆成多个 1 宽列区：`zone-ctu`、`zone-ctu-2` …
    `zone-ctu-147`。只用精确 id `zone-ctu` 只能拿到其中一列（24 个库位），
    必须按 zone 类型把全部列区都纳入，才能覆盖完整库位数据。
    """
    ids = {zone["id"] for zone in topology.get("zones", []) if zone.get("type") == "ctu"}
    if ids:
        return ids
    # 没有 zone 类型信息的老拓扑回退到约定 id。
    return {"zone-ctu"}


def build_inventory(
    topology: dict[str, Any],
    catalog: dict[str, Any] | None = None,
    seed: int = INVENTORY_SEED,
) -> list[dict[str, Any]]:
    """把商品目录确定性地分配到当前拓扑的 CTU 库位上。

    每个 CTU storage/buffer 节点 × 1..17 层 = 一个库位槽；空则回退到全部
    storage/buffer 节点（与 simulation._seed_tasks 的 ctu_sources 回退一致）。
    """
    if catalog is None:
        catalog = load_catalog()
    skus = catalog.get("skus", [])
    if not skus:
        return []

    nodes = topology.get("nodes", [])
    ctu_zones = ctu_zone_ids(topology)
    ctu_sources = [node for node in nodes if node["type"] == "storage" and node.get("zoneId") in ctu_zones]
    if not ctu_sources:
        ctu_sources = [node for node in nodes if node["type"] == "storage"]
    if not ctu_sources:
        return []

    slots: list[tuple[dict[str, Any], int]] = [(node, layer) for node in ctu_sources for layer in range(1, CTU_LAYERS + 1)]
    rng = random.Random(seed)
    rng.shuffle(slots)

    # 一次性建立 位置 -> cellId 映射，避免每个槽都重扫 nodes/cells（666 拓扑有 42432 个槽）。
    pos_to_cell = {cell_position(cell): cell["id"] for cell in topology.get("cells", [])}

    def cell_id_for(node: dict[str, Any]) -> str:
        return pos_to_cell.get(node_position(node), cell_id(node["row"], node["col"], node.get("floor")))

    inventory: list[dict[str, Any]] = []
    for index, (node, layer) in enumerate(slots):
        sku = skus[index % len(skus)]
        inventory.append(
            {
                "nodeId": node["id"],
                "cellId": cell_id_for(node),
                "layer": layer,
                "skuId": sku.get("skuId"),
                "skuName": sku.get("name"),
                "largeCategory": sku.get("largeCategory"),
                "subclass": sku.get("subclass"),
                "spec": sku.get("spec"),
                "grade": sku.get("grade"),
                "material": sku.get("material"),
                "surfaceTreatment": sku.get("surfaceTreatment"),
                "qty": rng.randint(1, 5),
            }
        )
    return inventory


def sample_order_lines(inventory: list[dict[str, Any]], count: int, rng: random.Random) -> list[dict[str, Any]]:
    """从库存确定性地抽 `count` 个订单行（每行一箱，qty=1），返回独立副本。"""
    if not inventory:
        return []
    picks = rng.sample(inventory, min(count, len(inventory)))
    lines: list[dict[str, Any]] = []
    for slot in picks:
        line = {"nodeId": slot["nodeId"], "layer": slot["layer"]}
        for field in SKU_FIELDS:
            if slot.get(field) not in (None, ""):
                line[field] = slot[field]
        lines.append(line)
    return lines
