from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from .pathfinding import parse_cell_id


Deck = Literal["lower", "upper"]
Side = Literal["north", "east", "south", "west"]

SIDE_STEP: dict[Side, tuple[int, int]] = {
    "north": (-2, 0),
    # 仓库坐标约定：列号减小为东、列号增大为西。
    "east": (0, -2),
    "south": (2, 0),
    "west": (0, 2),
}
DIRECTION_SIDE: dict[str, Side] = {
    "up": "north",
    "right": "west",
    "down": "south",
    "left": "east",
}


def opposite_side(side: Side) -> Side:
    return {
        "north": "south",
        "south": "north",
        "east": "west",
        "west": "east",
    }[side]  # type: ignore[return-value]


def _anchor(cell: dict[str, Any]) -> tuple[int, int]:
    anchor = cell.get("conveyorAnchor") or {}
    row = int(anchor.get("row", int(cell["row"]) - int(cell["row"]) % 2))
    col = int(anchor.get("col", int(cell["col"]) - int(cell["col"]) % 2))
    return row, col


def normalize_conveyor_anchors(topology: dict[str, Any]) -> dict[str, Any]:
    """Persist one shared anchor for every legacy 2x2 conveyor block."""
    source_groups: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
    for cell in topology.get("cells", []):
        if cell.get("type") != "conveyor":
            continue
        row, col = _anchor(cell)
        source_groups.setdefault((str(cell.get("floor") or "1F"), row, col), []).append(cell)

    already_normalized = True
    for (_, inferred_row, inferred_col), cells in source_groups.items():
        anchor_cell = next(
            (
                cell
                for cell in cells
                if int(cell.get("row", -1)) == inferred_row
                and int(cell.get("col", -1)) == inferred_col
            ),
            cells[0],
        )
        expected = {"row": int(anchor_cell["row"]), "col": int(anchor_cell["col"])}
        if any(
            cell.get("conveyorAnchor") != expected
            or (
                cell is not anchor_cell
                and (cell.get("conveyorDecks") or cell.get("conveyorMode"))
            )
            for cell in cells
        ):
            already_normalized = False
            break
    if already_normalized:
        return topology

    normalized = deepcopy(topology)
    groups: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
    for cell in normalized.get("cells", []):
        if cell.get("type") != "conveyor":
            continue
        row, col = _anchor(cell)
        key = (str(cell.get("floor") or "1F"), row, col)
        groups.setdefault(key, []).append(cell)

    for (_, inferred_row, inferred_col), cells in groups.items():
        anchor_cell = next(
            (
                cell
                for cell in cells
                if int(cell.get("row", -1)) == inferred_row
                and int(cell.get("col", -1)) == inferred_col
            ),
            cells[0],
        )
        anchor = {"row": int(anchor_cell["row"]), "col": int(anchor_cell["col"])}
        config_source = next((cell for cell in cells if cell.get("conveyorDecks")), anchor_cell)
        if not anchor_cell.get("conveyorDecks") and config_source.get("conveyorDecks"):
            anchor_cell["conveyorDecks"] = deepcopy(config_source["conveyorDecks"])
            if config_source.get("conveyorMode") is not None:
                anchor_cell["conveyorMode"] = config_source["conveyorMode"]
        for cell in cells:
            cell["conveyorAnchor"] = dict(anchor)
            if cell is not anchor_cell:
                cell.pop("conveyorDecks", None)
                cell.pop("conveyorMode", None)
    return normalized


def _legacy_decks(cell: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output_side = DIRECTION_SIDE.get(str(cell.get("conveyorDirection") or "right"), "east")
    input_side = opposite_side(output_side)
    decks: dict[str, dict[str, Any]] = {
        "lower": {
            "enabled": True,
            "inputSide": input_side,
            "outputSide": output_side,
            "role": "outbound",
            "capacity": 1,
            "speed": 1.0,
        }
    }
    if not cell.get("conveyorGround") and int(cell.get("levels", 1) or 1) > 1:
        decks["upper"] = {
            "enabled": True,
            "inputSide": output_side,
            "outputSide": input_side,
            "role": "return",
            "capacity": 1,
            "speed": 1.0,
        }
    return decks


def conveyor_decks(cell: dict[str, Any]) -> dict[str, dict[str, Any]]:
    stored = cell.get("conveyorDecks")
    if isinstance(stored, dict) and isinstance(stored.get("lower"), dict):
        return stored
    return _legacy_decks(cell)


def conveyor_input_sides(config: dict[str, Any]) -> list[Side]:
    stored = config.get("inputSides")
    raw = stored if isinstance(stored, list) and stored else [config.get("inputSide")]
    sides: list[Side] = []
    for side in raw:
        if side in SIDE_STEP and side not in sides:
            sides.append(side)
    return sides


def conveyor_output_sides(config: dict[str, Any]) -> list[Side]:
    """读取模块层的出口方向列表：outputSides 数组（多出口分流），缺省回退 outputSide 单出口。"""
    stored = config.get("outputSides")
    raw = stored if isinstance(stored, list) and stored else [config.get("outputSide")]
    sides: list[Side] = []
    for side in raw:
        if side in SIDE_STEP and side not in sides:
            sides.append(side)
    return sides


def compile_conveyor_graph(topology: dict[str, Any]) -> dict[str, Any]:
    """把 2×2 输送块编译为按层有向图；旧 direction 数据在这里自动迁移读取。"""
    blocks: dict[tuple[str, int, int], dict[str, Any]] = {}
    for cell in topology.get("cells", []):
        if cell.get("type") != "conveyor":
            continue
        floor = str(cell.get("floor") or "1F")
        row, col = _anchor(cell)
        key = (floor, row, col)
        current = blocks.get(key)
        if current is None or (int(cell["row"]), int(cell["col"])) == (row, col):
            blocks[key] = cell

    nodes: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, str]] = []

    # 转向移载机是单层设备。设备自身保存的入口/出口配置优先于所在
    # 输送块的该层配置，另一层仍按原输送线配置参与编图。
    turn_transfers: dict[tuple[str, int, int, str], dict[str, Any]] = {}
    for transfer in topology.get("nodes", []):
        if transfer.get("type") != "turn_transfer":
            continue
        floor = str(transfer.get("floor") or "1F")
        row = int(transfer.get("row", 0))
        col = int(transfer.get("col", 0))
        mount = blocks.get((floor, row, col))
        inferred_deck = (
            "upper"
            if mount and not mount.get("conveyorGround") and int(mount.get("levels", 1) or 1) > 1
            else "lower"
        )
        deck = str(transfer.get("transferDeck") or inferred_deck)
        block_id = f"{floor}:{row}:{col}"
        if mount is None:
            issues.append({
                "blockId": block_id,
                "deck": deck,
                "message": f"{transfer.get('label') or '转向移载机'}必须安装在输送模块上。",
            })
            continue
        if deck == "upper" and (
            mount.get("conveyorGround") or int(mount.get("levels", 1) or 1) < 2
        ):
            issues.append({
                "blockId": block_id,
                "deck": deck,
                "message": f"{transfer.get('label') or '转向移载机'}选择了上层，但当前输送模块没有可用上层。",
            })
            continue
        input_side = transfer.get("transferInputSide")
        output_side = transfer.get("transferOutputSide")
        if input_side and input_side == output_side:
            issues.append({
                "blockId": block_id,
                "deck": deck,
                "message": f"{transfer.get('label') or '转向移载机'}的入口方向和出口方向不能相同。",
            })
        turn_transfers[(floor, row, col, deck)] = transfer

    def deck_config(
        floor: str,
        row: int,
        col: int,
        cell: dict[str, Any],
        deck: str,
    ) -> dict[str, Any] | None:
        stored = conveyor_decks(cell).get(deck)
        if not stored:
            return None
        config = dict(stored)
        transfer = turn_transfers.get((floor, row, col, deck))
        if transfer:
            if transfer.get("transferInputSide"):
                config["inputSide"] = transfer["transferInputSide"]
                config["inputSides"] = [transfer["transferInputSide"]]
            if transfer.get("transferOutputSide"):
                config["outputSide"] = transfer["transferOutputSide"]
        return config

    def same_line(left: dict[str, Any], right: dict[str, Any]) -> bool:
        left_id = left.get("conveyorLineId")
        right_id = right.get("conveyorLineId")
        return (not left_id and not right_id) or left_id == right_id

    for (floor, row, col), cell in blocks.items():
        neighbors = {
            side: blocks.get((floor, row + step[0], col + step[1]))
            for side, step in SIDE_STEP.items()
        }
        for deck in ("lower", "upper"):
            config = deck_config(floor, row, col, cell, deck)
            if not config or not config.get("enabled", True):
                continue
            key = f"{floor}:{row}:{col}:{deck}"
            output_side: Side = str(config.get("outputSide") or "east")  # type: ignore[assignment]
            input_side: Side = str(config.get("inputSide") or opposite_side(output_side))  # type: ignore[assignment]
            input_sides = conveyor_input_sides(config) or [input_side]
            output_sides = conveyor_output_sides(config) or [output_side]
            if cell.get("conveyorDecks") and output_side in input_sides:
                issues.append({"blockId": key, "deck": deck, "message": "入口侧和出口侧不能相同。"})
            if cell.get("conveyorDecks") and cell.get("conveyorGround") and deck == "upper":
                issues.append({"blockId": key, "deck": deck, "message": "地面输送线不能配置上层。"})
            # 多出口：逐个出口方向解析下游邻块（分流器），主出口 = 第一个连通的出口。
            # 单出口模块（未配置 outputSides）行为与旧编译完全一致。
            nexts: dict[str, str | None] = {}
            for out_side in output_sides:
                out_dr, out_dc = SIDE_STEP[out_side]
                out_block = blocks.get((floor, row + out_dr, col + out_dc))
                out_key: str | None = None
                if out_block is not None:
                    out_config = deck_config(floor, row + out_dr, col + out_dc, out_block, deck)
                    if (
                        same_line(cell, out_block)
                        and out_config
                        and out_config.get("enabled", True)
                        and opposite_side(out_side) in conveyor_input_sides(out_config)
                    ):
                        out_key = f"{floor}:{row + out_dr}:{col + out_dc}:{deck}"
                nexts[out_side] = out_key
            next_key: str | None = next((nk for nk in nexts.values() if nk), None)
            for accepted_input in input_sides:
                previous_block = neighbors.get(accepted_input)
                if (
                    cell.get("conveyorDecks")
                    and previous_block is not None
                    and previous_block.get("conveyorDecks")
                    and same_line(cell, previous_block)
                ):
                    previous_step = SIDE_STEP[accepted_input]
                    previous_config = deck_config(
                        floor,
                        row + previous_step[0],
                        col + previous_step[1],
                        previous_block,
                        deck,
                    )
                    if not (
                        previous_config
                        and previous_config.get("enabled", True)
                        and (
                            previous_config.get("outputSide") == opposite_side(accepted_input)
                            or opposite_side(accepted_input) in conveyor_output_sides(previous_config)
                        )
                    ):
                        issues.append({
                            "blockId": key,
                            "deck": deck,
                            "message": "入口方向与上一手动配置模块的出口方向不匹配。",
                        })
            nodes[key] = {
                "id": key,
                "floor": floor,
                "row": row,
                "col": col,
                "deck": deck,
                "inputSide": input_side,
                "inputSides": input_sides,
                "outputSide": output_side,
                "outputSides": output_sides,
                "role": config.get("role", "custom"),
                "capacity": max(1, int(config.get("capacity", 1) or 1)),
                "speed": max(0.01, float(config.get("speed", 1.0) or 1.0)),
                "ground": bool(cell.get("conveyorGround")),
                "lineId": cell.get("conveyorLineId"),
                "next": next_key,
                "nexts": nexts,
            }

    indegree = {key: 0 for key in nodes}
    for node in nodes.values():
        for branch_next in (node.get("nexts") or {}).values():
            if branch_next in indegree:
                indegree[branch_next] += 1
    explicit = any(
        isinstance(cell.get("conveyorDecks"), dict)
        for cell in topology.get("cells", [])
        if cell.get("type") == "conveyor"
    ) or any(
        node.get("type") == "turn_transfer"
        and (node.get("transferInputSide") or node.get("transferOutputSide"))
        for node in topology.get("nodes", [])
    )
    if explicit:
        for zone in topology.get("zones", []):
            if zone.get("type") not in {"port", "pick_station"}:
                continue
            binding = zone.get("conveyorBinding")
            if not isinstance(binding, dict):
                continue
            block_id = str(binding.get("blockId") or "")
            side = binding.get("side")
            lower = nodes.get(f"{block_id}:lower")
            upper = nodes.get(f"{block_id}:upper")
            if lower is None:
                issues.append({"blockId": block_id, "deck": "lower", "message": f"{zone.get('label')}绑定的输送块不存在。"})
                continue
            if zone.get("type") == "port":
                if side not in lower.get("inputSides", [lower.get("inputSide")]):
                    issues.append({"blockId": block_id, "deck": "lower", "message": f"{zone.get('label')}连接侧与下层入口侧不一致。"})
                if upper and upper.get("outputSide") != side:
                    issues.append({"blockId": block_id, "deck": "upper", "message": f"{zone.get('label')}连接侧与上层出口侧不一致。"})
            else:
                if lower.get("outputSide") != side:
                    issues.append({"blockId": block_id, "deck": "lower", "message": f"{zone.get('label')}连接侧与下层出口侧不一致。"})
                if upper and side not in upper.get("inputSides", [upper.get("inputSide")]):
                    issues.append({"blockId": block_id, "deck": "upper", "message": f"{zone.get('label')}连接侧与上层入口侧不一致。"})
    return {"nodes": nodes, "indegree": indegree, "explicit": explicit, "issues": issues}


def route_from_cell(
    graph: dict[str, Any],
    cell_id: str | None,
    deck: Deck,
    *,
    ground: bool | None = None,
) -> list[str]:
    if not cell_id:
        return []
    try:
        floor, row, col = parse_cell_id(cell_id)
    except (TypeError, ValueError):
        return []
    candidates = [
        node
        for node in graph.get("nodes", {}).values()
        if node.get("floor") == floor
        and node.get("deck") == deck
        and (ground is None or bool(node.get("ground")) == ground)
    ]
    if not candidates:
        return []
    sources = [node for node in candidates if int(graph.get("indegree", {}).get(node["id"], 0)) == 0]
    start_pool = sources or candidates
    current = min(
        start_pool,
        key=lambda node: abs(int(node["row"]) - row) + abs(int(node["col"]) - col),
    )
    route: list[str] = []
    seen: set[str] = set()
    while current and current["id"] not in seen:
        seen.add(current["id"])
        route.append(current["id"])
        next_id = current.get("next")
        current = graph.get("nodes", {}).get(next_id) if next_id else None
    return route


def route_from_block(graph: dict[str, Any], block_id: str | None, deck: Deck) -> list[str]:
    """从区域显式绑定的输送块出发，沿指定层的有向边生成路线。"""
    if not block_id:
        return []
    start_id = block_id if block_id.endswith(f":{deck}") else f"{block_id}:{deck}"
    current = graph.get("nodes", {}).get(start_id)
    route: list[str] = []
    seen: set[str] = set()
    while current and current["id"] not in seen:
        seen.add(current["id"])
        route.append(current["id"])
        next_id = current.get("next")
        current = graph.get("nodes", {}).get(next_id) if next_id else None
    return route


def nearest_lift_id(topology: dict[str, Any], block_id: str | None) -> str | None:
    if not block_id:
        return None
    parts = block_id.split(":")
    if len(parts) < 4:
        return None
    floor, row_text, col_text = parts[0], parts[1], parts[2]
    row, col = int(row_text), int(col_text)
    lifts = [
        node for node in topology.get("nodes", [])
        if node.get("type") == "lift_transfer" and str(node.get("floor") or "1F") == floor
    ]
    if not lifts:
        return None
    lift = min(lifts, key=lambda node: abs(int(node["row"]) - row) + abs(int(node["col"]) - col))
    return str(lift.get("id"))


def route_from_block_branch(
    graph: dict[str, Any],
    block_id: str | None,
    deck: Deck,
    *,
    branch_side: Side,
    branch_target: str | None = None,
) -> list[str]:
    """沿多出口图行走：在第一个「branch_side 出口指向 branch_target」的块上分叉，
    其余块走主出口；图无该分叉时沿主出口走到链尾（退化为 route_from_block）。

    用于分流场景（如回库支线）：主出口继续去出货口，分叉出口送回库段。
    """
    if not block_id:
        return []
    start = block_id if block_id.endswith(f":{deck}") else f"{block_id}:{deck}"
    current = graph.get("nodes", {}).get(start)
    route: list[str] = []
    seen: set[str] = set()
    while current and current["id"] not in seen:
        seen.add(current["id"])
        route.append(current["id"])
        nexts = current.get("nexts") or {}
        candidate = nexts.get(branch_side)
        next_id: str | None = None
        if candidate and (branch_target is None or candidate == branch_target):
            next_id = candidate
        if not next_id:
            next_id = current.get("next")
        current = graph.get("nodes", {}).get(next_id) if next_id else None
    return route
