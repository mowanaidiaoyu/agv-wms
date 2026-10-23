from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from app.services.goods import build_inventory
from app.services.simulation import (
    DEFAULT_SPEED_MULTIPLIER,
    TICK_INTERVAL_SECONDS,
    RunRuntime,
    SimulationService,
)
from app.services.simulation_config import build_simulation_config


TOPOLOGY_PATH = Path(__file__).resolve().parents[2] / "data" / "topology_666.json"


class MemoryRepository:
    def __init__(self) -> None:
        self.events: list[tuple[str, int, str, dict[str, Any]]] = []
        self.states: list[dict[str, Any]] = []

    def add_event(self, run_id: str, tick: int, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append((run_id, tick, event_type, deepcopy(payload)))

    def create_run(
        self,
        _run_id: str,
        _status: str,
        _topology_id: str,
        payload: dict[str, Any],
        _metrics: dict[str, Any],
    ) -> dict[str, Any]:
        self.states.append(deepcopy(payload))
        return {}

    def update_run(
        self,
        _run_id: str,
        _status: str,
        payload: dict[str, Any],
        _metrics: dict[str, Any],
    ) -> dict[str, Any]:
        self.states.append(deepcopy(payload))
        return {}


def _topology() -> dict[str, Any]:
    return json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))


def _order(topology: dict[str, Any]) -> dict[str, Any]:
    slot = build_inventory(topology)[0]
    return {
        "id": "ORD-TICK-001",
        "priority": "normal",
        "targetStation": "出库口 01",
        "createdAt": "2026-10-23T00:00:00Z",
        "lines": [{
            "id": "line-tick-001",
            "skuId": slot["skuId"],
            "skuName": slot["skuName"],
            "spec": slot.get("spec"),
            "quantity": 1,
        }],
    }


def _transfer() -> dict[str, Any]:
    return {
        "id": "transfer-tick-001",
        "vehicleId": "SHUTTLE-UNIFIED-01",
        "skuId": "SKU-001",
        "skuName": "SKU-001",
        "sourceLocationId": "SIM-BIN-L1-r100-c7",
        "destinationLocationId": "SIM-BIN-L4-r100-c51",
        "sourceTrackNode": "L1-r100-c7",
        "destinationTrackNode": "L4-r100-c51",
        "sourceLevel": 1,
        "destinationLevel": 4,
        "quantity": 2,
        "algorithm": "astar",
        "initialSourceQuantity": 10,
        "initialDestinationQuantity": 5,
    }


def test_start_creates_background_run_and_advances_multiple_ticks(monkeypatch) -> None:
    topology = _topology()
    service = SimulationService(MemoryRepository())
    broadcast_ticks: list[int] = []

    async def capture_tick(current: RunRuntime) -> None:
        broadcast_ticks.append(current.state["tick"])

    monkeypatch.setattr("app.services.simulation.TICK_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(service, "_broadcast_tick", capture_tick)
    monkeypatch.setattr(service, "_all_tasks_completed", lambda current: current.state["tick"] >= 3)

    async def scenario() -> None:
        result = await service.start(topology, [], [_transfer()])
        runtime = service.runtimes[result["runId"]]
        assert result["status"] == "running"
        assert runtime.task_handle is not None
        await runtime.task_handle
        assert runtime.state["tick"] == 3

    asyncio.run(scenario())
    assert broadcast_ticks == [1, 2, 3]


@pytest.mark.parametrize(
    ("with_order", "with_transfer"),
    [(True, False), (False, True), (True, True)],
    ids=["orders-only", "transfer-only", "orders-and-transfer"],
)
def test_run_advances_multiple_ticks_for_unified_task_mix(monkeypatch, with_order: bool, with_transfer: bool) -> None:
    topology = _topology()
    service = SimulationService(MemoryRepository())
    runtime = service._create_runtime(
        f"run-{with_order}-{with_transfer}",
        topology,
        build_simulation_config(topology),
        [_order(topology)] if with_order else [],
        [_transfer()] if with_transfer else [],
    )
    broadcast_ticks: list[int] = []

    async def no_wait(_seconds: float) -> None:
        return None

    async def capture_tick(current: RunRuntime) -> None:
        broadcast_ticks.append(current.state["tick"])

    monkeypatch.setattr("app.services.simulation.asyncio.sleep", no_wait)
    monkeypatch.setattr(service, "_broadcast_tick", capture_tick)
    monkeypatch.setattr(service, "_all_tasks_completed", lambda current: current.state["tick"] >= 3)

    asyncio.run(service._loop(runtime))

    assert runtime.state["tick"] == 3
    assert broadcast_ticks == [1, 2, 3]
    if with_transfer:
        transfer = next(task for task in runtime.state["tasks"] if task.get("mode") == "shuttle_transfer")
        assert transfer["phase"] != "pickup_wait"
        assert transfer["inTransitQuantity"] == 2


def _controlled_runtime() -> RunRuntime:
    state = {
        "id": "run-controlled",
        "status": "running",
        "stopReason": None,
        "tick": 0,
        "topologyId": "topology-test",
        "tasks": [{"id": "task-open", "status": "assigned"}],
        "robots": [],
        "orders": [],
        "inventory": [],
        "alerts": [],
        "queues": {"conveyor": [], "station": [], "buffer": []},
        "createdAt": "",
        "updatedAt": "",
    }
    return RunRuntime(
        run_id="run-controlled",
        topology={},
        config={"durationTicks": 90, "maxDurationTicks": 2000},
        state=state,
        metrics={},
        initial_state=deepcopy(state),
        initial_metrics={},
    )


def test_pause_resume_stop_and_reset_lifecycle(monkeypatch) -> None:
    service = SimulationService(MemoryRepository())
    runtime = _controlled_runtime()
    service.runtimes[runtime.run_id] = runtime

    def step(current: RunRuntime, *, persist: bool) -> None:
        current.state["tick"] += 1

    async def broadcast(_current: RunRuntime) -> None:
        return None

    monkeypatch.setattr("app.services.simulation.TICK_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(service, "_step_runtime", step)
    monkeypatch.setattr(service, "_broadcast_tick", broadcast)
    monkeypatch.setattr(service, "_all_tasks_completed", lambda _current: False)

    async def scenario() -> None:
        runtime.task_handle = asyncio.create_task(service._loop(runtime))
        while runtime.state["tick"] < 3:
            await asyncio.sleep(0.001)

        service.pause(runtime.run_id)
        paused_tick = runtime.state["tick"]
        await asyncio.sleep(0.01)
        assert runtime.state["tick"] == paused_tick

        service.resume(runtime.run_id)
        while runtime.state["tick"] == paused_tick:
            await asyncio.sleep(0.001)
        resumed_tick = runtime.state["tick"]

        service.stop(runtime.run_id)
        await runtime.task_handle
        await asyncio.sleep(0.005)
        assert runtime.state["tick"] == resumed_tick
        assert runtime.state["status"] == "stopped"

        await service.reset(runtime.run_id)
        assert runtime.state["tick"] == 0
        assert runtime.state["status"] == "paused"
        assert runtime.paused is True
        assert runtime.task_handle is not None
        runtime.task_handle.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runtime.task_handle

    asyncio.run(scenario())


def test_background_exception_stops_run_and_exposes_reason(monkeypatch) -> None:
    service = SimulationService(MemoryRepository())
    runtime = _controlled_runtime()
    messages: list[dict[str, Any]] = []

    def failing_step(current: RunRuntime, *, persist: bool) -> None:
        current.state["tick"] += 1
        if current.state["tick"] == 2:
            raise RuntimeError("pickup adapter exploded")

    async def capture(_run_id: str, message: dict[str, Any]) -> None:
        messages.append(message)

    async def no_wait(_seconds: float) -> None:
        return None

    monkeypatch.setattr(service, "_step_runtime", failing_step)
    monkeypatch.setattr(service, "_broadcast_tick", lambda _runtime: asyncio.sleep(0))
    monkeypatch.setattr(service.hub, "broadcast", capture)
    monkeypatch.setattr("app.services.simulation.asyncio.sleep", no_wait)

    asyncio.run(service._loop(runtime))

    assert runtime.state["tick"] == 2
    assert runtime.state["status"] == "stopped"
    assert runtime.state["stopReason"] == "runtime_error"
    assert runtime.state["runtimeError"]["message"] == "pickup adapter exploded"
    assert messages[-1]["type"] == "simulation_error"


def test_websocket_broadcast_delivers_consecutive_tick_snapshots(monkeypatch) -> None:
    service = SimulationService(MemoryRepository())
    runtime = _controlled_runtime()
    runtime.metrics = {
        "conveyorUtilization": 0.0,
        "conveyorQueueDepth": 0,
        "stationQueueDepth": 0,
        "bufferOccupancy": 0,
    }

    class Socket:
        def __init__(self) -> None:
            self.messages: list[dict[str, Any]] = []

        async def send_json(self, message: dict[str, Any]) -> None:
            self.messages.append(deepcopy(message))

    socket = Socket()
    service.hub.connections[runtime.run_id] = [socket]  # type: ignore[list-item]
    monkeypatch.setattr(service, "_record_event", lambda *_args, **_kwargs: None)

    async def scenario() -> None:
        runtime.state["tick"] = 1
        await service._broadcast_tick(runtime)
        runtime.state["tick"] = 2
        await service._broadcast_tick(runtime)

    asyncio.run(scenario())

    ticks = [message["payload"]["tick"] for message in socket.messages if message["type"] == "tick"]
    assert ticks == [1, 2]


def test_websocket_send_failure_closes_connection_for_http_fallback() -> None:
    service = SimulationService(MemoryRepository())

    class BrokenSocket:
        def __init__(self) -> None:
            self.closed = False

        async def send_json(self, _message: dict[str, Any]) -> None:
            raise ConnectionError("client disappeared")

        async def close(self, *, code: int, reason: str) -> None:
            self.closed = code == 1011 and bool(reason)

    socket = BrokenSocket()
    service.hub.connections["run-broken"] = [socket]  # type: ignore[list-item]

    asyncio.run(service.hub.broadcast("run-broken", {"type": "tick", "tick": 2, "payload": {}}))

    assert socket.closed is True
    assert service.hub.connections["run-broken"] == []


@pytest.mark.parametrize(
    ("tasks", "orders"),
    [
        ([{"id": "order-task", "status": "completed"}], [{"id": "order", "status": "completed"}]),
        ([{"id": "transfer-task", "status": "completed"}], []),
        (
            [
                {"id": "order-task", "status": "completed"},
                {"id": "transfer-task", "status": "completed"},
            ],
            [{"id": "order", "status": "completed"}],
        ),
    ],
    ids=["orders-only", "transfer-only", "combined"],
)
def test_run_completion_requires_every_task_family_to_be_terminal(tasks, orders) -> None:
    service = SimulationService(MemoryRepository())
    runtime = _controlled_runtime()
    runtime.state["tasks"] = tasks
    runtime.state["orders"] = orders

    assert service._all_tasks_completed(runtime) is True


def test_partial_completion_does_not_finish_combined_run() -> None:
    service = SimulationService(MemoryRepository())
    runtime = _controlled_runtime()
    runtime.state["tasks"] = [
        {"id": "transfer-task", "status": "completed"},
        {"id": "order-task", "status": "assigned"},
    ]
    runtime.state["orders"] = [{"id": "order", "status": "executing"}]

    assert service._all_tasks_completed(runtime) is False


def test_empty_queue_completes_without_creating_a_tick() -> None:
    repository = MemoryRepository()
    service = SimulationService(repository)
    runtime = _controlled_runtime()
    runtime.state["tasks"] = []
    runtime.state["orders"] = []

    asyncio.run(service._loop(runtime))

    assert runtime.state["status"] == "completed"
    assert runtime.state["tick"] == 0


def test_terminal_run_does_not_advance_after_completion() -> None:
    service = SimulationService(MemoryRepository())
    runtime = _controlled_runtime()
    runtime.state["tasks"] = [{"id": "done", "status": "completed"}]
    runtime.state["orders"] = []
    runtime.state["tick"] = 17

    asyncio.run(service._loop(runtime))
    asyncio.run(service._loop(runtime))

    assert runtime.state["status"] == "completed"
    assert runtime.state["tick"] == 17


@pytest.mark.parametrize(
    ("multiplier", "expected_delay"),
    [(1, TICK_INTERVAL_SECONDS), (2, TICK_INTERVAL_SECONDS / 2), (4, TICK_INTERVAL_SECONDS / 4)],
)
def test_unified_speed_changes_only_the_authoritative_tick_delay(multiplier: int, expected_delay: float) -> None:
    service = SimulationService(MemoryRepository())
    runtime = _controlled_runtime()
    runtime.speed_multiplier = multiplier
    before_tasks = deepcopy(runtime.state["tasks"])

    assert service._tick_delay(runtime) == pytest.approx(expected_delay)
    assert runtime.state["tasks"] == before_tasks


def test_reset_restores_default_speed(monkeypatch) -> None:
    service = SimulationService(MemoryRepository())
    runtime = _controlled_runtime()
    runtime.state["speedMultiplier"] = 4
    runtime.speed_multiplier = 4
    service.runtimes[runtime.run_id] = runtime

    async def scenario() -> None:
        await service.reset(runtime.run_id)
        assert runtime.speed_multiplier == DEFAULT_SPEED_MULTIPLIER
        assert runtime.state["speedMultiplier"] == DEFAULT_SPEED_MULTIPLIER
        assert runtime.state["tickIntervalSeconds"] == pytest.approx(
            TICK_INTERVAL_SECONDS / DEFAULT_SPEED_MULTIPLIER
        )
        assert runtime.task_handle is not None
        runtime.task_handle.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runtime.task_handle

    asyncio.run(scenario())


def test_unreachable_pending_task_becomes_explicit_failure() -> None:
    topology = _topology()
    service = SimulationService(MemoryRepository())
    runtime = service._create_runtime(
        "run-unreachable",
        topology,
        build_simulation_config(topology),
        [_order(topology)],
        [],
    )
    task = runtime.state["tasks"][0]
    task["sourceCellId"] = "cell-r999-c999"

    service._assign_tasks(runtime)

    assert task["status"] == "failed"
    assert task["failureReason"] == "source_unreachable"
    assert runtime.state["alerts"][-1]["details"]["taskId"] == task["id"]
