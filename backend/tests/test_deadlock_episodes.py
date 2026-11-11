"""Standard-library tests for the standalone deadlock episode contract."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
import unittest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.deadlock_episodes import (  # noqa: E402
    DataQuality,
    DeadlockConfig,
    EpisodeStatus,
    MitigationAction,
    ResourceType,
    RobotSnapshot,
    advance_deadlock_state,
    build_wait_for_graph,
    detect_wait_cycles,
    finish_deadlock_state,
    new_deadlock_state,
    summarize_deadlock_metrics,
)


def robot(
    robot_id: str,
    current: str,
    next_cell: str | None,
    *,
    waiting: bool = True,
    wait_ticks: int = 1,
    progress: str | None = "phase-1",
    declared_blocker: str | None = None,
    resource_type: ResourceType | None = None,
    resource_id: str | None = None,
) -> RobotSnapshot:
    return RobotSnapshot(
        robot_id=robot_id,
        current_cell_id=current,
        next_cell_id=next_cell,
        is_waiting=waiting,
        wait_ticks=wait_ticks,
        declared_blocker_robot_id=declared_blocker,
        wait_resource_type=resource_type,
        wait_resource_id=resource_id,
        task_progress_token=progress,
    )


def ring(size: int, *, wait_ticks: int = 1) -> list[RobotSnapshot]:
    ids = [f"R{index + 1}" for index in range(size)]
    cells = [f"cell-{index + 1}" for index in range(size)]
    return [
        robot(
            robot_id,
            cells[index],
            cells[(index + 1) % size],
            wait_ticks=wait_ticks,
        )
        for index, robot_id in enumerate(ids)
    ]


def cleared_ring(*, moved: bool, progress: bool = False) -> list[RobotSnapshot]:
    first_cell = "cell-3" if moved else "cell-1"
    progress_token = "phase-2" if progress else "phase-1"
    return [
        robot("R1", first_cell, None, waiting=False, progress=progress_token),
        robot("R2", "cell-2", None, waiting=False, progress="phase-1"),
    ]


class WaitForGraphTests(unittest.TestCase):
    def test_single_robot_block_has_no_cycle(self) -> None:
        graph = build_wait_for_graph([robot("R1", "cell-1", "cell-2")])
        self.assertEqual(len(graph.unknown_edges), 1)
        self.assertEqual(detect_wait_cycles(graph), ())

    def test_two_robot_head_on_wait_forms_cycle(self) -> None:
        cycles = detect_wait_cycles(build_wait_for_graph(ring(2)))
        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0].participant_robot_ids, ("R1", "R2"))

    def test_three_robot_wait_cycle(self) -> None:
        cycles = detect_wait_cycles(build_wait_for_graph(ring(3)))
        self.assertEqual(cycles[0].participant_robot_ids, ("R1", "R2", "R3"))

    def test_four_robot_wait_cycle(self) -> None:
        cycles = detect_wait_cycles(build_wait_for_graph(ring(4)))
        self.assertEqual(len(cycles[0].waiting_edges), 4)

    def test_multiple_independent_cycles(self) -> None:
        second = [
            robot("R3", "cell-3", "cell-4"),
            robot("R4", "cell-4", "cell-3"),
        ]
        cycles = detect_wait_cycles(build_wait_for_graph(ring(2) + second))
        self.assertEqual(
            {cycle.participant_robot_ids for cycle in cycles},
            {("R1", "R2"), ("R3", "R4")},
        )

    def test_linear_wait_chain_is_not_cycle(self) -> None:
        snapshots = [
            robot("R1", "cell-1", "cell-2"),
            robot("R2", "cell-2", "cell-3"),
            robot("R3", "cell-3", None, waiting=False),
        ]
        self.assertEqual(detect_wait_cycles(build_wait_for_graph(snapshots)), ())

    def test_resource_wait_is_not_vehicle_edge(self) -> None:
        graph = build_wait_for_graph([
            robot(
                "R1",
                "cell-1",
                "cell-2",
                resource_type=ResourceType.LIFT,
                resource_id="lift-01",
            )
        ])
        self.assertEqual(graph.edges[0].resource_type, ResourceType.LIFT)
        self.assertEqual(graph.robot_edges, ())

    def test_explicit_resource_wait_takes_precedence_over_cell_occupancy(self) -> None:
        graph = build_wait_for_graph([
            robot(
                "R1",
                "cell-1",
                "cell-2",
                resource_type=ResourceType.RESERVATION,
                resource_id="reservation-01",
            ),
            robot("R2", "cell-2", None, waiting=False),
        ])
        self.assertEqual(graph.edges[0].resource_type, ResourceType.RESERVATION)
        self.assertIsNone(graph.edges[0].blocker_robot_id)
        self.assertEqual(detect_wait_cycles(graph), ())

    def test_declared_blocker_without_occupancy_is_unknown(self) -> None:
        graph = build_wait_for_graph([
            robot("R1", "cell-1", "cell-2", declared_blocker="R2"),
            robot("R2", "cell-9", None, waiting=False),
        ])
        self.assertEqual(graph.edges[0].resource_type, ResourceType.UNKNOWN_BLOCKER)
        self.assertIsNone(graph.edges[0].blocker_robot_id)
        self.assertEqual(graph.edges[0].evidence, "DECLARED_BLOCKER_UNVERIFIED")

    def test_self_reference_and_invalid_cell_do_not_create_cycle(self) -> None:
        snapshots = [robot("R1", "cell-1", "cell-1")]
        graph = build_wait_for_graph(snapshots, valid_cell_ids={"cell-1"})
        self.assertEqual(graph.robot_edges, ())
        invalid = build_wait_for_graph(
            [robot("R1", "cell-1", "cell-missing")],
            valid_cell_ids={"cell-1"},
        )
        self.assertEqual(invalid.edges[0].evidence, "INVALID_OR_MISSING_NEXT_CELL")

    def test_input_order_does_not_change_cycle_fingerprint(self) -> None:
        forward = detect_wait_cycles(build_wait_for_graph(ring(3)))[0]
        reverse = detect_wait_cycles(build_wait_for_graph(list(reversed(ring(3)))))[0]
        self.assertEqual(forward.fingerprint, reverse.fingerprint)
        self.assertEqual(forward.waiting_edges, reverse.waiting_edges)


class EpisodeLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = DeadlockConfig(persistent_cycle_ticks=3, quiet_window_ticks=2)

    def advance_ring(self, state, start: int, end: int):
        for tick in range(start, end + 1):
            state = advance_deadlock_state(
                state,
                tick,
                ring(2, wait_ticks=tick),
                config=self.config,
            )
        return state

    def test_cycle_reaches_persistent_threshold_on_consecutive_ticks(self) -> None:
        state = self.advance_ring(new_deadlock_state("run-1"), 1, 3)
        episode = state.episodes[0]
        self.assertEqual(episode.persistent_at_tick, 3)
        self.assertEqual(episode.status, EpisodeStatus.PERSISTENT_WAIT_CYCLE)
        self.assertEqual(episode.max_wait_ticks, 3)

    def test_cycle_below_threshold_remains_suspected(self) -> None:
        state = self.advance_ring(new_deadlock_state("run-1"), 1, 2)
        self.assertIsNone(state.episodes[0].persistent_at_tick)
        self.assertEqual(state.episodes[0].status, EpisodeStatus.SUSPECTED_CYCLE)

    def test_alert_without_verified_cycle_creates_no_episode(self) -> None:
        state = advance_deadlock_state(
            new_deadlock_state("run-alert"),
            1,
            [robot("R1", "cell-1", "cell-9"), robot("R2", "cell-2", "cell-8")],
            config=self.config,
        )
        self.assertEqual(state.episodes, ())

    def test_cycle_disappears_with_real_movement_and_resolves(self) -> None:
        state = self.advance_ring(new_deadlock_state("run-resolve"), 1, 3)
        state = advance_deadlock_state(state, 4, cleared_ring(moved=True), config=self.config)
        state = advance_deadlock_state(state, 5, cleared_ring(moved=True), config=self.config)
        episode = state.episodes[0]
        self.assertEqual(episode.status, EpisodeStatus.RESOLVED)
        self.assertEqual(episode.resolved_at_tick, 5)
        self.assertTrue(any(item.startswith("ROBOT_MOVED:R1") for item in episode.resolution_evidence))

    def test_cycle_disappears_without_progress_is_unverified(self) -> None:
        state = self.advance_ring(new_deadlock_state("run-unverified"), 1, 3)
        state = advance_deadlock_state(state, 4, cleared_ring(moved=False), config=self.config)
        state = advance_deadlock_state(state, 5, cleared_ring(moved=False), config=self.config)
        episode = state.episodes[0]
        self.assertEqual(episode.status, EpisodeStatus.ENDED_UNVERIFIED)
        self.assertIsNone(episode.resolved_at_tick)

    def test_task_progress_is_valid_resolution_evidence(self) -> None:
        state = self.advance_ring(new_deadlock_state("run-progress"), 1, 3)
        state = advance_deadlock_state(
            state,
            4,
            cleared_ring(moved=False, progress=True),
            config=self.config,
        )
        state = advance_deadlock_state(
            state,
            5,
            cleared_ring(moved=False, progress=True),
            config=self.config,
        )
        self.assertEqual(state.episodes[0].status, EpisodeStatus.RESOLVED)
        self.assertTrue(
            any(item.startswith("TASK_PROGRESS:R1") for item in state.episodes[0].resolution_evidence)
        )

    def test_run_end_marks_present_cycle_unresolved(self) -> None:
        state = self.advance_ring(new_deadlock_state("run-end"), 1, 3)
        state = finish_deadlock_state(state)
        self.assertTrue(state.ended)
        self.assertEqual(state.episodes[0].status, EpisodeStatus.UNRESOLVED_AT_RUN_END)
        self.assertIsNone(state.episodes[0].resolved_at_tick)

    def test_run_end_during_quiet_window_is_unverified(self) -> None:
        state = self.advance_ring(new_deadlock_state("run-end-quiet"), 1, 3)
        state = advance_deadlock_state(state, 4, cleared_ring(moved=True), config=self.config)
        state = finish_deadlock_state(state)
        self.assertEqual(state.episodes[0].status, EpisodeStatus.ENDED_UNVERIFIED)
        self.assertIn("RUN_ENDED_BEFORE_QUIET_WINDOW_COMPLETED", state.episodes[0].resolution_evidence)

    def test_same_cycle_is_deduplicated_across_ticks(self) -> None:
        state = self.advance_ring(new_deadlock_state("run-dedupe"), 1, 8)
        self.assertEqual(len(state.episodes), 1)
        self.assertEqual(state.episodes[0].first_observed_tick, 1)
        self.assertEqual(state.episodes[0].last_observed_tick, 8)

    def test_quiet_window_allows_later_cycle_to_create_new_episode(self) -> None:
        state = self.advance_ring(new_deadlock_state("run-repeat"), 1, 3)
        state = advance_deadlock_state(state, 4, cleared_ring(moved=True), config=self.config)
        state = advance_deadlock_state(state, 5, cleared_ring(moved=True), config=self.config)
        state = advance_deadlock_state(state, 6, ring(2), config=self.config)
        self.assertEqual(len(state.episodes), 2)
        self.assertNotEqual(state.episodes[0].episode_id, state.episodes[1].episode_id)

    def test_cycle_reappearing_before_quiet_window_reuses_episode(self) -> None:
        state = advance_deadlock_state(
            new_deadlock_state("run-transient"),
            1,
            ring(2),
            config=self.config,
        )
        episode_id = state.episodes[0].episode_id
        state = advance_deadlock_state(
            state,
            2,
            cleared_ring(moved=True),
            config=self.config,
        )
        state = advance_deadlock_state(state, 3, ring(2), config=self.config)
        self.assertEqual(len(state.episodes), 1)
        self.assertEqual(state.episodes[0].episode_id, episode_id)
        self.assertEqual(state.episodes[0].last_observed_tick, 3)

    def test_mitigation_action_changes_status_but_not_resolution(self) -> None:
        state = advance_deadlock_state(
            new_deadlock_state("run-mitigation"),
            1,
            ring(2),
            mitigation_actions=[MitigationAction(1, "R1", "YIELD")],
            config=self.config,
        )
        self.assertEqual(state.episodes[0].status, EpisodeStatus.MITIGATION_OBSERVED)
        self.assertIsNone(state.episodes[0].resolved_at_tick)

    def test_duplicate_tick_is_idempotent(self) -> None:
        state = advance_deadlock_state(new_deadlock_state("run-idempotent"), 1, ring(2))
        duplicate = advance_deadlock_state(state, 1, ring(2, wait_ticks=99))
        self.assertIs(duplicate, state)
        self.assertEqual(
            summarize_deadlock_metrics(duplicate).cumulative_blocked_robot_ticks,
            2,
        )

    def test_tick_gap_ends_episode_without_interpolating(self) -> None:
        state = advance_deadlock_state(new_deadlock_state("run-gap"), 1, ring(2), config=self.config)
        state = advance_deadlock_state(state, 3, ring(2), config=self.config)
        self.assertEqual(state.data_quality, DataQuality.INCOMPLETE)
        self.assertEqual(len(state.episodes), 2)
        self.assertEqual(state.episodes[0].status, EpisodeStatus.ENDED_UNVERIFIED)
        self.assertEqual(
            summarize_deadlock_metrics(state).cumulative_blocked_robot_ticks,
            4,
        )

    def test_missing_participant_prevents_verified_resolution_duration(self) -> None:
        state = self.advance_ring(new_deadlock_state("run-missing"), 1, 3)
        only_one = [robot("R1", "cell-3", None, waiting=False)]
        state = advance_deadlock_state(state, 4, only_one, config=self.config)
        state = advance_deadlock_state(state, 5, only_one, config=self.config)
        self.assertEqual(state.episodes[0].status, EpisodeStatus.ENDED_UNVERIFIED)
        self.assertEqual(state.episodes[0].data_quality, DataQuality.INCOMPLETE)
        metrics = summarize_deadlock_metrics(state)
        self.assertEqual(metrics.confirmed_cycle_duration_ticks, ())
        self.assertIsNone(metrics.average_resolution_ticks)

    def test_missing_current_cell_is_not_treated_as_real_movement(self) -> None:
        state = self.advance_ring(new_deadlock_state("run-missing-cell"), 1, 3)
        incomplete = [
            RobotSnapshot("R1", None, None, False, task_progress_token="phase-1"),
            robot("R2", "cell-2", None, waiting=False),
        ]
        state = advance_deadlock_state(state, 4, incomplete, config=self.config)
        state = advance_deadlock_state(state, 5, incomplete, config=self.config)
        episode = state.episodes[0]
        self.assertEqual(episode.status, EpisodeStatus.ENDED_UNVERIFIED)
        self.assertEqual(episode.data_quality, DataQuality.INCOMPLETE)
        self.assertFalse(any(item.startswith("ROBOT_MOVED") for item in episode.resolution_evidence))

    def test_episode_id_is_deterministic(self) -> None:
        first = advance_deadlock_state(new_deadlock_state("run-stable"), 7, ring(3))
        second = advance_deadlock_state(
            new_deadlock_state("run-stable"),
            7,
            list(reversed(ring(3))),
        )
        self.assertEqual(first.episodes[0].episode_id, second.episodes[0].episode_id)

    def test_input_objects_are_not_modified_and_are_immutable(self) -> None:
        snapshots = ring(2)
        original = list(snapshots)
        advance_deadlock_state(new_deadlock_state("run-pure"), 1, snapshots)
        self.assertEqual(snapshots, original)
        with self.assertRaises(FrozenInstanceError):
            snapshots[0].wait_ticks = 99  # type: ignore[misc]


class BlockingStatisticsTests(unittest.TestCase):
    def test_cumulative_blocked_robot_ticks_count_observed_ticks_only(self) -> None:
        state = new_deadlock_state("run-total")
        state = advance_deadlock_state(state, 1, ring(2))
        state = advance_deadlock_state(state, 2, ring(2))
        state = advance_deadlock_state(
            state,
            3,
            [
                robot("R1", "cell-1", None, waiting=False),
                robot("R2", "cell-2", None, waiting=False),
            ],
        )
        metrics = summarize_deadlock_metrics(state)
        self.assertEqual(metrics.cumulative_blocked_robot_ticks, 4)
        self.assertEqual(metrics.blocked_event_count, 2)

    def test_max_continuous_blocked_ticks_tracks_longest_streak(self) -> None:
        state = new_deadlock_state("run-max")
        for tick in range(1, 5):
            state = advance_deadlock_state(state, tick, ring(2))
        stats = {item.robot_id: item for item in state.robot_block_stats}
        self.assertEqual(stats["R1"].continuous_blocked_ticks, 4)
        self.assertEqual(stats["R1"].max_continuous_blocked_ticks, 4)
        self.assertEqual(stats["R1"].blocked_start_tick, 1)
        self.assertEqual(stats["R1"].blocker_robot_id, "R2")

    def test_blocked_end_tick_is_last_observed_wait_tick(self) -> None:
        state = new_deadlock_state("run-end-tick")
        state = advance_deadlock_state(state, 10, ring(2))
        state = advance_deadlock_state(state, 11, ring(2))
        state = advance_deadlock_state(
            state,
            12,
            [
                robot("R1", "cell-3", None, waiting=False),
                robot("R2", "cell-2", None, waiting=False),
            ],
        )
        stats = {item.robot_id: item for item in state.robot_block_stats}
        self.assertEqual(stats["R1"].blocked_end_tick, 11)
        self.assertEqual(stats["R1"].continuous_blocked_ticks, 0)

    def test_resolved_metrics_use_persistent_tick_as_duration_origin(self) -> None:
        config = DeadlockConfig(persistent_cycle_ticks=3, quiet_window_ticks=2)
        state = new_deadlock_state("run-duration")
        for tick in range(1, 4):
            state = advance_deadlock_state(state, tick, ring(2), config=config)
        state = advance_deadlock_state(state, 4, cleared_ring(moved=True), config=config)
        state = advance_deadlock_state(state, 5, cleared_ring(moved=True), config=config)
        metrics = summarize_deadlock_metrics(state)
        self.assertEqual(metrics.confirmed_cycle_duration_ticks, (2,))
        self.assertEqual(metrics.average_resolution_ticks, 2.0)
        self.assertTrue(metrics.hotspot_cells)

    def test_configuration_rejects_non_positive_thresholds(self) -> None:
        with self.assertRaises(ValueError):
            DeadlockConfig(persistent_cycle_ticks=0)
        with self.assertRaises(ValueError):
            DeadlockConfig(quiet_window_ticks=0)


if __name__ == "__main__":
    unittest.main()
