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
# 四向车库货道深度：每条货道最多 3 箱（depth=1 外层靠巷道面，深处箱被外层箱挡住）。
SHUTTLE_LANE_DEPTH = 3

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


def ctu_storage_nodes(topology: dict[str, Any]) -> list[dict[str, Any]]:
    """CTU 货架里的库位节点。

    优先按 `zoneId` 归属 CTU 区域；旧数据里库位没有（或指向已删除的）zoneId 时，
    按位置是否落在 CTU 区域矩形内识别。拓扑里没有 CTU 区域时返回空——
    货架已删除，残留的 storage 节点不应再产出货物。
    """
    zones = topology.get("zones", [])
    ctu_zones = [zone for zone in zones if zone.get("type") == "ctu"]
    if not ctu_zones:
        return []
    ctu_ids = {zone["id"] for zone in ctu_zones}
    all_zone_ids = {zone["id"] for zone in zones}
    nodes = [node for node in topology.get("nodes", []) if node.get("type") == "storage"]

    def inside_ctu_zone(node: dict[str, Any]) -> bool:
        row = int(node["row"])
        col = int(node["col"])
        return any(
            zone["y"] <= row < zone["y"] + zone["height"] and zone["x"] <= col < zone["x"] + zone["width"]
            for zone in ctu_zones
        )

    return [
        node
        for node in nodes
        if node.get("zoneId") in ctu_ids
        or (node.get("zoneId") not in all_zone_ids and inside_ctu_zone(node))
    ]


def shuttle_lanes(topology: dict[str, Any]) -> list[dict[str, Any]]:
    """四向车库可服务货道：[{cellId, layer, nodeId}]，供带深度的货道库存使用。"""
    from .pathfinding import parse_cell_id, shuttle_servable_slots

    node_by_pos = {
        node_position(node): node
        for node in topology.get("nodes", [])
        if node.get("type") == "storage"
    }
    lanes: list[dict[str, Any]] = []
    for lane_cell, layer in shuttle_servable_slots(topology):
        _floor, row, col = parse_cell_id(lane_cell)
        node = node_by_pos.get((row, col))
        lanes.append({
            "cellId": lane_cell,
            "layer": int(layer),
            "nodeId": node["id"] if node else None,
        })
    return lanes


def build_inventory(
    topology: dict[str, Any],
    catalog: dict[str, Any] | None = None,
    seed: int = INVENTORY_SEED,
) -> list[dict[str, Any]]:
    """把商品目录确定性地分配到当前拓扑的 CTU 库位上。

    每个 CTU storage 节点 × 1..17 层 = 一个库位槽；只认 CTU 货架里的库位，
    货架删除后残留的无归属 storage 节点不再产出货物。
    """
    if catalog is None:
        catalog = load_catalog()
    skus = catalog.get("skus", [])
    if not skus:
        return []

    ctu_sources = ctu_storage_nodes(topology)

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

    # 四向车库货道库存：每条可服务货道 0..SHUTTLE_LANE_DEPTH 箱，depth=1 为外层
    # （靠巷道面），深处箱被外层箱挡住——取出前需先倒库到空货道。
    # 填充按确定性轮转（约 1/4 货道留空作倒库缓冲），保证演示与测试可复现。
    lanes = shuttle_lanes(topology)
    rng.shuffle(lanes)
    fill_cycle = (3, 1, 2, 0, 2, 1, 3, 0)
    sku_index = len(slots)
    for lane_index, lane in enumerate(lanes):
        count = fill_cycle[lane_index % len(fill_cycle)]
        for depth in range(1, count + 1):
            sku = skus[sku_index % len(skus)]
            sku_index += 1
            inventory.append(
                {
                    "nodeId": lane["nodeId"],
                    "cellId": lane["cellId"],
                    "layer": lane["layer"],
                    "depth": depth,
                    "skuId": sku.get("skuId"),
                    "skuName": sku.get("name"),
                    "largeCategory": sku.get("largeCategory"),
                    "subclass": sku.get("subclass"),
                    "spec": sku.get("spec"),
                    "grade": sku.get("grade"),
                    "material": sku.get("material"),
                    "surfaceTreatment": sku.get("surfaceTreatment"),
                    "qty": 1,
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
