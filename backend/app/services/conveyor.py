from __future__ import annotations

from typing import Any, Literal

from .pathfinding import parse_cell_id


Deck = Literal["lower", "upper"]
Side = Literal["north", "east", "south", "west"]

SIDE_STEP: dict[Side, tuple[int, int]] = {
    "north": (-2, 0),
    "east": (0, 2),
    "south": (2, 0),
    "west": (0, -2),
}
DIRECTION_SIDE: dict[str, Side] = {
    "up": "north",
    "right": "east",
    "down": "south",
    "left": "west",
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
            if cell.get("conveyorDecks") and input_side == output_side:
                issues.append({"blockId": key, "deck": deck, "message": "入口侧和出口侧不能相同。"})
            if cell.get("conveyorDecks") and cell.get("conveyorGround") and deck == "upper":
                issues.append({"blockId": key, "deck": deck, "message": "地面输送线不能配置上层。"})
            dr, dc = SIDE_STEP[output_side]
            next_block = blocks.get((floor, row + dr, col + dc))
            next_key: str | None = None
            if next_block is not None:
                next_config = deck_config(floor, row + dr, col + dc, next_block, deck)
                if (
                    same_line(cell, next_block)
                    and
                    next_config
                    and next_config.get("enabled", True)
                    and next_config.get("inputSide") == opposite_side(output_side)
                ):
                    next_key = f"{floor}:{row + dr}:{col + dc}:{deck}"
                elif (
                    cell.get("conveyorDecks")
                    and next_block.get("conveyorDecks")
                    and same_line(cell, next_block)
                ):
                    issues.append({
                        "blockId": key,
                        "deck": deck,
                        "message": "出口方向与下一手动配置模块的入口方向不匹配。",
                    })
            previous_block = neighbors.get(input_side)
            if (
                cell.get("conveyorDecks")
                and previous_block is not None
                and previous_block.get("conveyorDecks")
                and same_line(cell, previous_block)
            ):
                previous_step = SIDE_STEP[input_side]
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
                    and previous_config.get("outputSide") == opposite_side(input_side)
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
                "outputSide": output_side,
                "role": config.get("role", "custom"),
                "capacity": max(1, int(config.get("capacity", 1) or 1)),
                "speed": max(0.01, float(config.get("speed", 1.0) or 1.0)),
                "ground": bool(cell.get("conveyorGround")),
                "lineId": cell.get("conveyorLineId"),
                "next": next_key,
            }

    indegree = {key: 0 for key in nodes}
    for node in nodes.values():
        if node["next"] in indegree:
            indegree[node["next"]] += 1
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
                if lower.get("inputSide") != side:
                    issues.append({"blockId": block_id, "deck": "lower", "message": f"{zone.get('label')}连接侧与下层入口侧不一致。"})
                if upper and upper.get("outputSide") != side:
                    issues.append({"blockId": block_id, "deck": "upper", "message": f"{zone.get('label')}连接侧与上层出口侧不一致。"})
            else:
                if lower.get("outputSide") != side:
                    issues.append({"blockId": block_id, "deck": "lower", "message": f"{zone.get('label')}连接侧与下层出口侧不一致。"})
                if upper and upper.get("inputSide") != side:
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
