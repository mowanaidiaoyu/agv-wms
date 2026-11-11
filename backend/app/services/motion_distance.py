"""Pure physical-distance contracts for future AGV movement telemetry.

This module intentionally has no application, runtime, database, or framework
dependencies.  It does not read configuration: every physical parameter must be
supplied by the caller as a frozen :class:`PhysicsContract`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import hypot, isfinite
from typing import Iterable, Mapping, Sequence


class MovementType(str, Enum):
    NORMAL = "NORMAL"
    YIELD = "YIELD"
    BACKOFF = "BACKOFF"
    RETURN_TO_STANDBY = "RETURN_TO_STANDBY"
    LIFT = "LIFT"
    UNKNOWN = "UNKNOWN"


class DataQuality(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"


class DistanceSource(str, Enum):
    PLANNED_PATH_EDGES = "PLANNED_PATH_EDGES"
    ACTUAL_MOVEMENT_SEGMENTS = "ACTUAL_MOVEMENT_SEGMENTS"
    PLANNED_AND_ACTUAL = "PLANNED_AND_ACTUAL"


def _require_identifier(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_positive_finite(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite positive number")
    result = float(value)
    if not isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return result


def _optional_finite(name: str, value: object | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number when provided")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{name} must be a finite number when provided")
    return result


@dataclass(frozen=True)
class PhysicsContract:
    physics_contract_version: str
    meters_per_cell: float
    shuttle_level_height_meters: float
    floor_height_meters: float
    coordinate_contract_version: str

    def __post_init__(self) -> None:
        _require_identifier("physics_contract_version", self.physics_contract_version)
        _require_identifier("coordinate_contract_version", self.coordinate_contract_version)
        object.__setattr__(self, "meters_per_cell", _require_positive_finite("meters_per_cell", self.meters_per_cell))
        object.__setattr__(
            self,
            "shuttle_level_height_meters",
            _require_positive_finite("shuttle_level_height_meters", self.shuttle_level_height_meters),
        )
        object.__setattr__(self, "floor_height_meters", _require_positive_finite("floor_height_meters", self.floor_height_meters))

    def to_dict(self) -> dict[str, object]:
        return {
            "physicsContractVersion": self.physics_contract_version,
            "metersPerCell": self.meters_per_cell,
            "shuttleLevelHeightMeters": self.shuttle_level_height_meters,
            "floorHeightMeters": self.floor_height_meters,
            "coordinateContractVersion": self.coordinate_contract_version,
        }


@dataclass(frozen=True)
class Position:
    row: float | None
    col: float | None
    floor: str | None = None
    layer: int | None = None
    floor_index: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "row", _optional_finite("row", self.row))
        object.__setattr__(self, "col", _optional_finite("col", self.col))
        if self.floor is not None and (not isinstance(self.floor, str) or not self.floor.strip()):
            raise ValueError("floor must be a non-empty string when provided")
        if self.layer is not None and (isinstance(self.layer, bool) or not isinstance(self.layer, int)):
            raise ValueError("layer must be an integer when provided")
        if self.floor_index is not None and (isinstance(self.floor_index, bool) or not isinstance(self.floor_index, int)):
            raise ValueError("floor_index must be an integer when provided")

    def to_dict(self) -> dict[str, object | None]:
        return {
            "row": self.row,
            "col": self.col,
            "floor": self.floor,
            "layer": self.layer,
            "floorIndex": self.floor_index,
        }


@dataclass(frozen=True)
class MovementInput:
    movement_id: str
    robot_id: str
    robot_type: str
    run_id: str
    run_instance_id: str
    from_position: Position | None
    to_position: Position | None
    from_tick: int
    to_tick: int
    movement_type: MovementType = MovementType.UNKNOWN
    planned_path_id: str | None = None
    replan_id: str | None = None
    physics_contract_version: str | None = None
    horizontal_edge_verified: bool = False
    diagonal_allowed: bool = False

    def __post_init__(self) -> None:
        for name, value in (
            ("movement_id", self.movement_id),
            ("robot_id", self.robot_id),
            ("robot_type", self.robot_type),
            ("run_id", self.run_id),
            ("run_instance_id", self.run_instance_id),
        ):
            _require_identifier(name, value)
        if isinstance(self.from_tick, bool) or not isinstance(self.from_tick, int):
            raise ValueError("from_tick must be an integer")
        if isinstance(self.to_tick, bool) or not isinstance(self.to_tick, int):
            raise ValueError("to_tick must be an integer")
        if not isinstance(self.movement_type, MovementType):
            raise ValueError("movement_type must be a MovementType")
        if self.physics_contract_version is not None:
            _require_identifier("physics_contract_version", self.physics_contract_version)
        if not isinstance(self.horizontal_edge_verified, bool) or not isinstance(self.diagonal_allowed, bool):
            raise ValueError("edge verification flags must be booleans")


@dataclass(frozen=True)
class MovementSegment:
    movement_id: str
    robot_id: str
    robot_type: str
    run_id: str
    run_instance_id: str
    from_position: Position | None
    to_position: Position | None
    from_tick: int
    to_tick: int
    movement_type: MovementType
    segment_horizontal_meters: float | None
    segment_vertical_meters: float | None
    planned_path_id: str | None
    replan_id: str | None
    physics_contract_version: str | None
    data_quality: DataQuality
    issue: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "movementId": self.movement_id,
            "robotId": self.robot_id,
            "robotType": self.robot_type,
            "runId": self.run_id,
            "runInstanceId": self.run_instance_id,
            "fromPosition": self.from_position.to_dict() if self.from_position else None,
            "toPosition": self.to_position.to_dict() if self.to_position else None,
            "fromTick": self.from_tick,
            "toTick": self.to_tick,
            "movementType": self.movement_type.value,
            "segmentHorizontalMeters": self.segment_horizontal_meters,
            "segmentVerticalMeters": self.segment_vertical_meters,
            "plannedPathId": self.planned_path_id,
            "replanId": self.replan_id,
            "physicsContractVersion": self.physics_contract_version,
            "dataQuality": self.data_quality.value,
            "issue": self.issue,
        }


@dataclass(frozen=True)
class PathDistanceResult:
    planned_horizontal_meters: float | None
    segment_count: int
    distance_source: DistanceSource
    data_quality: DataQuality
    physics_contract_version: str | None
    issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "plannedHorizontalMeters": self.planned_horizontal_meters,
            "segmentCount": self.segment_count,
            "distanceSource": self.distance_source.value,
            "dataQuality": self.data_quality.value,
            "physicsContractVersion": self.physics_contract_version,
            "issues": list(self.issues),
        }


@dataclass(frozen=True)
class DistanceAggregate:
    run_id: str
    run_instance_id: str
    robot_id: str
    robot_type: str
    floor_scope: str
    physics_contract_version: str | None
    actual_horizontal_meters: float | None
    actual_vertical_meters: float | None
    movement_count: int
    duplicate_count: int
    invalid_count: int
    movement_type_horizontal_meters: Mapping[str, float]
    distance_source: DistanceSource
    data_quality: DataQuality
    issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "runId": self.run_id,
            "runInstanceId": self.run_instance_id,
            "robotId": self.robot_id,
            "robotType": self.robot_type,
            "floorScope": self.floor_scope,
            "physicsContractVersion": self.physics_contract_version,
            "actualHorizontalMeters": self.actual_horizontal_meters,
            "actualVerticalMeters": self.actual_vertical_meters,
            "movementCount": self.movement_count,
            "duplicateCount": self.duplicate_count,
            "invalidCount": self.invalid_count,
            "movementTypeHorizontalMeters": dict(self.movement_type_horizontal_meters),
            "distanceSource": self.distance_source.value,
            "dataQuality": self.data_quality.value,
            "issues": list(self.issues),
        }


@dataclass(frozen=True)
class DistanceComparison:
    planned_horizontal_meters: float | None
    actual_horizontal_meters: float | None
    actual_vertical_meters: float | None
    extra_distance_meters: float | None
    attributable_replan_extra_meters: float | None
    distance_source: DistanceSource
    data_quality: DataQuality
    issue: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "plannedHorizontalMeters": self.planned_horizontal_meters,
            "actualHorizontalMeters": self.actual_horizontal_meters,
            "actualVerticalMeters": self.actual_vertical_meters,
            "extraDistanceMeters": self.extra_distance_meters,
            "attributableReplanExtraMeters": self.attributable_replan_extra_meters,
            "distanceSource": self.distance_source.value,
            "dataQuality": self.data_quality.value,
            "issue": self.issue,
        }


def _positions_equal(left: Position | None, right: Position | None) -> bool:
    return left == right


def _floor_scope(movement: MovementInput) -> str:
    start = movement.from_position.floor if movement.from_position else None
    end = movement.to_position.floor if movement.to_position else None
    if start and end and start != end:
        return f"{start}->{end}"
    return start or end or "UNKNOWN"


def _horizontal_distance(
    movement: MovementInput,
    physics: PhysicsContract,
) -> float:
    start = movement.from_position
    end = movement.to_position
    assert start is not None and end is not None
    assert start.row is not None and start.col is not None
    assert end.row is not None and end.col is not None
    delta_row = end.row - start.row
    delta_col = end.col - start.col
    if delta_row == 0 and delta_col == 0:
        return 0.0

    is_diagonal = delta_row != 0 and delta_col != 0
    robot_type = movement.robot_type.strip().lower()
    if is_diagonal and not (movement.diagonal_allowed and movement.horizontal_edge_verified):
        raise ValueError("diagonal movement requires an explicitly verified topology edge")
    if robot_type == "shuttle" and not movement.horizontal_edge_verified:
        raise ValueError("shuttle movement requires a verified track edge")
    if robot_type == "ctu" and not movement.horizontal_edge_verified:
        if is_diagonal or abs(delta_row) + abs(delta_col) != 1:
            raise ValueError("non-adjacent CTU movement requires a verified path edge")
    if robot_type not in {"ctu", "shuttle"} and not movement.horizontal_edge_verified:
        raise ValueError("unknown robot geometry requires a verified path edge")
    return physics.meters_per_cell * hypot(delta_row, delta_col)


def _vertical_distance(movement: MovementInput, physics: PhysicsContract) -> tuple[float | None, str | None]:
    start = movement.from_position
    end = movement.to_position
    assert start is not None and end is not None
    layer_changed = start.layer is not None and end.layer is not None and start.layer != end.layer
    floor_changed = (
        (start.floor is not None and end.floor is not None and start.floor != end.floor)
        or (start.floor_index is not None and end.floor_index is not None and start.floor_index != end.floor_index)
    )
    if (layer_changed or floor_changed) and movement.movement_type is not MovementType.LIFT:
        raise ValueError("vertical coordinate changes require movement_type LIFT")
    if layer_changed:
        return abs(end.layer - start.layer) * physics.shuttle_level_height_meters, None
    if floor_changed:
        if start.floor_index is None or end.floor_index is None:
            return None, "floor changed without explicit floor indexes"
        return abs(end.floor_index - start.floor_index) * physics.floor_height_meters, None
    return 0.0, None


def calculate_movement_segment(
    movement: MovementInput,
    physics: PhysicsContract | None,
) -> MovementSegment:
    """Calculate one verified movement segment without mutating either input."""

    if movement.to_tick < movement.from_tick:
        raise ValueError("movement tick order is reversed")
    if movement.to_tick == movement.from_tick and not _positions_equal(movement.from_position, movement.to_position):
        raise ValueError("a non-stationary movement cannot occur within the same tick")
    if physics is None:
        return _segment_result(movement, None, None, DataQuality.UNAVAILABLE, "physics contract is required")
    if movement.physics_contract_version is None:
        return _segment_result(movement, None, None, DataQuality.UNAVAILABLE, "movement has no frozen physics contract version")
    if movement.physics_contract_version != physics.physics_contract_version:
        raise ValueError("movement and supplied physics contract versions differ")
    if movement.from_position is None or movement.to_position is None:
        return _segment_result(movement, None, None, DataQuality.INCOMPLETE, "fromPosition and toPosition are required")
    start = movement.from_position
    end = movement.to_position
    if None in (start.row, start.col, end.row, end.col):
        return _segment_result(movement, None, None, DataQuality.INCOMPLETE, "row and col are required")

    horizontal = _horizontal_distance(movement, physics)
    vertical, issue = _vertical_distance(movement, physics)
    if issue:
        return _segment_result(movement, horizontal, None, DataQuality.INCOMPLETE, issue)
    return _segment_result(movement, horizontal, vertical, DataQuality.COMPLETE, None)


def _segment_result(
    movement: MovementInput,
    horizontal: float | None,
    vertical: float | None,
    quality: DataQuality,
    issue: str | None,
) -> MovementSegment:
    return MovementSegment(
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
        segment_horizontal_meters=horizontal,
        segment_vertical_meters=vertical,
        planned_path_id=movement.planned_path_id,
        replan_id=movement.replan_id,
        physics_contract_version=movement.physics_contract_version,
        data_quality=quality,
        issue=issue,
    )


def calculate_planned_path(
    positions: Sequence[Position],
    physics: PhysicsContract | None,
    *,
    robot_type: str,
    verified_edges: Sequence[bool] | None = None,
    diagonal_edges: Sequence[bool] | None = None,
) -> PathDistanceResult:
    """Sum each planned path edge; never infer a direct start-to-end shortcut."""

    if physics is None:
        return PathDistanceResult(None, 0, DistanceSource.PLANNED_PATH_EDGES, DataQuality.UNAVAILABLE, None, ("physics contract is required",))
    if not positions:
        return PathDistanceResult(None, 0, DistanceSource.PLANNED_PATH_EDGES, DataQuality.INCOMPLETE, physics.physics_contract_version, ("planned path has no positions",))
    if len(positions) == 1:
        return PathDistanceResult(0.0, 0, DistanceSource.PLANNED_PATH_EDGES, DataQuality.COMPLETE, physics.physics_contract_version)
    edge_count = len(positions) - 1
    verified = tuple(verified_edges) if verified_edges is not None else (False,) * edge_count
    diagonals = tuple(diagonal_edges) if diagonal_edges is not None else (False,) * edge_count
    if len(verified) != edge_count or len(diagonals) != edge_count:
        raise ValueError("edge flag count must equal len(positions) - 1")
    total = 0.0
    for index in range(edge_count):
        movement = MovementInput(
            movement_id=f"planned-edge-{index}",
            robot_id="planned-path",
            robot_type=robot_type,
            run_id="planned",
            run_instance_id="planned",
            from_position=positions[index],
            to_position=positions[index + 1],
            from_tick=index,
            to_tick=index + 1,
            movement_type=MovementType.LIFT if _has_vertical_change(positions[index], positions[index + 1]) else MovementType.NORMAL,
            physics_contract_version=physics.physics_contract_version,
            horizontal_edge_verified=verified[index],
            diagonal_allowed=diagonals[index],
        )
        segment = calculate_movement_segment(movement, physics)
        if segment.data_quality is not DataQuality.COMPLETE or segment.segment_horizontal_meters is None:
            return PathDistanceResult(None, index, DistanceSource.PLANNED_PATH_EDGES, DataQuality.INCOMPLETE, physics.physics_contract_version, (segment.issue or "incomplete edge",))
        total += segment.segment_horizontal_meters
    return PathDistanceResult(total, edge_count, DistanceSource.PLANNED_PATH_EDGES, DataQuality.COMPLETE, physics.physics_contract_version)


def _has_vertical_change(start: Position, end: Position) -> bool:
    return (
        start.layer is not None
        and end.layer is not None
        and start.layer != end.layer
    ) or (
        start.floor is not None
        and end.floor is not None
        and start.floor != end.floor
    ) or (
        start.floor_index is not None
        and end.floor_index is not None
        and start.floor_index != end.floor_index
    )


def aggregate_movements(
    movements: Iterable[MovementInput],
    physics: PhysicsContract | None,
) -> tuple[DistanceAggregate, ...]:
    """Deduplicate, validate continuity, and aggregate by run generation/robot/floor."""

    materialized = tuple(movements)
    by_id: dict[str, list[MovementInput]] = {}
    for movement in materialized:
        by_id.setdefault(movement.movement_id, []).append(movement)

    accepted: list[MovementInput] = []
    duplicate_counts: dict[tuple[str, str, str, str, str | None, str], int] = {}
    conflict_keys: dict[tuple[str, str, str, str, str | None, str], list[str]] = {}
    for movement_id, copies in by_id.items():
        first = copies[0]
        key = _group_key(first)
        if any(copy != first for copy in copies[1:]):
            conflict_keys.setdefault(key, []).append(f"conflicting duplicate movementId {movement_id}")
            continue
        accepted.append(first)
        duplicate_counts[key] = duplicate_counts.get(key, 0) + len(copies) - 1

    continuity_issues: dict[tuple[str, str, str, str, str | None, str], list[str]] = {}
    vehicle_groups: dict[tuple[str, str, str, str, str | None], list[MovementInput]] = {}
    for movement in accepted:
        vehicle_groups.setdefault(_vehicle_key(movement), []).append(movement)
    for group in vehicle_groups.values():
        ordered = sorted(group, key=lambda item: (item.from_tick, item.to_tick, item.movement_id))
        previous: MovementInput | None = None
        for current in ordered:
            if previous is not None:
                if current.from_tick <= previous.from_tick:
                    continuity_issues.setdefault(_group_key(current), []).append("duplicate or non-increasing movement tick")
                if not _positions_equal(previous.to_position, current.from_position):
                    message = f"trajectory gap between {previous.movement_id} and {current.movement_id}"
                    continuity_issues.setdefault(_group_key(previous), []).append(message)
                    continuity_issues.setdefault(_group_key(current), []).append(message)
            previous = current

    grouped: dict[tuple[str, str, str, str, str | None, str], list[MovementInput]] = {}
    for movement in accepted:
        grouped.setdefault(_group_key(movement), []).append(movement)
    for key in set(conflict_keys) | set(duplicate_counts):
        grouped.setdefault(key, [])

    results: list[DistanceAggregate] = []
    for key in sorted(grouped, key=lambda item: tuple("" if value is None else str(value) for value in item)):
        items = sorted(grouped[key], key=lambda item: (item.from_tick, item.to_tick, item.movement_id))
        issues = list(conflict_keys.get(key, ())) + list(continuity_issues.get(key, ()))
        horizontal_total = 0.0
        vertical_total = 0.0
        valid_count = 0
        invalid_count = len(conflict_keys.get(key, ()))
        type_totals: dict[str, float] = {}
        for movement in items:
            try:
                segment = calculate_movement_segment(movement, physics)
            except ValueError as exc:
                invalid_count += 1
                issues.append(f"{movement.movement_id}: {exc}")
                continue
            if segment.data_quality is not DataQuality.COMPLETE:
                invalid_count += 1
                issues.append(f"{movement.movement_id}: {segment.issue or segment.data_quality.value}")
                continue
            assert segment.segment_horizontal_meters is not None
            assert segment.segment_vertical_meters is not None
            valid_count += 1
            horizontal_total += segment.segment_horizontal_meters
            vertical_total += segment.segment_vertical_meters
            movement_name = movement.movement_type.value
            type_totals[movement_name] = type_totals.get(movement_name, 0.0) + segment.segment_horizontal_meters

        if valid_count == 0:
            horizontal_value = None
            vertical_value = None
            quality = DataQuality.UNAVAILABLE if physics is None else DataQuality.INCOMPLETE
        else:
            horizontal_value = horizontal_total
            vertical_value = vertical_total
            quality = DataQuality.COMPLETE if not issues and invalid_count == 0 else DataQuality.INCOMPLETE
        run_id, run_instance_id, robot_id, robot_type, version, floor_scope = key
        results.append(
            DistanceAggregate(
                run_id=run_id,
                run_instance_id=run_instance_id,
                robot_id=robot_id,
                robot_type=robot_type,
                floor_scope=floor_scope,
                physics_contract_version=version,
                actual_horizontal_meters=horizontal_value,
                actual_vertical_meters=vertical_value,
                movement_count=valid_count,
                duplicate_count=duplicate_counts.get(key, 0),
                invalid_count=invalid_count,
                movement_type_horizontal_meters=type_totals,
                distance_source=DistanceSource.ACTUAL_MOVEMENT_SEGMENTS,
                data_quality=quality,
                issues=tuple(issues),
            )
        )
    return tuple(results)


def _vehicle_key(movement: MovementInput) -> tuple[str, str, str, str, str | None]:
    return (
        movement.run_id,
        movement.run_instance_id,
        movement.robot_id,
        movement.robot_type,
        movement.physics_contract_version,
    )


def _group_key(movement: MovementInput) -> tuple[str, str, str, str, str | None, str]:
    return (*_vehicle_key(movement), _floor_scope(movement))


def compare_planned_and_actual(
    planned: PathDistanceResult,
    actual: DistanceAggregate,
    *,
    planned_path_id: str | None,
    actual_planned_path_ids: Sequence[str | None],
) -> DistanceComparison:
    """Compare complete, path-correlated totals without inventing causation."""

    comparable = (
        planned.data_quality is DataQuality.COMPLETE
        and actual.data_quality is DataQuality.COMPLETE
        and planned.planned_horizontal_meters is not None
        and actual.actual_horizontal_meters is not None
        and planned_path_id is not None
        and bool(actual_planned_path_ids)
        and all(value == planned_path_id for value in actual_planned_path_ids)
        and planned.physics_contract_version is not None
        and planned.physics_contract_version == actual.physics_contract_version
    )
    if not comparable:
        return DistanceComparison(
            planned.planned_horizontal_meters,
            actual.actual_horizontal_meters,
            actual.actual_vertical_meters,
            None,
            None,
            DistanceSource.PLANNED_AND_ACTUAL,
            DataQuality.INCOMPLETE,
            "complete path correlation is required",
        )
    extra = max(0.0, actual.actual_horizontal_meters - planned.planned_horizontal_meters)
    return DistanceComparison(
        planned.planned_horizontal_meters,
        actual.actual_horizontal_meters,
        actual.actual_vertical_meters,
        extra,
        None,
        DistanceSource.PLANNED_AND_ACTUAL,
        DataQuality.COMPLETE,
        None,
    )


__all__ = [
    "DataQuality",
    "DistanceAggregate",
    "DistanceComparison",
    "DistanceSource",
    "MovementInput",
    "MovementSegment",
    "MovementType",
    "PathDistanceResult",
    "PhysicsContract",
    "Position",
    "aggregate_movements",
    "calculate_movement_segment",
    "calculate_planned_path",
    "compare_planned_and_actual",
]
