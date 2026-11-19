"""P3-3B start-contract tests without importing the application runtime singleton."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from typing import Any
import uuid

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
TOPOLOGY_PATH = ROOT / "data" / "topology_666.json"
DEFAULT_DB = ROOT / "backend" / "app_data" / "agv_platform.db"
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

_ISOLATED_DB = Path(tempfile.gettempdir()) / f"agv-p3-3b-{uuid.uuid4().hex}" / "contract-tests.db"

from app.core import config as app_config  # noqa: E402
from app.core import db as app_db  # noqa: E402

app_config.DB_PATH = _ISOLATED_DB
app_db.DB_PATH = _ISOLATED_DB
assert app_config.DB_PATH.resolve() == app_db.DB_PATH.resolve()
assert app_config.DB_PATH.resolve() != DEFAULT_DB.resolve()

from app.schemas import SimulationStartRequest  # noqa: E402
from app.services.motion_distance import MovementType, PhysicsContract  # noqa: E402
from app.services.motion_runtime_adapter import MotionRuntimeObserver, RuntimePosition  # noqa: E402
from app.services.simulation import SimulationService  # noqa: E402


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


DEFAULT_DB_SHA = _sha(DEFAULT_DB)


def _contract(meters: float = 0.8) -> dict[str, Any]:
    return {
        "physicsContractVersion": "motion-physics-v1",
        "coordinateContractVersion": "topology-grid-v1",
        "metersPerCell": meters,
        "shuttleLevelHeightMeters": 3.0,
        "floorHeightMeters": 4.0,
    }


class MemoryRepository:
    def __init__(self) -> None:
        self.events: list[tuple[str, int, str, dict[str, Any]]] = []
        self.runs: dict[str, dict[str, Any]] = {}

    def add_event(self, run_id: str, tick: int, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append((run_id, tick, event_type, deepcopy(payload)))

    def create_run(self, run_id: str, status: str, topology_id: str, payload: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
        self.runs[run_id] = {"id": run_id, "status": status, "topology_id": topology_id, "payload": deepcopy(payload), "metrics": deepcopy(metrics)}
        return self.runs[run_id]

    def update_run(self, run_id: str, status: str, payload: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
        return self.create_run(run_id, status, payload["topologyId"], payload, metrics)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.runs.get(run_id)

    def get_topology(self, _topology_id: str) -> None:
        return None


def test_legacy_start_request_remains_compatible() -> None:
    request = SimulationStartRequest.model_validate({"topologyId": "topology-test"})
    assert request.motionPhysicsContract is None
    assert request.orders == []
    assert request.transfers == []


@pytest.mark.parametrize("meters", [0.8, 1.2])
def test_valid_metric_contract_is_normalized(meters: float) -> None:
    request = SimulationStartRequest.model_validate({"topologyId": "topology-test", "motionPhysicsContract": _contract(meters)})
    assert request.motionPhysicsContract is not None
    assert request.motionPhysicsContract.metersPerCell == meters
    assert request.motionPhysicsContract.source == "USER_DEFINED_SIMULATION"
    assert request.motionPhysicsContract.calibrationStatus == "UNVERIFIED"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("metersPerCell", 0),
        ("metersPerCell", -0.1),
        ("metersPerCell", True),
        ("metersPerCell", "0.8"),
        ("metersPerCell", float("nan")),
        ("metersPerCell", float("inf")),
        ("shuttleLevelHeightMeters", 0),
        ("shuttleLevelHeightMeters", False),
        ("shuttleLevelHeightMeters", "3.0"),
        ("shuttleLevelHeightMeters", float("-inf")),
        ("floorHeightMeters", 0),
        ("floorHeightMeters", -4),
        ("floorHeightMeters", True),
        ("floorHeightMeters", "4.0"),
        ("floorHeightMeters", float("nan")),
    ],
)
def test_invalid_physical_numbers_are_rejected(field: str, value: object) -> None:
    contract = _contract()
    contract[field] = value
    with pytest.raises(ValidationError):
        SimulationStartRequest.model_validate({"topologyId": "topology-test", "motionPhysicsContract": contract})


@pytest.mark.parametrize(
    "field",
    [
        "physicsContractVersion",
        "coordinateContractVersion",
        "metersPerCell",
        "shuttleLevelHeightMeters",
        "floorHeightMeters",
    ],
)
def test_missing_contract_field_is_rejected(field: str) -> None:
    contract = _contract()
    contract.pop(field)
    with pytest.raises(ValidationError):
        SimulationStartRequest.model_validate({"topologyId": "topology-test", "motionPhysicsContract": contract})


def test_unknown_contract_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SimulationStartRequest.model_validate({
            "topologyId": "topology-test",
            "motionPhysicsContract": {**_contract(), "metersPerPixel": 1},
        })


def test_start_freezes_contract_and_reset_starts_a_new_generation() -> None:
    topology_bytes = TOPOLOGY_PATH.read_bytes()
    topology = json.loads(topology_bytes)
    repository = MemoryRepository()
    service = SimulationService(repository)  # type: ignore[arg-type]

    async def idle_loop(_runtime: object) -> None:
        await asyncio.Event().wait()

    service._loop = idle_loop  # type: ignore[method-assign]

    async def scenario() -> None:
        result = await service.start(topology, [], [], _contract(0.8))
        runtime = service.runtimes[result["runId"]]
        frozen = runtime.state["motionPhysicsContract"]
        assert frozen == {
            **_contract(0.8),
            "source": "USER_DEFINED_SIMULATION",
            "calibrationStatus": "UNVERIFIED",
        }
        assert runtime.config["motionPhysicsContract"] == frozen
        assert runtime.motion_observer is not None
        assert runtime.motion_observer.generation == 1

        reset_result = await service.reset(runtime.run_id)
        assert reset_result["status"] == "paused"
        assert runtime.state["motionPhysicsContract"] == frozen
        assert runtime.motion_observer is not None
        assert runtime.motion_observer.generation == 2
        assert runtime.state["motionDistanceDiagnosticsV1"]["totalMovementCount"] == 0

        assert runtime.task_handle is not None
        runtime.task_handle.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runtime.task_handle

    asyncio.run(scenario())
    assert TOPOLOGY_PATH.read_bytes() == topology_bytes
    assert _sha(DEFAULT_DB) == DEFAULT_DB_SHA


def test_runtime_without_contract_does_not_invent_metric_values() -> None:
    topology = json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))
    service = SimulationService(MemoryRepository())  # type: ignore[arg-type]
    from app.services.simulation_config import build_simulation_config

    runtime = service._create_runtime("run-legacy", topology, build_simulation_config(topology), [], [])
    assert runtime.state["motionPhysicsContract"] is None
    assert runtime.state["motionDistanceDiagnosticsV1"]["physicsContract"] is None
    assert runtime.state["motionDistanceDiagnosticsV1"]["metersPerCell"] is None
    assert _sha(DEFAULT_DB) == DEFAULT_DB_SHA


def test_example_contract_produces_ctu_shuttle_and_lift_distances() -> None:
    contract = PhysicsContract(
        physics_contract_version="motion-physics-v1",
        coordinate_contract_version="topology-grid-v1",
        meters_per_cell=0.8,
        shuttle_level_height_meters=3.0,
        floor_height_meters=4.0,
    )
    observer = MotionRuntimeObserver("run-example", 1, contract)
    ctu = observer.capture_committed_move(
        robot_id="ctu-1",
        robot_type="ctu",
        from_position=RuntimePosition(1, 1, "1F", 1),
        to_position=RuntimePosition(1, 2, "1F", 1),
        tick=1,
        movement_type=MovementType.NORMAL,
        source="test",
    )
    shuttle = observer.capture_committed_move(
        robot_id="shuttle-1",
        robot_type="shuttle",
        from_position=RuntimePosition(10, 10, "1F", 1),
        to_position=RuntimePosition(10, 12, "1F", 1),
        tick=2,
        movement_type=MovementType.BACKOFF,
        source="test",
    )
    lift = observer.capture_committed_move(
        robot_id="shuttle-1",
        robot_type="shuttle",
        from_position=RuntimePosition(10, 12, "1F", 1),
        to_position=RuntimePosition(10, 12, "1F", 2),
        tick=3,
        movement_type=MovementType.LIFT,
        source="test",
    )
    assert ctu.event is not None and ctu.event.segment.segment_horizontal_meters == pytest.approx(0.8)
    assert shuttle.event is not None and shuttle.event.segment.segment_horizontal_meters == pytest.approx(1.6)
    assert shuttle.event.segment.movement_type is MovementType.BACKOFF
    assert lift.event is not None and lift.event.segment.segment_vertical_meters == pytest.approx(3.0)
