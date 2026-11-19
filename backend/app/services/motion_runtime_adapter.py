"""Runtime observation adapter for the pure P3-1 motion-distance contract.

The observer is deliberately passive: it accepts positions only after the
simulation has committed them.  It never changes a robot, path, task, or tick.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping

from .motion_distance import (
    DataQuality,
    MovementInput,
    MovementSegment,
    MovementType,
    PhysicsContract,
    Position,
    calculate_movement_segment,
)


DIAGNOSTICS_NAMESPACE = "motionDistanceDiagnosticsV1"
EVENT_TYPE = "robot_movement_batch_v1"
RUNTIME_CONTRACT_VERSION = "motion-runtime-v1"
PHYSICS_CONFIG_KEY = "motionPhysicsContract"
MAX_BATCH_SIZE = 256


def physics_contract_from_config(config: Mapping[str, Any]) -> PhysicsContract | None:
    """Build one frozen contract from the optional internal Run config entry."""

    value = config.get(PHYSICS_CONFIG_KEY)
    if value is None:
        return None
    if isinstance(value, PhysicsContract):
        return value
    if not isinstance(value, Mapping):
        raise ValueError(f"{PHYSICS_CONFIG_KEY} must be an object")
    return PhysicsContract(
        physics_contract_version=value.get("physicsContractVersion", value.get("physics_contract_version")),
        meters_per_cell=value.get("metersPerCell", value.get("meters_per_cell")),
        shuttle_level_height_meters=value.get(
            "shuttleLevelHeightMeters",
            value.get("shuttle_level_height_meters"),
        ),
        floor_height_meters=value.get("floorHeightMeters", value.get("floor_height_meters")),
        coordinate_contract_version=value.get(
            "coordinateContractVersion",
            value.get("coordinate_contract_version"),
        ),
    )


@dataclass(frozen=True)
class RuntimePosition:
    row: float
    col: float
    floor: str | None
    layer: float | None

    @classmethod
    def from_robot(cls, robot: Mapping[str, Any]) -> RuntimePosition:
        row = _finite_number("currentRow", robot.get("currentRow"))
        col = _finite_number("currentCol", robot.get("currentCol"))
        layer_value = robot.get("currentLayer")
        layer = None if layer_value is None else _finite_number("currentLayer", layer_value)
        floor_value = robot.get("currentFloor")
        floor = str(floor_value) if floor_value not in (None, "") else None
        return cls(row=row, col=col, floor=floor, layer=layer)

    def to_contract_position(self) -> Position:
        layer = _integral_layer(self.layer)
        return Position(
            row=self.row,
            col=self.col,
            floor=self.floor,
            layer=layer,
            floor_index=_floor_index(self.floor),
        )

    def to_dict(self) -> dict[str, object | None]:
        return {
            "row": self.row,
            "col": self.col,
            "floor": self.floor,
            "layer": self.layer,
            "floorIndex": _floor_index(self.floor),
        }


@dataclass(frozen=True)
class RuntimeMovementEvent:
    segment: MovementSegment
    generation: int
    event_sequence: int
    source: str
    observed_from_position: RuntimePosition
    observed_to_position: RuntimePosition

    @property
    def movement_id(self) -> str:
        return self.segment.movement_id

    @property
    def tick(self) -> int:
        return self.segment.to_tick

    def to_payload(self) -> dict[str, object]:
        payload = self.segment.to_dict()
        payload.update(
            {
                "contractVersion": RUNTIME_CONTRACT_VERSION,
                "generation": self.generation,
                "eventSequence": self.event_sequence,
                "source": self.source,
                "observedFromPosition": self.observed_from_position.to_dict(),
                "observedToPosition": self.observed_to_position.to_dict(),
            }
        )
        return payload


@dataclass(frozen=True)
class CaptureOutcome:
    event: RuntimeMovementEvent | None
    should_persist: bool = False
    duplicate: bool = False
    conflict: bool = False
    pending_lift: bool = False


@dataclass(frozen=True)
class _LiftAnchor:
    position: RuntimePosition
    from_tick: int
    source: str
    planned_path_id: str | None
    replan_id: str | None


def _finite_number(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _integral_layer(value: float | None) -> int | None:
    if value is None:
        return None
    rounded = round(value)
    if abs(value - rounded) > 1e-9:
        raise ValueError("fractional shuttle layer is playback interpolation, not a track level")
    return int(rounded)


def _floor_index(floor: str | None) -> int | None:
    if floor is None:
        return None
    normalized = floor.strip().upper()
    if normalized.endswith("F") and normalized[:-1].isdigit():
        return int(normalized[:-1])
    return None


def _same_position(left: RuntimePosition, right: RuntimePosition) -> bool:
    return left == right


def _new_type_summary(physics_available: bool) -> dict[str, dict[str, object]]:
    return {
        movement_type.value: {
            "movementCount": 0,
            "horizontalMeters": 0.0 if physics_available else None,
            "verticalMeters": 0.0 if physics_available else None,
        }
        for movement_type in MovementType
    }


class MotionRuntimeObserver:
    """Bounded in-memory summary plus per-move persistent event producer."""

    def __init__(
        self,
        run_id: str,
        generation: int,
        physics_contract: PhysicsContract | None,
        *,
        initial_tick: int = 0,
        recovered_diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("run_id is required")
        if generation < 1:
            raise ValueError("generation must be at least 1")
        self.run_id = run_id
        self.generation = generation
        self.run_instance_id = f"{run_id}:generation-{generation}"
        self.physics_contract = physics_contract
        self._last_processed_tick = max(0, int(initial_tick))
        self._total_horizontal_meters = 0.0 if physics_contract else None
        self._total_vertical_meters = 0.0 if physics_contract else None
        self._total_movement_count = 0
        self._per_robot: dict[str, dict[str, Any]] = {}
        self._movement_types = _new_type_summary(physics_contract is not None)
        self._duplicate_count = 0
        self._conflict_count = 0
        self._incomplete_count = 0
        self._persistence_failure_count = 0
        self._observation_incomplete = initial_tick > 0
        self._historical_recovery_incomplete = initial_tick > 0 or recovered_diagnostics is not None
        self._watermark: dict[str, object] | None = None
        self._ended = False
        self._end_reason: str | None = None
        self._tick_for_sequence: int | None = None
        self._next_sequence = 1
        self._commit_key_to_id: dict[tuple[object, ...], str] = {}
        self._events_by_id: dict[str, RuntimeMovementEvent] = {}
        self._pending_events: dict[str, RuntimeMovementEvent] = {}
        self._active_lifts: dict[str, _LiftAnchor] = {}
        if recovered_diagnostics:
            self._recover_summary(recovered_diagnostics)

    @property
    def ended(self) -> bool:
        return self._ended

    def register_robots(self, robots: list[Mapping[str, Any]]) -> None:
        for robot in robots:
            robot_id = str(robot.get("id") or "")
            if robot_id:
                self._ensure_robot(robot_id, str(robot.get("type") or "unknown"))

    def begin_tick(self, tick: int) -> None:
        if self._ended:
            return
        tick = int(tick)
        self._last_processed_tick = max(self._last_processed_tick, tick)
        self._begin_tick(tick)

    def new_generation(self) -> MotionRuntimeObserver:
        return MotionRuntimeObserver(
            self.run_id,
            self.generation + 1,
            self.physics_contract,
            initial_tick=0,
        )

    def capture_committed_move(
        self,
        *,
        robot_id: str,
        robot_type: str,
        from_position: RuntimePosition,
        to_position: RuntimePosition,
        tick: int,
        movement_type: MovementType,
        source: str,
        planned_path_id: str | None = None,
        replan_id: str | None = None,
        event_sequence: int | None = None,
    ) -> CaptureOutcome:
        """Capture one already committed logical move; never mutate the source state."""

        tick = int(tick)
        self._last_processed_tick = max(self._last_processed_tick, tick)
        if self._ended or _same_position(from_position, to_position):
            return CaptureOutcome(None)
        if movement_type is MovementType.LIFT:
            lift_outcome = self._capture_lift(
                robot_id=robot_id,
                robot_type=robot_type,
                from_position=from_position,
                to_position=to_position,
                tick=tick,
                source=source,
                planned_path_id=planned_path_id,
                replan_id=replan_id,
                event_sequence=event_sequence,
            )
            if lift_outcome is not None:
                return lift_outcome
        elif robot_id in self._active_lifts:
            self._mark_incomplete(robot_id, "lift interpolation ended without an integral target level")
            del self._active_lifts[robot_id]

        return self._capture_segment(
            robot_id=robot_id,
            robot_type=robot_type,
            from_position=from_position,
            to_position=to_position,
            from_tick=max(0, tick - 1),
            to_tick=tick,
            movement_type=movement_type,
            source=source,
            planned_path_id=planned_path_id,
            replan_id=replan_id,
            event_sequence=event_sequence,
        )

    def acknowledge_persistence(self, event: RuntimeMovementEvent) -> None:
        self._watermark = {
            "movementId": event.movement_id,
            "tick": event.tick,
            "eventSequence": event.event_sequence,
        }
        self._pending_events.pop(event.movement_id, None)

    def record_persistence_failure(self, event: RuntimeMovementEvent, error: BaseException) -> None:
        self._persistence_failure_count += 1
        self._observation_incomplete = True
        self._incomplete_count += 1
        robot = self._ensure_robot(event.segment.robot_id, event.segment.robot_type)
        robot["incompleteCount"] += 1
        robot["lastIssue"] = f"EVENT_PERSISTENCE_FAILED:{type(error).__name__}"
        self._pending_events.pop(event.movement_id, None)

    def pending_events(self) -> tuple[RuntimeMovementEvent, ...]:
        return tuple(
            sorted(
                self._pending_events.values(),
                key=lambda event: (event.tick, event.event_sequence, event.movement_id),
            )
        )

    def batch_payload(self, events: tuple[RuntimeMovementEvent, ...]) -> dict[str, object]:
        if not events or len(events) > MAX_BATCH_SIZE:
            raise ValueError(f"movement batch size must be between 1 and {MAX_BATCH_SIZE}")
        ticks = {event.tick for event in events}
        if len(ticks) != 1:
            raise ValueError("one movement batch cannot mix ticks")
        return {
            "contractVersion": RUNTIME_CONTRACT_VERSION,
            "runId": self.run_id,
            "runInstanceId": self.run_instance_id,
            "generation": self.generation,
            "tick": events[0].tick,
            "movementCount": len(events),
            "movements": [event.to_payload() for event in events],
        }

    def record_observation_failure(
        self,
        tick: int,
        robot_id: str,
        robot_type: str,
        error: BaseException,
    ) -> None:
        self._last_processed_tick = max(self._last_processed_tick, int(tick))
        self._observation_incomplete = True
        self._incomplete_count += 1
        self._mark_robot_issue(
            robot_id,
            robot_type,
            f"MOVEMENT_OBSERVATION_FAILED:{type(error).__name__}",
        )

    def finish(self, tick: int, reason: str) -> None:
        if self._ended:
            return
        self._last_processed_tick = max(self._last_processed_tick, int(tick))
        for robot_id in tuple(self._active_lifts):
            self._mark_incomplete(robot_id, "run ended during a lift interpolation")
        self._active_lifts.clear()
        self._ended = True
        self._end_reason = str(reason)

    def diagnostics_payload(self) -> dict[str, object]:
        per_robot = {
            robot_id: self._robot_payload(summary)
            for robot_id, summary in sorted(self._per_robot.items())
        }
        incomplete_robots = sum(1 for summary in self._per_robot.values() if summary["incompleteCount"] > 0)
        complete_robots = len(self._per_robot) - incomplete_robots
        if self._observation_incomplete:
            quality = DataQuality.INCOMPLETE.value
        elif self.physics_contract is None:
            quality = DataQuality.UNAVAILABLE.value
        else:
            quality = DataQuality.COMPLETE.value
        return {
            "contractVersion": RUNTIME_CONTRACT_VERSION,
            "runId": self.run_id,
            "runInstanceId": self.run_instance_id,
            "generation": self.generation,
            "physicsContractVersion": (
                self.physics_contract.physics_contract_version if self.physics_contract else None
            ),
            "physicsContract": self.physics_contract.to_dict() if self.physics_contract else None,
            "metersPerCell": self.physics_contract.meters_per_cell if self.physics_contract else None,
            "lastProcessedTick": self._last_processed_tick,
            "totalHorizontalMeters": self._total_horizontal_meters,
            "totalVerticalMeters": self._total_vertical_meters,
            "totalMovementCount": self._total_movement_count,
            "robotCount": len(self._per_robot),
            "completeTrajectoryRobotCount": complete_robots,
            "incompleteTrajectoryRobotCount": incomplete_robots,
            "perRobotSummary": per_robot,
            "movementTypeSummary": deepcopy(self._movement_types),
            "duplicateCount": self._duplicate_count,
            "conflictCount": self._conflict_count,
            "incompleteCount": self._incomplete_count,
            "persistenceFailureCount": self._persistence_failure_count,
            "observationCompleteness": "INCOMPLETE" if self._observation_incomplete else "COMPLETE",
            "dataQuality": quality,
            "eventPersistenceWatermark": deepcopy(self._watermark),
            "pendingPersistenceCount": len(self._pending_events),
            "plannedHorizontalMeters": None,
            "extraDistanceMeters": None,
            "attributableReplanExtraMeters": None,
            "activeLiftCount": len(self._active_lifts),
            "historicalRecoveryIncomplete": self._historical_recovery_incomplete,
            "ended": self._ended,
            "endReason": self._end_reason,
        }

    def _capture_lift(
        self,
        *,
        robot_id: str,
        robot_type: str,
        from_position: RuntimePosition,
        to_position: RuntimePosition,
        tick: int,
        source: str,
        planned_path_id: str | None,
        replan_id: str | None,
        event_sequence: int | None,
    ) -> CaptureOutcome | None:
        from_integral = _is_integral(from_position.layer)
        to_integral = _is_integral(to_position.layer)
        anchor = self._active_lifts.get(robot_id)
        if anchor is None and from_integral:
            anchor = _LiftAnchor(
                position=from_position,
                from_tick=max(0, tick - 1),
                source=source,
                planned_path_id=planned_path_id,
                replan_id=replan_id,
            )
            self._active_lifts[robot_id] = anchor
        if not to_integral:
            if anchor is None:
                self._mark_incomplete(robot_id, "lift observation began at a fractional layer")
            return CaptureOutcome(None, pending_lift=True)
        if anchor is None:
            self._mark_incomplete(robot_id, "lift observation ended without its integral start level")
            return CaptureOutcome(None, pending_lift=True)
        del self._active_lifts[robot_id]
        if _same_position(anchor.position, to_position):
            return CaptureOutcome(None)
        return self._capture_segment(
            robot_id=robot_id,
            robot_type=robot_type,
            from_position=anchor.position,
            to_position=to_position,
            from_tick=anchor.from_tick,
            to_tick=tick,
            movement_type=MovementType.LIFT,
            source=anchor.source,
            planned_path_id=anchor.planned_path_id,
            replan_id=anchor.replan_id,
            event_sequence=event_sequence,
        )

    def _capture_segment(
        self,
        *,
        robot_id: str,
        robot_type: str,
        from_position: RuntimePosition,
        to_position: RuntimePosition,
        from_tick: int,
        to_tick: int,
        movement_type: MovementType,
        source: str,
        planned_path_id: str | None,
        replan_id: str | None,
        event_sequence: int | None,
    ) -> CaptureOutcome:
        self._begin_tick(to_tick)
        commit_key = (
            robot_id,
            robot_type,
            from_position,
            to_position,
            movement_type.value,
            source,
            planned_path_id,
            replan_id,
        )
        existing_id = self._commit_key_to_id.get(commit_key)
        if existing_id is not None:
            self._duplicate_count += 1
            return CaptureOutcome(self._events_by_id[existing_id], duplicate=True)

        sequence = self._allocate_sequence(event_sequence)
        movement_id = (
            f"{self.run_instance_id}:g{self.generation}:t{to_tick}:"
            f"r{robot_id}:s{sequence}"
        )
        movement = MovementInput(
            movement_id=movement_id,
            robot_id=robot_id,
            robot_type=robot_type,
            run_id=self.run_id,
            run_instance_id=self.run_instance_id,
            from_position=from_position.to_contract_position(),
            to_position=to_position.to_contract_position(),
            from_tick=from_tick,
            to_tick=to_tick,
            movement_type=movement_type,
            planned_path_id=planned_path_id,
            replan_id=replan_id,
            physics_contract_version=(
                self.physics_contract.physics_contract_version if self.physics_contract else None
            ),
            horizontal_edge_verified=True,
            diagonal_allowed=False,
        )
        try:
            segment = calculate_movement_segment(movement, self.physics_contract)
        except ValueError as error:
            segment = MovementSegment(
                movement_id=movement.movement_id,
                robot_id=movement.robot_id,
                robot_type=movement.robot_type,
                run_id=movement.run_id,
                run_instance_id=movement.run_instance_id,
                from_position=movement.from_position,
                to_position=movement.to_position,
                from_tick=movement.from_tick,
                to_tick=movement.to_tick,
                movement_type=movement.movement_type,
                segment_horizontal_meters=None,
                segment_vertical_meters=None,
                planned_path_id=movement.planned_path_id,
                replan_id=movement.replan_id,
                physics_contract_version=movement.physics_contract_version,
                data_quality=DataQuality.INVALID,
                issue=str(error),
            )
        event = RuntimeMovementEvent(
            segment=segment,
            generation=self.generation,
            event_sequence=sequence,
            source=source,
            observed_from_position=from_position,
            observed_to_position=to_position,
        )
        previous = self._events_by_id.get(movement_id)
        if previous is not None:
            if previous.to_payload() == event.to_payload():
                self._duplicate_count += 1
                return CaptureOutcome(previous, duplicate=True)
            self._conflict_count += 1
            self._observation_incomplete = True
            self._incomplete_count += 1
            self._mark_robot_issue(robot_id, robot_type, "MOVEMENT_ID_CONFLICT")
            return CaptureOutcome(previous, conflict=True)

        self._commit_key_to_id[commit_key] = movement_id
        self._events_by_id[movement_id] = event
        self._pending_events[movement_id] = event
        self._accumulate(event)
        return CaptureOutcome(event, should_persist=True)

    def _begin_tick(self, tick: int) -> None:
        if self._tick_for_sequence == tick:
            return
        self._tick_for_sequence = tick
        self._next_sequence = 1
        self._commit_key_to_id = {}
        self._events_by_id = {}

    def _allocate_sequence(self, requested: int | None) -> int:
        if requested is not None:
            if requested < 1:
                raise ValueError("event_sequence must be positive")
            self._next_sequence = max(self._next_sequence, requested + 1)
            return requested
        sequence = self._next_sequence
        self._next_sequence += 1
        return sequence

    def _accumulate(self, event: RuntimeMovementEvent) -> None:
        segment = event.segment
        self._total_movement_count += 1
        robot = self._ensure_robot(segment.robot_id, segment.robot_type)
        robot["totalMovementCount"] += 1
        movement_summary = self._movement_types[segment.movement_type.value]
        robot_movement_summary = robot["movementTypeSummary"][segment.movement_type.value]
        movement_summary["movementCount"] += 1
        robot_movement_summary["movementCount"] += 1
        if segment.data_quality is DataQuality.COMPLETE:
            assert segment.segment_horizontal_meters is not None
            assert segment.segment_vertical_meters is not None
            self._total_horizontal_meters += segment.segment_horizontal_meters  # type: ignore[operator]
            self._total_vertical_meters += segment.segment_vertical_meters  # type: ignore[operator]
            robot["totalHorizontalMeters"] += segment.segment_horizontal_meters
            robot["totalVerticalMeters"] += segment.segment_vertical_meters
            movement_summary["horizontalMeters"] += segment.segment_horizontal_meters
            movement_summary["verticalMeters"] += segment.segment_vertical_meters
            robot_movement_summary["horizontalMeters"] += segment.segment_horizontal_meters
            robot_movement_summary["verticalMeters"] += segment.segment_vertical_meters
        elif segment.data_quality is not DataQuality.UNAVAILABLE:
            self._mark_incomplete(segment.robot_id, segment.issue or segment.data_quality.value)

    def _ensure_robot(self, robot_id: str, robot_type: str) -> dict[str, Any]:
        summary = self._per_robot.get(robot_id)
        if summary is None:
            summary = {
                "robotId": robot_id,
                "robotType": robot_type,
                "totalHorizontalMeters": 0.0 if self.physics_contract else None,
                "totalVerticalMeters": 0.0 if self.physics_contract else None,
                "totalMovementCount": 0,
                "movementTypeSummary": _new_type_summary(self.physics_contract is not None),
                "incompleteCount": 0,
                "lastIssue": None,
            }
            self._per_robot[robot_id] = summary
        return summary

    def _mark_incomplete(self, robot_id: str, issue: str) -> None:
        self._observation_incomplete = True
        self._incomplete_count += 1
        self._mark_robot_issue(robot_id, "unknown", issue)

    def _mark_robot_issue(self, robot_id: str, robot_type: str, issue: str) -> None:
        summary = self._ensure_robot(robot_id, robot_type)
        summary["incompleteCount"] += 1
        summary["lastIssue"] = issue

    def _robot_payload(self, summary: Mapping[str, Any]) -> dict[str, object]:
        result = deepcopy(dict(summary))
        if int(summary["incompleteCount"]) > 0:
            result["observationCompleteness"] = "INCOMPLETE"
            result["dataQuality"] = DataQuality.INCOMPLETE.value
        elif self.physics_contract is None:
            result["observationCompleteness"] = "COMPLETE"
            result["dataQuality"] = DataQuality.UNAVAILABLE.value
        else:
            result["observationCompleteness"] = "COMPLETE"
            result["dataQuality"] = DataQuality.COMPLETE.value
        return result

    def _recover_summary(self, saved: Mapping[str, Any]) -> None:
        if str(saved.get("runInstanceId") or "") != self.run_instance_id:
            self._observation_incomplete = True
            return
        self._total_horizontal_meters = saved.get("totalHorizontalMeters")
        self._total_vertical_meters = saved.get("totalVerticalMeters")
        self._total_movement_count = int(saved.get("totalMovementCount") or 0)
        self._duplicate_count = int(saved.get("duplicateCount") or 0)
        self._conflict_count = int(saved.get("conflictCount") or 0)
        self._incomplete_count = int(saved.get("incompleteCount") or 0) + 1
        self._persistence_failure_count = int(saved.get("persistenceFailureCount") or 0)
        self._watermark = deepcopy(saved.get("eventPersistenceWatermark"))
        per_robot = saved.get("perRobotSummary")
        if isinstance(per_robot, Mapping):
            self._per_robot = {str(key): deepcopy(dict(value)) for key, value in per_robot.items() if isinstance(value, Mapping)}
        movement_types = saved.get("movementTypeSummary")
        if isinstance(movement_types, Mapping):
            self._movement_types = deepcopy(dict(movement_types))
        self._observation_incomplete = True


def _is_integral(value: float | None) -> bool:
    if value is None:
        return False
    return abs(value - round(value)) <= 1e-9


__all__ = [
    "CaptureOutcome",
    "DIAGNOSTICS_NAMESPACE",
    "EVENT_TYPE",
    "MAX_BATCH_SIZE",
    "MotionRuntimeObserver",
    "PHYSICS_CONFIG_KEY",
    "RUNTIME_CONTRACT_VERSION",
    "RuntimeMovementEvent",
    "RuntimePosition",
    "physics_contract_from_config",
]
