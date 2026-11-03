from __future__ import annotations

from typing import Any


DEVICE_NODE_TO_ROBOT_TYPE = {
    "ctu_robot": "ctu",
    "shuttle_robot": "shuttle",
}


def build_simulation_config(topology: dict[str, Any]) -> dict[str, Any]:
    """Create runnable defaults directly from the selected topology."""
    robots = []
    for node in topology.get("nodes", []):
        robot_type = DEVICE_NODE_TO_ROBOT_TYPE.get(node.get("type"))
        if not robot_type:
            continue
        robots.append(
            {
                "id": node["id"],
                "type": robot_type,
                "startNode": node["id"],
                "speed": 1.4 if robot_type == "ctu" else 1.1,
                "battery": 100,
                "enabled": True,
            }
        )

    first_conveyor = next(
        (cell["id"] for cell in topology.get("cells", []) if cell.get("type") == "conveyor"),
        None,
    )
    return {
        "id": f"config-{topology['id']}",
        "topologyId": topology["id"],
        "robots": robots,
        "orderGenerator": {
            "ratePerMinute": 6,
            "maxOrders": 18,
            "linesPerOrderMin": 1,
            "linesPerOrderMax": 3,
            "urgentInsertionTick": 20,
        },
        "pathPolicy": "astar",
        "conveyorPolicy": {"releaseThreshold": 0.8, "fifo": True},
        "faultInjection": {
            "robotFailureTick": 35,
            "robotFailureId": robots[0]["id"] if robots else None,
            "conveyorJammedTick": 45 if first_conveyor else None,
            "conveyorCellId": first_conveyor,
        },
        "seed": 7,
        # 每段输送线转运（下层出入口→升降移栽机 / 上层→拣货位 / 上层→出入口回程）
        # 所需 tick 数（有 pick_station 区域时启用）。
        "conveyorTransferTicks": 6,
        # 升降移栽机把货箱从下层抬到上层所需 tick 数。
        "liftTicks": 3,
        # 人工拣货默认占用 tick 数；可通过 complete-picking 接口提前结束。
        "pickTicks": 6,
        # 拣出的货物放到放置区（drop_zone）所需 tick 数；无 drop_zone 时跳过该阶段。
        "placeTicks": 3,
        # 90 tick 仅作为原统计周期；任务未完成时继续运行，2000 tick 是异常保护上限。
        "durationTicks": 90,
        "maxDurationTicks": 2000,
    }
