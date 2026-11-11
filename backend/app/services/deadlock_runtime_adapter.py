"""Runtime observation adapter for the deadlock episode contract.

The adapter records authoritative movement-attempt evidence, converts it to the
P2-1 immutable data contract, and serializes bounded diagnostics.  It does not
change robot, path, task, inventory, or scheduling state and does not implement
a second cycle-detection algorithm.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import time
from typing import Any, Collection, Sequence

from .deadlock_episodes import (
    DataQuality,
    DeadlockConfig,
    DeadlockEpisode,
    DeadlockState,
    EpisodeStatus,
    MitigationAction,
    ResourceType,
    RobotSnapshot,
    advance_deadlock_state,
    finish_deadlock_state,
    new_deadlock_state,
    summarize_deadlock_metrics,
)


CONTRACT_VERSION = "deadlock-episodes-v1"
DIAGNOSTICS_NAMESPACE = "deadlockDiagnosticsV1"
MAX_RECENT_CLOSED_EPISODES = 50
MAX_BLOCK_EVIDENCE = 100
MAX_DIAGNOSTIC_FAILURES = 10


@dataclass(frozen=True, slots=True)
class RuntimeBlockEvidence:
    tick: int
    robot_id: str
    current_cell_id: str | None
    attempted_next_cell_id: str | None
    blocker_robot_id: str | None
    resource_type: ResourceType
    reason: str
    task_id: str | None
    current_layer: int | None
    current_floor: str | None


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    event_type: str
    tick: int
    transition_key: str
    payload: dict[str, Any]


class DeadlockRuntimeObserver:
    """One in-memory detector instance for one run generation."""

    def __init__(
        self,
        run_id: str,
        generation: int,
        topology: dict[str, Any] | None,
        *,
        initial_tick: int = 0,
        config: DeadlockConfig = DeadlockConfig(),
    ) -> None:
        if generation < 1:
            raise ValueError("generation must be at least 1")
        self.run_id = run_id
        self.generation = generation
        self.run_instance_id = f"{run_id}:generation-{generation}"
        self.config = config
        self.valid_cell_ids = self._valid_cell_ids(topology or {})
        self.tracker_state = new_deadlock_state(self.run_instance_id)
        self.current_tick: int | None = None
        self._tick_start_positions: dict[str, str | None] = {}
        self._block_attempts: dict[str, RuntimeBlockEvidence] = {}
        self._mitigation_actions: set[MitigationAction] = set()
        self._pending_events: dict[str, LifecycleEvent] = {}
        self._acknowledged_event_keys: set[str] = set()
        self._last_acknowledged_transition_key: str | None = None
        self._last_block_evidence: tuple[RuntimeBlockEvidence, ...] = ()
        self._diagnostic_failures: list[dict[str, Any]] = []
        self._recovered_counters: dict[str, int] = {}
        self._recovered_unverified_episodes: list[dict[str, Any]] = []
        self._ended = False
        self._end_reason: str | None = None
        self._historical_incomplete = initial_tick > 0
        self._processed_tick_count = 0
        self._total_detection_ms = 0.0
        self._last_detection_ms = 0.0
        self._max_detection_ms = 0.0

    @property
    def ended(self) -> bool:
        return self._ended

    def begin_tick(self, tick: int, runtime_state: dict[str, Any]) -> None:
        if self._ended:
            return
        if self.current_tick == tick:
            return
        self.current_tick = tick
        self._block_attempts = {}
        self._mitigation_actions = set()
        self._tick_start_positions = {
            str(robot.get("id")): self._clean(robot.get("currentCellId"))
            for robot in runtime_state.get("robots", [])
            if robot.get("id")
        }

    def observe_block(self, evidence: RuntimeBlockEvidence) -> None:
        if self._ended or evidence.tick != self.current_tick:
            return
        previous = self._block_attempts.get(evidence.robot_id)
        if previous is None:
            self._block_attempts[evidence.robot_id] = evidence
            return
        if previous != evidence:
            self._historical_incomplete = True
            self._diagnostic_failures.append(
                {
                    "tick": evidence.tick,
                    "type": "MULTIPLE_BLOCK_ATTEMPTS",
                    "message": f"Multiple distinct block attempts for {evidence.robot_id}",
                }
            )
            self._diagnostic_failures = self._diagnostic_failures[-MAX_DIAGNOSTIC_FAILURES:]

    def observe_mitigation(
        self,
        tick: int,
        robot_id: str,
        action: str,
        details: str | None = None,
    ) -> None:
        if self._ended or tick != self.current_tick:
            return
        self._mitigation_actions.add(
            MitigationAction(tick=tick, robot_id=robot_id, action=action, details=details)
        )

    def process_tick(
        self,
        tick: int,
        runtime_state: dict[str, Any],
    ) -> tuple[LifecycleEvent, ...]:
        if self._ended:
            return self.pending_events()
        if self.current_tick != tick:
            self.begin_tick(tick, runtime_state)
        started = time.perf_counter()
        before = self.tracker_state
        snapshots = self._build_snapshots(runtime_state)
        self.tracker_state = advance_deadlock_state(
            before,
            tick,
            snapshots,
            mitigation_actions=tuple(
                sorted(
                    self._mitigation_actions,
                    key=lambda item: (item.tick, item.robot_id, item.action, item.details or ""),
                )
            ),
            config=self.config,
            valid_cell_ids=self.valid_cell_ids or None,
        )
        self._queue_transitions(before, self.tracker_state, tick)
        self._last_block_evidence = tuple(
            sorted(
                self._block_attempts.values(),
                key=lambda item: (item.robot_id, item.reason),
            )
        )[-MAX_BLOCK_EVIDENCE:]
        self.current_tick = None
        self._block_attempts = {}
        self._mitigation_actions = set()
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self._processed_tick_count += 1
        self._total_detection_ms += elapsed_ms
        self._last_detection_ms = elapsed_ms
        self._max_detection_ms = max(self._max_detection_ms, elapsed_ms)
        return self.pending_events()

    def finish(self, tick: int, reason: str) -> tuple[LifecycleEvent, ...]:
        if self._ended:
            return self.pending_events()
        before = self.tracker_state
        self.tracker_state = finish_deadlock_state(before)
        self._queue_transitions(before, self.tracker_state, tick)
        self._ended = True
        self._end_reason = reason
        self.current_tick = None
        self._block_attempts = {}
        self._mitigation_actions = set()
        return self.pending_events()

    def record_failure(self, tick: int, error: Exception) -> LifecycleEvent:
        error_type = type(error).__name__
        message = str(error) or error_type
        self._historical_incomplete = True
        issue = f"RUNTIME_ADAPTER_FAILURE:{tick}:{error_type}"
        if issue not in self.tracker_state.quality_issues:
            self.tracker_state = replace(
                self.tracker_state,
                data_quality=DataQuality.INCOMPLETE,
                quality_issues=self.tracker_state.quality_issues + (issue,),
            )
        failure = {"tick": tick, "type": error_type, "message": message}
        self._diagnostic_failures.append(failure)
        self._diagnostic_failures = self._diagnostic_failures[-MAX_DIAGNOSTIC_FAILURES:]
        transition_key = f"{self.run_instance_id}:diagnostic-failure:{tick}:{error_type}"
        event = LifecycleEvent(
            event_type="deadlock_diagnostics_failed",
            tick=tick,
            transition_key=transition_key,
            payload={
                "contractVersion": CONTRACT_VERSION,
                "runId": self.run_id,
                "runInstanceId": self.run_instance_id,
                "generation": self.generation,
                "transitionKey": transition_key,
                "errorType": error_type,
                "message": message,
                "observationCompleteness": DataQuality.INCOMPLETE.value,
            },
        )
        self._queue_event(event)
        return event

    def mark_incomplete_recovery(
        self,
        saved_diagnostics: dict[str, Any],
        tick: int,
    ) -> None:
        """Carry bounded counters forward and close unrecoverable active episodes."""

        self._historical_incomplete = True
        issue = f"PROCESS_RESTART_RECOVERY_INCOMPLETE:{tick}"
        self.tracker_state = replace(
            self.tracker_state,
            data_quality=DataQuality.INCOMPLETE,
            quality_issues=self.tracker_state.quality_issues + (issue,),
        )
        counter_names = (
            "blockedRobotTicks",
            "blockEpisodeCount",
            "suspectedCycleCount",
            "persistentCycleCount",
            "resolvedCycleCount",
            "unresolvedCycleCount",
            "unverifiedCycleCount",
        )
        self._recovered_counters = {
            name: max(0, int(saved_diagnostics.get(name) or 0))
            for name in counter_names
        }
        active = saved_diagnostics.get("activeEpisodes") or []
        for raw_episode in active:
            if not isinstance(raw_episode, dict) or not raw_episode.get("episodeId"):
                continue
            episode = dict(raw_episode)
            episode["status"] = EpisodeStatus.ENDED_UNVERIFIED.value
            episode["resolvedAtTick"] = None
            episode["dataQuality"] = DataQuality.INCOMPLETE.value
            evidence = list(episode.get("resolutionEvidence") or [])
            if "PROCESS_RESTART_WITHOUT_CONTINUOUS_TICK_STATE" not in evidence:
                evidence.append("PROCESS_RESTART_WITHOUT_CONTINUOUS_TICK_STATE")
            episode["resolutionEvidence"] = evidence
            self._recovered_unverified_episodes.append(episode)
            transition_key = (
                f"{self.run_instance_id}:{episode['episodeId']}:"
                f"deadlock_episode_unverified:recovery-{tick}"
            )
            self._queue_event(
                LifecycleEvent(
                    event_type="deadlock_episode_unverified",
                    tick=tick,
                    transition_key=transition_key,
                    payload={
                        "contractVersion": CONTRACT_VERSION,
                        "runId": self.run_id,
                        "runInstanceId": self.run_instance_id,
                        "generation": self.generation,
                        "transitionKey": transition_key,
                        "episode": episode,
                        "recoveryReason": "PROCESS_RESTART_WITHOUT_CONTINUOUS_TICK_STATE",
                    },
                )
            )
        self._recovered_counters["unverifiedCycleCount"] = (
            self._recovered_counters.get("unverifiedCycleCount", 0)
            + len(self._recovered_unverified_episodes)
        )
        self._diagnostic_failures.append(
            {
                "tick": tick,
                "type": "PROCESS_RESTART_RECOVERY_INCOMPLETE",
                "message": "Open episodes were ended unverified because continuous in-memory Tick state was unavailable.",
            }
        )
        self._diagnostic_failures = self._diagnostic_failures[-MAX_DIAGNOSTIC_FAILURES:]

    def pending_events(self) -> tuple[LifecycleEvent, ...]:
        return tuple(self._pending_events.values())

    def acknowledge_event(self, transition_key: str) -> None:
        if transition_key not in self._pending_events:
            return
        del self._pending_events[transition_key]
        self._acknowledged_event_keys.add(transition_key)
        self._last_acknowledged_transition_key = transition_key

    def diagnostics_payload(self) -> dict[str, Any]:
        metrics = summarize_deadlock_metrics(self.tracker_state)
        active_ids = {item.episode_id for item in self.tracker_state.active_cycles}
        active = [
            self._episode_payload(episode)
            for episode in self.tracker_state.episodes
            if episode.episode_id in active_ids
        ]
        closed = (
            self._recovered_unverified_episodes
            + [
                self._episode_payload(episode)
                for episode in self.tracker_state.episodes
                if episode.episode_id not in active_ids
            ]
        )[-MAX_RECENT_CLOSED_EPISODES:]
        observation_quality = (
            DataQuality.INCOMPLETE
            if self._historical_incomplete
            or self.tracker_state.data_quality is DataQuality.INCOMPLETE
            else DataQuality.COMPLETE
        )
        average_ms = (
            self._total_detection_ms / self._processed_tick_count
            if self._processed_tick_count
            else 0.0
        )
        payload: dict[str, Any] = {
            "contractVersion": CONTRACT_VERSION,
            "runId": self.run_id,
            "runInstanceId": self.run_instance_id,
            "generation": self.generation,
            "lastProcessedTick": self.tracker_state.last_tick,
            "observationCompleteness": observation_quality.value,
            "qualityIssues": list(self.tracker_state.quality_issues),
            "blockedRobotTicks": self._recovered_counters.get("blockedRobotTicks", 0) + metrics.cumulative_blocked_robot_ticks,
            "blockEpisodeCount": self._recovered_counters.get("blockEpisodeCount", 0) + metrics.blocked_event_count,
            "suspectedCycleCount": self._recovered_counters.get("suspectedCycleCount", 0) + metrics.suspected_cycle_count,
            "persistentCycleCount": self._recovered_counters.get("persistentCycleCount", 0) + metrics.persistent_wait_cycle_count,
            "resolvedCycleCount": self._recovered_counters.get("resolvedCycleCount", 0) + metrics.resolved_count,
            "unresolvedCycleCount": self._recovered_counters.get("unresolvedCycleCount", 0) + metrics.unresolved_at_run_end_count,
            "unverifiedCycleCount": self._recovered_counters.get("unverifiedCycleCount", 0) + metrics.ended_unverified_count,
            "activeEpisodes": active,
            "recentClosedEpisodes": closed,
            "robotBlockStats": [self._robot_stats_payload(item) for item in self.tracker_state.robot_block_stats],
            "lastBlockedObservations": [self._block_payload(item) for item in self._last_block_evidence],
            "diagnosticFailures": list(self._diagnostic_failures),
            "eventPersistence": {
                "guarantee": "BEST_EFFORT_NOT_EXACTLY_ONCE",
                "lastAcknowledgedTransitionKey": self._last_acknowledged_transition_key,
                "pendingTransitionCount": len(self._pending_events),
            },
            "recovery": {
                "mode": "CURRENT_PROCESS_MEMORY_V1",
                "historicalBackfill": False,
                "limitation": "Process restart cannot reconstruct missing per-tick evidence from legacy snapshots.",
            },
            "performance": {
                "processedTicks": self._processed_tick_count,
                "lastDetectionMs": round(self._last_detection_ms, 6),
                "averageDetectionMs": round(average_ms, 6),
                "maxDetectionMs": round(self._max_detection_ms, 6),
            },
            "ended": self._ended,
            "endReason": self._end_reason,
        }
        payload["stateStoreBytes"] = len(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        return payload

    def new_generation(self, topology: dict[str, Any] | None) -> DeadlockRuntimeObserver:
        return DeadlockRuntimeObserver(
            self.run_id,
            self.generation + 1,
            topology,
            initial_tick=0,
            config=self.config,
        )

    def _build_snapshots(self, runtime_state: dict[str, Any]) -> tuple[RobotSnapshot, ...]:
        robots = {
            str(robot.get("id")): robot
            for robot in runtime_state.get("robots", [])
            if robot.get("id")
        }
        tasks = {
            str(task.get("id")): task
            for task in runtime_state.get("tasks", [])
            if task.get("id")
        }
        own_attempt_positions = {
            robot_id: self._clean(evidence.current_cell_id)
            for robot_id, evidence in self._block_attempts.items()
        }
        blocker_cells: dict[str, set[str]] = {}
        for evidence in self._block_attempts.values():
            if evidence.blocker_robot_id and evidence.attempted_next_cell_id:
                blocker_cells.setdefault(evidence.blocker_robot_id, set()).add(
                    evidence.attempted_next_cell_id
                )
        ambiguous_blockers = {
            robot_id
            for robot_id, cells in blocker_cells.items()
            if len(cells | ({own_attempt_positions[robot_id]} if own_attempt_positions.get(robot_id) else set())) > 1
        }
        observed_positions = {
            robot_id: self._clean(robot.get("currentCellId"))
            for robot_id, robot in robots.items()
        }
        observed_positions.update(own_attempt_positions)
        for robot_id, cells in blocker_cells.items():
            if robot_id not in ambiguous_blockers and len(cells) == 1:
                observed_positions[robot_id] = next(iter(cells))

        snapshots: list[RobotSnapshot] = []
        for robot_id in sorted(robots):
            robot = robots[robot_id]
            attempt = self._block_attempts.get(robot_id)
            task_id = attempt.task_id if attempt else self._clean(robot.get("currentTaskId"))
            progress_token = self._task_progress_token(tasks.get(task_id or ""), task_id)
            if attempt is not None:
                blocker_id = (
                    attempt.blocker_robot_id
                    if attempt.blocker_robot_id not in ambiguous_blockers
                    else None
                )
                resource_type = (
                    attempt.resource_type
                    if blocker_id is not None or attempt.resource_type is not ResourceType.ROBOT
                    else ResourceType.UNKNOWN_BLOCKER
                )
                snapshots.append(
                    RobotSnapshot(
                        robot_id=robot_id,
                        current_cell_id=attempt.current_cell_id,
                        next_cell_id=attempt.attempted_next_cell_id,
                        is_waiting=True,
                        wait_ticks=max(1, int(robot.get("waitTicks") or 0)),
                        declared_blocker_robot_id=blocker_id,
                        blocked_cell_id=attempt.attempted_next_cell_id,
                        wait_resource_type=resource_type,
                        wait_resource_id=(
                            blocker_id
                            if blocker_id is not None
                            else attempt.reason
                        ),
                        task_progress_token=progress_token,
                    )
                )
                continue

            path = robot.get("path") or []
            final_blocked = robot.get("status") == "blocked"
            yield_wait = int(robot.get("yieldTicks") or 0) > 0
            snapshots.append(
                RobotSnapshot(
                    robot_id=robot_id,
                    current_cell_id=observed_positions.get(robot_id) or self._clean(robot.get("currentCellId")),
                    next_cell_id=self._clean(path[1]) if len(path) > 1 else None,
                    is_waiting=final_blocked,
                    wait_ticks=max(0, int(robot.get("waitTicks") or 0)),
                    wait_resource_type=(
                        ResourceType.OTHER
                        if final_blocked and yield_wait
                        else ResourceType.UNKNOWN_BLOCKER
                        if final_blocked
                        else None
                    ),
                    wait_resource_id="ACTIVE_YIELD_WAIT" if final_blocked and yield_wait else None,
                    task_progress_token=progress_token,
                )
            )
        return tuple(snapshots)

    def _queue_transitions(
        self,
        before: DeadlockState,
        after: DeadlockState,
        tick: int,
    ) -> None:
        previous = {episode.episode_id: episode for episode in before.episodes}
        for episode in after.episodes:
            old = previous.get(episode.episode_id)
            if old is None:
                self._queue_episode_event("deadlock_episode_started", tick, episode, episode.suspected_at_tick)
            if episode.persistent_at_tick is not None and (
                old is None or old.persistent_at_tick is None
            ):
                self._queue_episode_event(
                    "deadlock_episode_persistent",
                    tick,
                    episode,
                    episode.persistent_at_tick,
                )
            old_actions = set(old.mitigation_actions) if old else set()
            for action in episode.mitigation_actions:
                if action in old_actions:
                    continue
                self._queue_episode_event(
                    "deadlock_mitigation_observed",
                    action.tick,
                    episode,
                    action.tick,
                    extra={
                        "mitigation": {
                            "robotId": action.robot_id,
                            "action": action.action,
                            "details": action.details,
                        }
                    },
                )
            if old is not None and old.status == episode.status:
                continue
            if episode.status is EpisodeStatus.RESOLVED:
                self._queue_episode_event(
                    "deadlock_episode_resolved",
                    tick,
                    episode,
                    episode.resolved_at_tick or tick,
                )
            elif episode.status is EpisodeStatus.ENDED_UNVERIFIED:
                self._queue_episode_event(
                    "deadlock_episode_unverified",
                    tick,
                    episode,
                    tick,
                )
            elif episode.status is EpisodeStatus.UNRESOLVED_AT_RUN_END:
                self._queue_episode_event(
                    "deadlock_episode_unresolved",
                    tick,
                    episode,
                    tick,
                )

    def _queue_episode_event(
        self,
        event_type: str,
        tick: int,
        episode: DeadlockEpisode,
        milestone_tick: int,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        transition_key = (
            f"{self.run_instance_id}:{episode.episode_id}:{event_type}:{milestone_tick}"
        )
        payload = {
            "contractVersion": CONTRACT_VERSION,
            "runId": self.run_id,
            "runInstanceId": self.run_instance_id,
            "generation": self.generation,
            "transitionKey": transition_key,
            "episode": self._episode_payload(episode),
        }
        if extra:
            payload.update(extra)
        self._queue_event(
            LifecycleEvent(
                event_type=event_type,
                tick=tick,
                transition_key=transition_key,
                payload=payload,
            )
        )

    def _queue_event(self, event: LifecycleEvent) -> None:
        if event.transition_key in self._acknowledged_event_keys:
            return
        self._pending_events.setdefault(event.transition_key, event)

    @staticmethod
    def _valid_cell_ids(topology: dict[str, Any]) -> frozenset[str]:
        return frozenset(
            str(cell.get("id"))
            for cell in topology.get("cells", [])
            if cell.get("id")
        )

    @staticmethod
    def _clean(value: Any) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @staticmethod
    def _task_progress_token(task: dict[str, Any] | None, task_id: str | None) -> str | None:
        if task is None and task_id is None:
            return None
        payload = {
            "taskId": task_id,
            "status": (task or {}).get("status"),
            "phase": (task or {}).get("phase"),
            "picked": (task or {}).get("picked"),
            "completedTick": (task or {}).get("completedTick"),
            "remainingStageTicks": (task or {}).get("remainingStageTicks"),
            "returnLineIndex": (task or {}).get("returnLineIndex"),
        }
        return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _edge_payload(edge: Any) -> dict[str, Any]:
        return {
            "waiterRobotId": edge.waiter_robot_id,
            "blockerRobotId": edge.blocker_robot_id,
            "resourceType": edge.resource_type.value,
            "resourceId": edge.resource_id,
            "blockedCellId": edge.blocked_cell_id,
            "nextCellId": edge.next_cell_id,
            "evidence": edge.evidence,
        }

    @classmethod
    def _episode_payload(cls, episode: DeadlockEpisode) -> dict[str, Any]:
        return {
            "episodeId": episode.episode_id,
            "detectorRunId": episode.run_id,
            "firstObservedTick": episode.first_observed_tick,
            "suspectedAtTick": episode.suspected_at_tick,
            "persistentAtTick": episode.persistent_at_tick,
            "resolvedAtTick": episode.resolved_at_tick,
            "lastObservedTick": episode.last_observed_tick,
            "participantRobotIds": list(episode.participant_robot_ids),
            "waitingEdges": [cls._edge_payload(edge) for edge in episode.waiting_edges],
            "involvedCellIds": list(episode.involved_cell_ids),
            "maxWaitTicks": episode.max_wait_ticks,
            "mitigationActions": [
                {
                    "tick": action.tick,
                    "robotId": action.robot_id,
                    "action": action.action,
                    "details": action.details,
                }
                for action in episode.mitigation_actions
            ],
            "status": episode.status.value,
            "resolutionEvidence": list(episode.resolution_evidence),
            "dataQuality": episode.data_quality.value,
            "cycleFingerprint": episode.cycle_fingerprint,
        }

    @staticmethod
    def _robot_stats_payload(item: Any) -> dict[str, Any]:
        return {
            "robotId": item.robot_id,
            "blockedStartTick": item.blocked_start_tick,
            "blockedEndTick": item.blocked_end_tick,
            "continuousBlockedTicks": item.continuous_blocked_ticks,
            "maxContinuousBlockedTicks": item.max_continuous_blocked_ticks,
            "cumulativeBlockedRobotTicks": item.cumulative_blocked_robot_ticks,
            "blockerRobotId": item.blocker_robot_id,
            "blockedCellId": item.blocked_cell_id,
            "nextCellId": item.next_cell_id,
            "blockedEventCount": item.blocked_event_count,
            "dataQuality": item.data_quality.value,
        }

    @staticmethod
    def _block_payload(item: RuntimeBlockEvidence) -> dict[str, Any]:
        return {
            "tick": item.tick,
            "robotId": item.robot_id,
            "currentCellId": item.current_cell_id,
            "attemptedNextCellId": item.attempted_next_cell_id,
            "blockerRobotId": item.blocker_robot_id,
            "resourceType": item.resource_type.value,
            "reason": item.reason,
            "taskId": item.task_id,
            "currentLayer": item.current_layer,
            "currentFloor": item.current_floor,
        }
