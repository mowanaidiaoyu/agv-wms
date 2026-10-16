from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from .pathfinding import node_to_cell_id

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
    ctu_sources = [node for node in nodes if node["type"] in {"storage", "buffer"} and node.get("zoneId") == "zone-ctu"]
    if not ctu_sources:
        ctu_sources = [node for node in nodes if node["type"] in {"storage", "buffer"}]
    if not ctu_sources:
        return []

    slots: list[tuple[dict[str, Any], int]] = [(node, layer) for node in ctu_sources for layer in range(1, CTU_LAYERS + 1)]
    rng = random.Random(seed)
    rng.shuffle(slots)

    inventory: list[dict[str, Any]] = []
    for index, (node, layer) in enumerate(slots):
        sku = skus[index % len(skus)]
        inventory.append(
            {
                "nodeId": node["id"],
                "cellId": node_to_cell_id(topology, node["id"]),
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
