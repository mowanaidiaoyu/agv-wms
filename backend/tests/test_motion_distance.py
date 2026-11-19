"""Isolated standard-library tests for the P3-1 distance contract."""

from __future__ import annotations

import json
import math
import sys
import unittest
from dataclasses import replace
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.motion_distance import (  # noqa: E402
    DataQuality,
    MovementInput,
    MovementType,
    PhysicsContract,
    Position,
    aggregate_movements,
    calculate_movement_segment,
    calculate_planned_path,
    compare_planned_and_actual,
)


def physics(*, meters: float = 0.8, version: str = "physics-test-v1") -> PhysicsContract:
    return PhysicsContract(version, meters, 1.4, 4.2, "grid-row-col-v1")


def position(
    row: float,
    col: float,
    *,
    floor: str = "1F",
    layer: int = 1,
    floor_index: int | None = 1,
) -> Position:
    return Position(row, col, floor, layer, floor_index)


def movement(
    movement_id: str = "m-1",
    start: Position | None = None,
    end: Position | None = None,
    *,
    movement_type: MovementType = MovementType.NORMAL,
    robot_id: str = "ctu-01",
    robot_type: str = "ctu",
    run_id: str = "run-1",
    run_instance_id: str = "generation-1",
    from_tick: int = 1,
    to_tick: int = 2,
    version: str | None = "physics-test-v1",
    verified: bool = False,
    diagonal: bool = False,
    planned_path_id: str | None = "path-1",
) -> MovementInput:
    return MovementInput(
        movement_id=movement_id,
        robot_id=robot_id,
        robot_type=robot_type,
        run_id=run_id,
        run_instance_id=run_instance_id,
        from_position=position(1, 1) if start is None else start,
        to_position=position(1, 2) if end is None else end,
        from_tick=from_tick,
        to_tick=to_tick,
        movement_type=movement_type,
        planned_path_id=planned_path_id,
        physics_contract_version=version,
        horizontal_edge_verified=verified,
        diagonal_allowed=diagonal,
    )


class PhysicsContractTests(unittest.TestCase):
    def test_positive_parameters_are_explicit_and_serializable(self) -> None:
        value = physics()
        self.assertEqual(value.to_dict()["metersPerCell"], 0.8)
        self.assertEqual(value.to_dict()["physicsContractVersion"], "physics-test-v1")

    def test_zero_parameter_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "finite positive"):
            PhysicsContract("v", 0, 1, 1, "coords")

    def test_negative_parameter_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "finite positive"):
            PhysicsContract("v", 1, -1, 1, "coords")

    def test_nan_and_infinity_are_rejected(self) -> None:
        for bad in (math.nan, math.inf, -math.inf):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                PhysicsContract("v", bad, 1, 1, "coords")

    def test_numeric_strings_and_booleans_are_rejected(self) -> None:
        for bad in ("0.8", True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                PhysicsContract("v", bad, 1, 1, "coords")  # type: ignore[arg-type]


class SegmentCalculationTests(unittest.TestCase):
    def test_ctu_adjacent_horizontal_distance(self) -> None:
        result = calculate_movement_segment(movement(), physics())
        self.assertEqual(result.segment_horizontal_meters, 0.8)
        self.assertEqual(result.segment_vertical_meters, 0.0)

    def test_ctu_stationary_segment_is_real_zero(self) -> None:
        same = position(3, 4)
        result = calculate_movement_segment(movement(start=same, end=same), physics())
        self.assertEqual(result.segment_horizontal_meters, 0.0)
        self.assertEqual(result.data_quality, DataQuality.COMPLETE)

    def test_ctu_respects_different_meters_per_cell(self) -> None:
        result = calculate_movement_segment(movement(), physics(meters=1.25))
        self.assertEqual(result.segment_horizontal_meters, 1.25)

    def test_explicit_diagonal_edge(self) -> None:
        item = movement(start=position(1, 1), end=position(2, 2), verified=True, diagonal=True)
        result = calculate_movement_segment(item, physics())
        self.assertAlmostEqual(result.segment_horizontal_meters or -1, math.sqrt(2) * 0.8)

    def test_unverified_diagonal_edge_is_rejected(self) -> None:
        item = movement(start=position(1, 1), end=position(2, 2))
        with self.assertRaisesRegex(ValueError, "diagonal"):
            calculate_movement_segment(item, physics())

    def test_unverified_ctu_jump_is_rejected(self) -> None:
        item = movement(start=position(1, 1), end=position(1, 4))
        with self.assertRaisesRegex(ValueError, "non-adjacent"):
            calculate_movement_segment(item, physics())

    def test_shuttle_two_cell_track_edge(self) -> None:
        item = movement(
            start=position(10, 20),
            end=position(10, 22),
            robot_id="shuttle-01",
            robot_type="shuttle",
            verified=True,
        )
        result = calculate_movement_segment(item, physics())
        self.assertEqual(result.segment_horizontal_meters, 1.6)

    def test_unverified_shuttle_edge_is_rejected(self) -> None:
        item = movement(robot_type="shuttle", robot_id="shuttle-01")
        with self.assertRaisesRegex(ValueError, "verified track edge"):
            calculate_movement_segment(item, physics())

    def test_lift_single_level(self) -> None:
        item = movement(
            start=position(10, 20, layer=1),
            end=position(10, 20, layer=2),
            movement_type=MovementType.LIFT,
            robot_type="shuttle",
            robot_id="shuttle-01",
        )
        result = calculate_movement_segment(item, physics())
        self.assertEqual(result.segment_vertical_meters, 1.4)
        self.assertEqual(result.segment_horizontal_meters, 0.0)

    def test_lift_multiple_levels(self) -> None:
        item = movement(
            start=position(10, 20, layer=1),
            end=position(10, 20, layer=4),
            movement_type=MovementType.LIFT,
            robot_type="shuttle",
            robot_id="shuttle-01",
        )
        self.assertAlmostEqual(calculate_movement_segment(item, physics()).segment_vertical_meters or -1, 4.2)

    def test_cross_floor_without_indexes_is_incomplete(self) -> None:
        start = Position(1, 1, "1F", None, None)
        end = Position(1, 1, "2F", None, None)
        result = calculate_movement_segment(movement(start=start, end=end, movement_type=MovementType.LIFT), physics())
        self.assertIsNone(result.segment_vertical_meters)
        self.assertEqual(result.data_quality, DataQuality.INCOMPLETE)

    def test_floor_height_uses_explicit_floor_indexes(self) -> None:
        start = position(1, 1, floor="1F", floor_index=1)
        end = position(1, 1, floor="3F", floor_index=3)
        result = calculate_movement_segment(movement(start=start, end=end, movement_type=MovementType.LIFT), physics())
        self.assertEqual(result.segment_vertical_meters, 8.4)

    def test_horizontal_and_vertical_are_separate(self) -> None:
        start = position(10, 20, layer=1)
        end = position(10, 22, layer=2)
        item = movement(
            start=start,
            end=end,
            movement_type=MovementType.LIFT,
            robot_type="shuttle",
            robot_id="shuttle-01",
            verified=True,
        )
        result = calculate_movement_segment(item, physics())
        self.assertEqual(result.segment_horizontal_meters, 1.6)
        self.assertEqual(result.segment_vertical_meters, 1.4)

    def test_non_lift_vertical_change_is_rejected(self) -> None:
        item = movement(start=position(1, 1, layer=1), end=position(1, 1, layer=2))
        with self.assertRaisesRegex(ValueError, "movement_type LIFT"):
            calculate_movement_segment(item, physics())

    def test_missing_from_position_is_incomplete(self) -> None:
        item = replace(movement(), from_position=None)
        result = calculate_movement_segment(item, physics())
        self.assertIsNone(result.segment_horizontal_meters)
        self.assertEqual(result.data_quality, DataQuality.INCOMPLETE)

    def test_missing_to_position_is_incomplete(self) -> None:
        item = replace(movement(), to_position=None)
        result = calculate_movement_segment(item, physics())
        self.assertIsNone(result.segment_horizontal_meters)
        self.assertEqual(result.data_quality, DataQuality.INCOMPLETE)

    def test_reverse_ticks_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "reversed"):
            calculate_movement_segment(movement(from_tick=3, to_tick=2), physics())

    def test_non_stationary_duplicate_tick_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "same tick"):
            calculate_movement_segment(movement(from_tick=2, to_tick=2), physics())

    def test_missing_physics_does_not_assume_one_meter(self) -> None:
        result = calculate_movement_segment(movement(), None)
        self.assertIsNone(result.segment_horizontal_meters)
        self.assertEqual(result.data_quality, DataQuality.UNAVAILABLE)

    def test_missing_frozen_version_is_unavailable(self) -> None:
        result = calculate_movement_segment(movement(version=None), physics())
        self.assertIsNone(result.segment_horizontal_meters)
        self.assertEqual(result.data_quality, DataQuality.UNAVAILABLE)

    def test_physics_version_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "versions differ"):
            calculate_movement_segment(movement(version="old"), physics())

    def test_input_is_not_mutated(self) -> None:
        item = movement()
        before = repr(item)
        calculate_movement_segment(item, physics())
        self.assertEqual(repr(item), before)

    def test_segment_is_json_serializable(self) -> None:
        result = calculate_movement_segment(movement(), physics())
        payload = json.loads(json.dumps(result.to_dict(), ensure_ascii=False))
        self.assertEqual(payload["movementId"], "m-1")
        self.assertEqual(payload["dataQuality"], "COMPLETE")


class PlannedPathTests(unittest.TestCase):
    def test_shuttle_multi_edge_path_uses_each_two_cell_edge(self) -> None:
        points = [position(10, 20), position(10, 22), position(12, 22)]
        result = calculate_planned_path(points, physics(), robot_type="shuttle", verified_edges=[True, True])
        self.assertEqual(result.planned_horizontal_meters, 3.2)
        self.assertEqual(result.segment_count, 2)

    def test_empty_planned_path_is_not_zero(self) -> None:
        result = calculate_planned_path([], physics(), robot_type="ctu")
        self.assertIsNone(result.planned_horizontal_meters)
        self.assertEqual(result.data_quality, DataQuality.INCOMPLETE)

    def test_single_position_path_is_valid_zero(self) -> None:
        result = calculate_planned_path([position(1, 1)], physics(), robot_type="ctu")
        self.assertEqual(result.planned_horizontal_meters, 0.0)

    def test_planned_and_actual_are_calculated_separately(self) -> None:
        planned = calculate_planned_path([position(1, 1), position(1, 2)], physics(), robot_type="ctu")
        actual = aggregate_movements([movement()], physics())[0]
        comparison = compare_planned_and_actual(
            planned,
            actual,
            planned_path_id="path-1",
            actual_planned_path_ids=["path-1"],
        )
        self.assertEqual(comparison.planned_horizontal_meters, 0.8)
        self.assertEqual(comparison.actual_horizontal_meters, 0.8)
        self.assertEqual(comparison.extra_distance_meters, 0.0)

    def test_unproven_replan_attribution_remains_null(self) -> None:
        planned = calculate_planned_path([position(1, 1), position(1, 2)], physics(), robot_type="ctu")
        actual_item = movement(end=position(1, 3), verified=True)
        actual = aggregate_movements([actual_item], physics())[0]
        comparison = compare_planned_and_actual(
            planned,
            actual,
            planned_path_id="path-1",
            actual_planned_path_ids=["path-1"],
        )
        self.assertEqual(comparison.extra_distance_meters, 0.8)
        self.assertIsNone(comparison.attributable_replan_extra_meters)

    def test_missing_path_correlation_returns_null_difference(self) -> None:
        planned = calculate_planned_path([position(1, 1), position(1, 2)], physics(), robot_type="ctu")
        actual = aggregate_movements([movement()], physics())[0]
        comparison = compare_planned_and_actual(planned, actual, planned_path_id=None, actual_planned_path_ids=[])
        self.assertIsNone(comparison.extra_distance_meters)
        self.assertEqual(comparison.data_quality, DataQuality.INCOMPLETE)


class AggregationTests(unittest.TestCase):
    def _chain(self, second_type: MovementType = MovementType.NORMAL) -> list[MovementInput]:
        return [
            movement("m-1", position(1, 1), position(1, 2), from_tick=1, to_tick=2),
            movement("m-2", position(1, 2), position(1, 3), from_tick=2, to_tick=3, movement_type=second_type),
        ]

    def test_normal_movement_accumulates(self) -> None:
        result = aggregate_movements(self._chain(), physics())[0]
        self.assertEqual(result.actual_horizontal_meters, 1.6)
        self.assertEqual(result.movement_type_horizontal_meters["NORMAL"], 1.6)

    def test_shuttle_multi_edge_actual_trajectory(self) -> None:
        items = [
            movement(
                "s-1",
                position(10, 20),
                position(10, 22),
                robot_id="shuttle-01",
                robot_type="shuttle",
                from_tick=1,
                to_tick=2,
                verified=True,
            ),
            movement(
                "s-2",
                position(10, 22),
                position(12, 22),
                robot_id="shuttle-01",
                robot_type="shuttle",
                from_tick=2,
                to_tick=3,
                verified=True,
            ),
        ]
        result = aggregate_movements(items, physics())[0]
        self.assertEqual(result.actual_horizontal_meters, 3.2)
        self.assertEqual(result.data_quality, DataQuality.COMPLETE)

    def test_yield_movement_accumulates_by_type(self) -> None:
        result = aggregate_movements(self._chain(MovementType.YIELD), physics())[0]
        self.assertEqual(result.movement_type_horizontal_meters["YIELD"], 0.8)

    def test_backoff_movement_accumulates_by_type(self) -> None:
        result = aggregate_movements(self._chain(MovementType.BACKOFF), physics())[0]
        self.assertEqual(result.movement_type_horizontal_meters["BACKOFF"], 0.8)

    def test_return_to_standby_accumulates_by_type(self) -> None:
        result = aggregate_movements(self._chain(MovementType.RETURN_TO_STANDBY), physics())[0]
        self.assertEqual(result.movement_type_horizontal_meters["RETURN_TO_STANDBY"], 0.8)

    def test_identical_movement_id_is_idempotent(self) -> None:
        item = movement()
        result = aggregate_movements([item, item], physics())[0]
        self.assertEqual(result.actual_horizontal_meters, 0.8)
        self.assertEqual(result.movement_count, 1)
        self.assertEqual(result.duplicate_count, 1)

    def test_conflicting_movement_id_is_not_counted(self) -> None:
        first = movement()
        conflicting = replace(first, to_position=position(1, 3), horizontal_edge_verified=True)
        result = aggregate_movements([first, conflicting], physics())[0]
        self.assertIsNone(result.actual_horizontal_meters)
        self.assertEqual(result.data_quality, DataQuality.INCOMPLETE)

    def test_different_robots_are_separate(self) -> None:
        items = [movement(robot_id="ctu-01"), movement("m-2", robot_id="ctu-02")]
        results = aggregate_movements(items, physics())
        self.assertEqual({item.robot_id for item in results}, {"ctu-01", "ctu-02"})

    def test_different_run_generations_are_separate(self) -> None:
        items = [movement(), movement("m-2", run_instance_id="generation-2")]
        results = aggregate_movements(items, physics())
        self.assertEqual({item.run_instance_id for item in results}, {"generation-1", "generation-2"})

    def test_different_floors_are_separate(self) -> None:
        floor_two = movement(
            "m-2",
            position(1, 1, floor="2F", floor_index=2),
            position(1, 2, floor="2F", floor_index=2),
        )
        results = aggregate_movements([movement(), floor_two], physics())
        self.assertEqual({item.floor_scope for item in results}, {"1F", "2F"})

    def test_different_physics_versions_are_not_mixed(self) -> None:
        results = aggregate_movements([movement(), movement("m-2", version="physics-test-v2")], physics())
        self.assertEqual(len(results), 2)
        self.assertEqual({item.physics_contract_version for item in results}, {"physics-test-v1", "physics-test-v2"})

    def test_trajectory_gap_marks_incomplete_without_endpoint_fill(self) -> None:
        items = [
            movement("m-1", position(1, 1), position(1, 2), from_tick=1, to_tick=2),
            movement("m-2", position(1, 4), position(1, 5), from_tick=3, to_tick=4),
        ]
        result = aggregate_movements(items, physics())[0]
        self.assertEqual(result.actual_horizontal_meters, 1.6)
        self.assertEqual(result.data_quality, DataQuality.INCOMPLETE)
        self.assertTrue(any("trajectory gap" in issue for issue in result.issues))

    def test_duplicate_event_tick_marks_incomplete(self) -> None:
        items = [
            movement("m-1", position(1, 1), position(1, 2), from_tick=1, to_tick=2),
            movement("m-2", position(1, 2), position(1, 3), from_tick=1, to_tick=3),
        ]
        result = aggregate_movements(items, physics())[0]
        self.assertEqual(result.data_quality, DataQuality.INCOMPLETE)
        self.assertTrue(any("non-increasing" in issue for issue in result.issues))

    def test_no_valid_samples_are_not_reported_as_complete_zero(self) -> None:
        result = aggregate_movements([replace(movement(), from_position=None)], physics())[0]
        self.assertIsNone(result.actual_horizontal_meters)
        self.assertIsNone(result.actual_vertical_meters)
        self.assertEqual(result.data_quality, DataQuality.INCOMPLETE)

    def test_valid_stationary_sample_aggregates_to_zero(self) -> None:
        same = position(1, 1)
        result = aggregate_movements([movement(start=same, end=same)], physics())[0]
        self.assertEqual(result.actual_horizontal_meters, 0.0)
        self.assertEqual(result.data_quality, DataQuality.COMPLETE)

    def test_aggregate_is_json_serializable_and_deterministic(self) -> None:
        first = [item.to_dict() for item in aggregate_movements(self._chain(), physics())]
        second = [item.to_dict() for item in aggregate_movements(self._chain(), physics())]
        self.assertEqual(first, second)
        json.dumps(first, ensure_ascii=False, sort_keys=True)


if __name__ == "__main__":
    unittest.main()
