from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.services.pathfinding import (
    build_shuttle_lookup,
    cell_id,
    shortest_path_shuttle,
    shuttle_servable_slots,
)
from app.services.simulation import (
    EMPTY_PALLET_RETURN_CELL_ID,
    EMPTY_PALLET_TASK_KIND,
    SimulationService,
)
from app.services.simulation_config import build_simulation_config


TOPOLOGY_PATH = Path(__file__).resolve().parents[2] / "data" / "topology_666.json"


class MemoryRepository:
    def __init__(self) -> None:
        self.states: list[dict[str, Any]] = []

    def add_event(self, _run_id: str, _tick: int, _event_type: str, _payload: dict[str, Any]) -> None:
        return None

    def create_run(self, _run_id: str, _status: str, _topology_id: str, payload: dict[str, Any], _metrics: dict[str, Any]) -> dict[str, Any]:
        self.states.append(deepcopy(payload))
        return {}

    def update_run(self, _run_id: str, _status: str, payload: dict[str, Any], _metrics: dict[str, Any]) -> dict[str, Any]:
        self.states.append(deepcopy(payload))
        return {}


def _topology() -> dict[str, Any]:
    return json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))


def _runtime(topology: dict[str, Any]):
    service = SimulationService(MemoryRepository())
    runtime = service._create_runtime(
        "run-empty-pallet",
        topology,
        build_simulation_config(topology),
        [],
        [],
    )
    return service, runtime


def _handoff_cell(topology: dict[str, Any]) -> str:
    handoff = next(node for node in topology["nodes"] if node.get("type") == "picking_handoff")
    return cell_id(int(handoff["row"]), int(handoff["col"]), handoff.get("floor") or "1F")


def _place_shuttle(runtime, robot, cell: str, layer: int = 1) -> None:
    robot["currentCellId"] = cell
    robot["currentLayer"] = layer
    robot["path"] = [cell]
    robot["pathLayers"] = [layer]
    robot["status"] = "handling"
    robot["carrying"] = []
    robot["load"] = 0


def test_pickup_marks_pallet_empty_only_when_lane_becomes_empty() -> None:
    topology = _topology()
    service, runtime = _runtime(topology)
    cell_id_, layer = shuttle_servable_slots(topology)[0]
    delivery_cell = _handoff_cell(topology)
    robot = next(item for item in runtime.state["robots"] if item["type"] == "shuttle")

    # 货道里还有更深层的箱子：托盘不为空，不触发回收。
    runtime.state["inventory"] = [
        {"cellId": cell_id_, "layer": layer, "depth": 1, "skuId": "SKU-X"},
        {"cellId": cell_id_, "layer": layer, "depth": 2, "skuId": "SKU-Y"},
    ]
    _place_shuttle(runtime, robot, cell_id_, layer)
    task = service._build_task(
        topology, "task-deep-lane", "shuttle", "cross_zone",
        [{"cellId": cell_id_, "layer": layer, "depth": 1}], delivery_cell, False, 3,
    )
    runtime.state["tasks"].append(task)
    robot["currentTaskId"] = task["id"]
    task["phase"] = "pickup_from_shuttle"
    service._advance_robot_phase(runtime, robot, task)

    assert not task.get("palletBecomesEmpty")

    # 最后一箱被取走：托盘变空，标记由这台车送到 (76,35)。
    runtime.state["inventory"] = [{"cellId": cell_id_, "layer": layer, "depth": 1, "skuId": "SKU-X"}]
    _place_shuttle(runtime, robot, cell_id_, layer)
    last_task = service._build_task(
        topology, "task-last-box", "shuttle", "cross_zone",
        [{"cellId": cell_id_, "layer": layer, "depth": 1}], delivery_cell, False, 3,
    )
    runtime.state["tasks"].append(last_task)
    robot["currentTaskId"] = last_task["id"]
    last_task["phase"] = "pickup_from_shuttle"
    service._advance_robot_phase(runtime, robot, last_task)

    assert last_task.get("palletBecomesEmpty") is True
    assert last_task.get("palletSourceCellId") == cell_id_
    assert last_task.get("palletSourceLayer") == layer
    assert runtime.state["inventory"] == []
    # 取货阶段不产生任何回收任务：托盘还在车上，等机械臂取走货物后才变空。
    assert not [t for t in runtime.state["tasks"] if t.get("taskKind") == EMPTY_PALLET_TASK_KIND]


def test_empty_pallet_rides_the_same_shuttle_to_target_cell() -> None:
    topology = _topology()
    service, runtime = _runtime(topology)
    handoff_cell = _handoff_cell(topology)
    robot = next(item for item in runtime.state["robots"] if item["type"] == "shuttle")
    _place_shuttle(runtime, robot, handoff_cell, 1)
    robot["carrying"] = [{"cellId": "cell-r96-c33", "layer": 1, "skuId": "SKU-X"}]

    parent = service._build_task(
        topology, "task-parent", "shuttle", "cross_zone",
        [{"cellId": "cell-r96-c33", "layer": 1, "depth": 1}], handoff_cell, False, 3,
    )
    parent["status"] = "in_progress"
    parent["phase"] = "waiting_arm_handoff"
    parent["assignedRobotId"] = robot["id"]
    parent["palletBecomesEmpty"] = True
    parent["palletSourceCellId"] = "cell-r96-c33"
    parent["palletSourceLayer"] = 1
    runtime.state["tasks"].append(parent)
    robot["currentTaskId"] = parent["id"]

    assert service._send_empty_pallet_to_stack(runtime, robot, parent, {"handoffCellId": handoff_cell})

    return_tasks = [t for t in runtime.state["tasks"] if t.get("taskKind") == EMPTY_PALLET_TASK_KIND]
    assert len(return_tasks) == 1
    return_task = return_tasks[0]
    assert return_task["deliveryCellId"] == EMPTY_PALLET_RETURN_CELL_ID
    assert return_task["phase"] == "delivery_empty_pallet"
    assert robot["currentTaskId"] == return_task["id"]
    # 空托盘随车走：不是回货位取托盘，而是从交接处直接开往堆叠点。
    assert robot["carrying"] == [{"cellId": "cell-r96-c33", "layer": 1, "kind": "empty_pallet"}]

    for _ in range(600):
        service._step_runtime(runtime, persist=False)
        if runtime.state["emptyPalletStack"]["count"] >= 1:
            break

    stack = runtime.state["emptyPalletStack"]
    assert stack["cellId"] == EMPTY_PALLET_RETURN_CELL_ID
    assert stack["count"] == 1
    assert stack["capacity"] == 6
    assert return_task["status"] == "completed"
    assert return_task["palletReturnedTick"] is not None
    assert robot["carrying"] == []


def test_arm_handoff_hands_empty_pallet_to_same_shuttle() -> None:
    """机械臂把最后一批货物放上输送线后，空托盘留在车上并被送到 (76,35)。"""
    topology = _topology()
    service, runtime = _runtime(topology)
    target = runtime.arm_handoff
    assert target is not None
    robot = next(item for item in runtime.state["robots"] if item["type"] == "shuttle")
    _place_shuttle(runtime, robot, target["handoffCellId"], 1)

    parent = service._build_task(
        topology, "task-handoff", "shuttle", "cross_zone",
        [{"cellId": "cell-r96-c33", "layer": 1, "depth": 1}], target["handoffCellId"], False, 3,
    )
    parent["status"] = "in_progress"
    parent["phase"] = "waiting_arm_handoff"
    parent["assignedRobotId"] = robot["id"]
    parent["palletBecomesEmpty"] = True
    parent["palletSourceCellId"] = "cell-r96-c33"
    parent["palletSourceLayer"] = 1
    runtime.state["tasks"].append(parent)
    robot["currentTaskId"] = parent["id"]
    robot["carrying"] = [{"cellId": "cell-r96-c33", "layer": 1, "depth": 1, "skuId": "SKU-X"}]

    runtime.state["pickingArm"].update({
        "status": "busy",
        "stage": "place",
        "remainingTicks": 1,
        "taskId": parent["id"],
        "robotId": robot["id"],
    })
    service._advance_picking_arm(runtime)

    return_tasks = [t for t in runtime.state["tasks"] if t.get("taskKind") == EMPTY_PALLET_TASK_KIND]
    assert len(return_tasks) == 1
    # 空托盘随车走：没有「回货位取托盘」的取货阶段，直接从交接处开往 (76,35)。
    assert return_tasks[0]["phase"] == "delivery_empty_pallet"
    assert robot["currentTaskId"] == return_tasks[0]["id"]
    assert robot["carrying"] == [{"cellId": "cell-r96-c33", "layer": 1, "kind": "empty_pallet"}]
    assert parent["assignedRobotId"] is None

    for _ in range(600):
        service._step_runtime(runtime, persist=False)
        if runtime.state["emptyPalletStack"]["count"] >= 1:
            break

    assert runtime.state["emptyPalletStack"]["count"] == 1
    assert return_tasks[0]["status"] == "completed"


def test_same_layer_shuttle_paths_never_enter_the_lift() -> None:
    """四向车在巷道里同层行驶：起讫同层时不允许借道升降机竖井。"""
    topology = _topology()
    _, _, lift = build_shuttle_lookup(topology)
    home = next(node for node in topology["nodes"] if node.get("type") == "shuttle_robot")
    start = cell_id(int(home["row"]), int(home["col"]), home.get("floor") or "1F")
    same_layer_slots = [cell for cell, layer in shuttle_servable_slots(topology) if layer == 1]
    assert same_layer_slots

    for goal in same_layer_slots:
        path = shortest_path_shuttle(topology, start, 1, goal, 1)
        assert path, f"1F 货位不可达: {goal}"
        assert not [step for step in path if (step[0], step[1]) in lift], f"同层路径借道升降机: {goal}"


def test_pallet_return_task_targets_the_original_layer() -> None:
    """余货回库任务的目标层必须写回原货位所在层，不能停留在 1F。"""
    topology = _topology()
    service, runtime = _runtime(topology)
    handoff = runtime.arm_handoff
    assert handoff is not None
    robot = next(item for item in runtime.state["robots"] if item["type"] == "shuttle")
    lane_cell, lane_layer = next((cell, layer) for cell, layer in shuttle_servable_slots(topology) if layer == 3)

    robot["currentCellId"] = handoff["handoffCellId"]
    robot["currentLayer"] = 1
    parent = service._build_task(
        topology, "task-layer-3-pick", "shuttle", "cross_zone",
        [{"cellId": lane_cell, "layer": lane_layer, "depth": 1, "skuId": "SKU-A"}],
        handoff["handoffCellId"], False, 3,
    )
    parent["palletSourceCellId"] = lane_cell
    parent["palletSourceLayer"] = lane_layer
    parent["palletExtraBoxes"] = [{"skuId": "SKU-B", "depth": 2}]
    runtime.state["tasks"].append(parent)

    assert service._send_pallet_back_to_lane(runtime, robot, parent, handoff)
    return_task = next(t for t in runtime.state["tasks"] if t.get("taskKind") == "pallet_return_to_lane")
    assert return_task["deliveryCellId"] == lane_cell
    assert return_task["targetCellId"] == lane_cell
    assert return_task["targetLayer"] == lane_layer


def test_pallet_stack_caps_at_six_layers_and_counts_overflow() -> None:
    topology = _topology()
    service, runtime = _runtime(topology)
    robot = next(item for item in runtime.state["robots"] if item["type"] == "shuttle")
    task = {
        "id": "task-empty-pallet-overflow",
        "status": "assigned",
        "phase": "delivery_empty_pallet",
        "taskKind": EMPTY_PALLET_TASK_KIND,
        "palletSourceCellId": "cell-r96-c33",
        "palletSourceLayer": 1,
        "history": [],
    }
    runtime.state["tasks"].append(task)
    robot["currentTaskId"] = task["id"]

    for _ in range(8):
        task["status"] = "assigned"
        task["phase"] = "delivery_empty_pallet"
        robot["currentTaskId"] = task["id"]
        service._deliver_empty_pallet(runtime, robot, task)

    stack = runtime.state["emptyPalletStack"]
    assert stack["count"] == 6
    assert stack["overflow"] == 2


def test_remaining_goods_ride_back_to_the_source_lane() -> None:
    """托盘还有余货：余货随托盘回原货位，车不能空着回待命位。"""
    topology = _topology()
    service, runtime = _runtime(topology)
    target = runtime.arm_handoff
    assert target is not None
    robot = next(item for item in runtime.state["robots"] if item["type"] == "shuttle")
    lane_cell, layer = "cell-r96-c33", 1

    # 一货位一托盘：货道 3 箱，订单只要 depth=1 那箱。
    runtime.state["inventory"] = [
        {"cellId": lane_cell, "layer": layer, "depth": 1, "skuId": "SKU-A"},
        {"cellId": lane_cell, "layer": layer, "depth": 2, "skuId": "SKU-B"},
        {"cellId": lane_cell, "layer": layer, "depth": 3, "skuId": "SKU-C"},
    ]
    _place_shuttle(runtime, robot, lane_cell, layer)
    parent = service._build_task(
        topology, "task-lane-pick", "shuttle", "cross_zone",
        [{"cellId": lane_cell, "layer": layer, "depth": 1, "skuId": "SKU-A"}],
        target["handoffCellId"], False, 3,
    )
    parent["taskKind"] = "shuttle_order_pick"
    parent["armHandoff"] = True
    parent["status"] = "in_progress"
    runtime.state["tasks"].append(parent)
    robot["currentTaskId"] = parent["id"]
    parent["phase"] = "pickup_from_shuttle"
    service._advance_robot_phase(runtime, robot, parent)

    # 整托盘抬出：库位清空，3 箱都在车上，余货带 pallet_goods 标记。
    assert runtime.state["inventory"] == []
    assert len(robot["carrying"]) == 3
    assert sum(1 for box in robot["carrying"] if box.get("kind") == "pallet_goods") == 2
    assert not parent.get("palletBecomesEmpty")

    # 机械臂只取走订单要的那箱，其余随车回库。
    robot["currentCellId"] = target["handoffCellId"]
    robot["path"] = [target["handoffCellId"]]
    robot["pathLayers"] = [1]
    robot["status"] = "handling"
    parent["phase"] = "waiting_arm_handoff"
    runtime.state["pickingArm"].update({
        "status": "busy",
        "stage": "place",
        "remainingTicks": 1,
        "taskId": parent["id"],
        "robotId": robot["id"],
    })
    service._advance_picking_arm(runtime)

    assert [box.get("skuId") for box in parent["deliveredGoods"]] == ["SKU-A"]
    return_tasks = [t for t in runtime.state["tasks"] if t.get("taskKind") == "pallet_return_to_lane"]
    assert len(return_tasks) == 1
    return_task = return_tasks[0]
    assert return_task["phase"] == "return_pallet_to_lane"
    assert return_task["deliveryCellId"] == lane_cell
    assert robot["currentTaskId"] == return_task["id"]
    assert len(robot["carrying"]) == 2
    # 不是空托盘：不应触发 (76,35) 堆叠。
    assert not [t for t in runtime.state["tasks"] if t.get("taskKind") == EMPTY_PALLET_TASK_KIND]

    for _ in range(600):
        service._step_runtime(runtime, persist=False)
        if return_task["status"] == "completed":
            break

    assert return_task["status"] == "completed"
    assert robot["carrying"] == []
    restored = sorted(
        (int(item["depth"]), item["skuId"])
        for item in runtime.state["inventory"]
        if item.get("cellId") == lane_cell
    )
    assert restored == [(2, "SKU-B"), (3, "SKU-C")]
    assert runtime.state["emptyPalletStack"]["count"] == 0
