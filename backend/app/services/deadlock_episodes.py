"""Pure wait-for graph and deadlock episode lifecycle primitives.

This module deliberately has no database, FastAPI, runtime, or simulation imports.
It consumes immutable per-tick observations and returns a new immutable state so it
can be used by offline replay tests before any runtime integration is attempted.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import json
from typing import Collection, Iterable, Sequence


class EpisodeStatus(str, Enum):
    OBSERVED_BLOCK = "OBSERVED_BLOCK"
    SUSPECTED_CYCLE = "SUSPECTED_CYCLE"
    PERSISTENT_WAIT_CYCLE = "PERSISTENT_WAIT_CYCLE"
    MITIGATION_OBSERVED = "MITIGATION_OBSERVED"
    RESOLVED = "RESOLVED"
    UNRESOLVED_AT_RUN_END = "UNRESOLVED_AT_RUN_END"
    ENDED_UNVERIFIED = "ENDED_UNVERIFIED"


class ResourceType(str, Enum):
    ROBOT = "ROBOT"
    UNKNOWN_BLOCKER = "UNKNOWN_BLOCKER"
    RESERVATION = "RESERVATION"
    CONVEYOR = "CONVEYOR"
    DEVICE = "DEVICE"
    LIFT = "LIFT"
    SHUTTLE_TRACK = "SHUTTLE_TRACK"
    OTHER = "OTHER"


class DataQuality(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True, slots=True)
class DeadlockConfig:
    persistent_cycle_ticks: int = 3
    quiet_window_ticks: int = 2

    def __post_init__(self) -> None:
        if self.persistent_cycle_ticks < 1:
            raise ValueError("persistent_cycle_ticks must be at least 1")
        if self.quiet_window_ticks < 1:
            raise ValueError("quiet_window_ticks must be at least 1")


@dataclass(frozen=True, slots=True)
class RobotSnapshot:
    robot_id: str
    current_cell_id: str | None
    next_cell_id: str | None
    is_waiting: bool
    wait_ticks: int = 0
    declared_blocker_robot_id: str | None = None
    blocked_cell_id: str | None = None
    wait_resource_type: ResourceType | None = None
    wait_resource_id: str | None = None
    task_progress_token: str | None = None

    def __post_init__(self) -> None:
        if not self.robot_id.strip():
            raise ValueError("robot_id must not be empty")
        if self.wait_ticks < 0:
            raise ValueError("wait_ticks must not be negative")


@dataclass(frozen=True, slots=True)
class MitigationAction:
    tick: int
    robot_id: str
    action: str
    details: str | None = None

    def __post_init__(self) -> None:
        if self.tick < 0:
            raise ValueError("mitigation tick must not be negative")
        if not self.robot_id.strip() or not self.action.strip():
            raise ValueError("mitigation robot_id and action must not be empty")


@dataclass(frozen=True, slots=True)
class WaitEdge:
    waiter_robot_id: str
    blocker_robot_id: str | None
    resource_type: ResourceType
    resource_id: str | None
    blocked_cell_id: str | None
    next_cell_id: str | None
    evidence: str

    @property
    def is_verified_robot_edge(self) -> bool:
        return (
            self.resource_type is ResourceType.ROBOT
            and self.blocker_robot_id is not None
            and self.blocker_robot_id != self.waiter_robot_id
        )

    def canonical_tuple(self) -> tuple[str, ...]:
        return (
            self.waiter_robot_id,
            self.blocker_robot_id or "",
            self.resource_type.value,
            self.resource_id or "",
            self.blocked_cell_id or "",
            self.next_cell_id or "",
            self.evidence,
        )


@dataclass(frozen=True, slots=True)
class WaitForGraph:
    edges: tuple[WaitEdge, ...]

    @property
    def robot_edges(self) -> tuple[WaitEdge, ...]:
        return tuple(edge for edge in self.edges if edge.is_verified_robot_edge)

    @property
    def unknown_edges(self) -> tuple[WaitEdge, ...]:
        return tuple(
            edge for edge in self.edges if edge.resource_type is ResourceType.UNKNOWN_BLOCKER
        )


@dataclass(frozen=True, slots=True)
class WaitCycle:
    fingerprint: str
    participant_robot_ids: tuple[str, ...]
    waiting_edges: tuple[WaitEdge, ...]
    involved_cell_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DeadlockEpisode:
    episode_id: str
    run_id: str
    first_observed_tick: int
    suspected_at_tick: int
    persistent_at_tick: int | None
    resolved_at_tick: int | None
    last_observed_tick: int
    participant_robot_ids: tuple[str, ...]
    waiting_edges: tuple[WaitEdge, ...]
    involved_cell_ids: tuple[str, ...]
    max_wait_ticks: int
    mitigation_actions: tuple[MitigationAction, ...]
    status: EpisodeStatus
    resolution_evidence: tuple[str, ...]
    data_quality: DataQuality
    cycle_fingerprint: str


@dataclass(frozen=True, slots=True)
class RobotBlockStats:
    robot_id: str
    blocked_start_tick: int | None = None
    blocked_end_tick: int | None = None
    continuous_blocked_ticks: int = 0
    max_continuous_blocked_ticks: int = 0
    cumulative_blocked_robot_ticks: int = 0
    blocker_robot_id: str | None = None
    blocked_cell_id: str | None = None
    next_cell_id: str | None = None
    blocked_event_count: int = 0
    last_observed_tick: int | None = None
    status: EpisodeStatus | None = None
    data_quality: DataQuality = DataQuality.COMPLETE


@dataclass(frozen=True, slots=True)
class _ActiveCycle:
    fingerprint: str
    episode_id: str
    consecutive_cycle_ticks: int
    last_cycle_tick: int
    absent_ticks: int
    last_participant_state: tuple[tuple[str, str | None, str | None], ...]
    pending_resolution_evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DeadlockState:
    run_id: str
    episodes: tuple[DeadlockEpisode, ...] = ()
    active_cycles: tuple[_ActiveCycle, ...] = ()
    robot_block_stats: tuple[RobotBlockStats, ...] = ()
    last_tick: int | None = None
    data_quality: DataQuality = DataQuality.COMPLETE
    quality_issues: tuple[str, ...] = ()
    ended: bool = False


@dataclass(frozen=True, slots=True)
class DeadlockMetrics:
    blocked_event_count: int
    cumulative_blocked_robot_ticks: int
    max_continuous_blocked_ticks: int
    suspected_cycle_count: int
    persistent_wait_cycle_count: int
    resolved_count: int
    unresolved_at_run_end_count: int
    ended_unverified_count: int
    confirmed_cycle_duration_ticks: tuple[int, ...]
    average_resolution_ticks: float | None
    hotspot_cells: tuple[tuple[str, int], ...]
    data_quality: DataQuality


def new_deadlock_state(run_id: str) -> DeadlockState:
    if not run_id.strip():
        raise ValueError("run_id must not be empty")
    return DeadlockState(run_id=run_id)


def build_wait_for_graph(
    snapshots: Sequence[RobotSnapshot],
    *,
    valid_cell_ids: Collection[str] | None = None,
) -> WaitForGraph:
    """Build verified robot edges from next-cell occupancy and explicit resource edges."""

    ordered = _validate_and_sort_snapshots(snapshots)
    valid_cells = frozenset(valid_cell_ids) if valid_cell_ids is not None else None
    occupants: dict[str, list[str]] = {}
    for snapshot in ordered:
        cell_id = _clean(snapshot.current_cell_id)
        if cell_id is None or (valid_cells is not None and cell_id not in valid_cells):
            continue
        occupants.setdefault(cell_id, []).append(snapshot.robot_id)

    edges: set[WaitEdge] = set()
    for snapshot in ordered:
        if not snapshot.is_waiting:
            continue
        next_cell_id = _clean(snapshot.next_cell_id)
        blocked_cell_id = _clean(snapshot.blocked_cell_id) or next_cell_id
        if next_cell_id is None or (
            valid_cells is not None and next_cell_id not in valid_cells
        ):
            edges.add(
                _unknown_edge(
                    snapshot,
                    blocked_cell_id,
                    next_cell_id,
                    "INVALID_OR_MISSING_NEXT_CELL",
                )
            )
            continue

        resource_type = snapshot.wait_resource_type
        if resource_type is not None and resource_type not in {
            ResourceType.ROBOT,
            ResourceType.UNKNOWN_BLOCKER,
        }:
            edges.add(
                WaitEdge(
                    waiter_robot_id=snapshot.robot_id,
                    blocker_robot_id=None,
                    resource_type=resource_type,
                    resource_id=_clean(snapshot.wait_resource_id),
                    blocked_cell_id=blocked_cell_id,
                    next_cell_id=next_cell_id,
                    evidence="EXPLICIT_RESOURCE_WAIT",
                )
            )
            continue

        verified_occupants = sorted(
            robot_id for robot_id in occupants.get(next_cell_id, ())
            if robot_id != snapshot.robot_id
        )
        if len(verified_occupants) == 1:
            blocker_id = verified_occupants[0]
            edges.add(
                WaitEdge(
                    waiter_robot_id=snapshot.robot_id,
                    blocker_robot_id=blocker_id,
                    resource_type=ResourceType.ROBOT,
                    resource_id=blocker_id,
                    blocked_cell_id=blocked_cell_id,
                    next_cell_id=next_cell_id,
                    evidence="NEXT_CELL_OCCUPANCY",
                )
            )
            continue
        if len(verified_occupants) > 1:
            edges.add(
                _unknown_edge(
                    snapshot,
                    blocked_cell_id,
                    next_cell_id,
                    "AMBIGUOUS_CELL_OCCUPANCY",
                )
            )
            continue

        evidence = (
            "DECLARED_BLOCKER_UNVERIFIED"
            if _clean(snapshot.declared_blocker_robot_id)
            else "NO_VERIFIED_OCCUPANT"
        )
        edges.add(_unknown_edge(snapshot, blocked_cell_id, next_cell_id, evidence))

    return WaitForGraph(edges=tuple(sorted(edges, key=WaitEdge.canonical_tuple)))


def detect_wait_cycles(graph: WaitForGraph) -> tuple[WaitCycle, ...]:
    """Return deterministic strongly connected vehicle wait cycles."""

    adjacency: dict[str, set[str]] = {}
    for edge in graph.robot_edges:
        blocker_id = edge.blocker_robot_id
        if blocker_id is None or blocker_id == edge.waiter_robot_id:
            continue
        adjacency.setdefault(edge.waiter_robot_id, set()).add(blocker_id)
        adjacency.setdefault(blocker_id, set())

    index = 0
    indices: dict[str, int] = {}
    low_links: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[tuple[str, ...]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        low_links[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for neighbor in sorted(adjacency.get(node, ())):
            if neighbor not in indices:
                visit(neighbor)
                low_links[node] = min(low_links[node], low_links[neighbor])
            elif neighbor in on_stack:
                low_links[node] = min(low_links[node], indices[neighbor])
        if low_links[node] != indices[node]:
            return
        component: list[str] = []
        while stack:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node:
                break
        if len(component) >= 2:
            components.append(tuple(sorted(component)))

    for robot_id in sorted(adjacency):
        if robot_id not in indices:
            visit(robot_id)

    cycles: list[WaitCycle] = []
    for participants in components:
        participant_set = frozenset(participants)
        internal_edges = tuple(
            sorted(
                (
                    edge for edge in graph.robot_edges
                    if edge.waiter_robot_id in participant_set
                    and edge.blocker_robot_id in participant_set
                ),
                key=WaitEdge.canonical_tuple,
            )
        )
        if not internal_edges:
            continue
        cell_ids = tuple(
            sorted(
                {
                    cell_id
                    for edge in internal_edges
                    for cell_id in (edge.blocked_cell_id, edge.next_cell_id)
                    if cell_id
                }
            )
        )
        cycles.append(
            WaitCycle(
                fingerprint=_cycle_fingerprint(participants, internal_edges),
                participant_robot_ids=participants,
                waiting_edges=internal_edges,
                involved_cell_ids=cell_ids,
            )
        )
    return tuple(sorted(cycles, key=lambda cycle: cycle.fingerprint))


def advance_deadlock_state(
    state: DeadlockState,
    tick: int,
    snapshots: Sequence[RobotSnapshot],
    *,
    mitigation_actions: Sequence[MitigationAction] = (),
    config: DeadlockConfig = DeadlockConfig(),
    valid_cell_ids: Collection[str] | None = None,
) -> DeadlockState:
    """Consume one complete Tick and return a new tracker state."""

    if state.ended:
        raise ValueError("cannot advance an ended run")
    if tick < 0:
        raise ValueError("tick must not be negative")
    ordered_snapshots = _validate_and_sort_snapshots(snapshots)
    ordered_actions = tuple(
        sorted(mitigation_actions, key=lambda item: (item.tick, item.robot_id, item.action, item.details or ""))
    )
    if any(action.tick != tick for action in ordered_actions):
        raise ValueError("every mitigation action must belong to the processed tick")
    if state.last_tick is not None and tick < state.last_tick:
        raise ValueError("ticks must be processed in non-decreasing order")
    if state.last_tick == tick:
        return state

    working = state
    has_gap = state.last_tick is not None and tick != state.last_tick + 1
    if has_gap:
        working = _terminate_for_tick_gap(state, tick)

    graph = build_wait_for_graph(ordered_snapshots, valid_cell_ids=valid_cell_ids)
    cycles = detect_wait_cycles(graph)
    robot_stats, missing_robot = _update_robot_block_stats(
        working.robot_block_stats,
        ordered_snapshots,
        graph,
        tick,
    )
    issues = list(working.quality_issues)
    data_quality = working.data_quality
    if has_gap:
        issue = f"TICK_GAP:{state.last_tick}->{tick}"
        if issue not in issues:
            issues.append(issue)
        data_quality = DataQuality.INCOMPLETE
    if missing_robot:
        issue = f"ROBOT_OBSERVATION_MISSING_AT_TICK:{tick}"
        if issue not in issues:
            issues.append(issue)
        data_quality = DataQuality.INCOMPLETE

    episodes = {episode.episode_id: episode for episode in working.episodes}
    active = {item.fingerprint: item for item in working.active_cycles}
    cycle_by_fingerprint = {cycle.fingerprint: cycle for cycle in cycles}
    snapshot_by_id = {snapshot.robot_id: snapshot for snapshot in ordered_snapshots}

    for fingerprint, cycle in cycle_by_fingerprint.items():
        active_cycle = active.get(fingerprint)
        participant_actions = tuple(
            action for action in ordered_actions
            if action.robot_id in cycle.participant_robot_ids
        )
        max_wait_ticks = max(
            (snapshot_by_id[robot_id].wait_ticks for robot_id in cycle.participant_robot_ids),
            default=0,
        )
        participant_state = _participant_state(cycle.participant_robot_ids, snapshot_by_id)
        if active_cycle is None:
            episode_id = _episode_id(state.run_id, fingerprint, tick)
            episode = DeadlockEpisode(
                episode_id=episode_id,
                run_id=state.run_id,
                first_observed_tick=tick,
                suspected_at_tick=tick,
                persistent_at_tick=tick if config.persistent_cycle_ticks == 1 else None,
                resolved_at_tick=None,
                last_observed_tick=tick,
                participant_robot_ids=cycle.participant_robot_ids,
                waiting_edges=cycle.waiting_edges,
                involved_cell_ids=cycle.involved_cell_ids,
                max_wait_ticks=max_wait_ticks,
                mitigation_actions=participant_actions,
                status=(
                    EpisodeStatus.MITIGATION_OBSERVED
                    if participant_actions
                    else EpisodeStatus.PERSISTENT_WAIT_CYCLE
                    if config.persistent_cycle_ticks == 1
                    else EpisodeStatus.SUSPECTED_CYCLE
                ),
                resolution_evidence=(),
                data_quality=DataQuality.COMPLETE,
                cycle_fingerprint=fingerprint,
            )
            episodes[episode_id] = episode
            active[fingerprint] = _ActiveCycle(
                fingerprint=fingerprint,
                episode_id=episode_id,
                consecutive_cycle_ticks=1,
                last_cycle_tick=tick,
                absent_ticks=0,
                last_participant_state=participant_state,
            )
            continue

        episode = episodes[active_cycle.episode_id]
        consecutive_ticks = (
            active_cycle.consecutive_cycle_ticks + 1
            if active_cycle.last_cycle_tick == tick - 1
            else 1
        )
        persistent_at_tick = episode.persistent_at_tick
        if persistent_at_tick is None and consecutive_ticks >= config.persistent_cycle_ticks:
            persistent_at_tick = tick
        actions = _merge_actions(episode.mitigation_actions, participant_actions)
        if actions:
            status = EpisodeStatus.MITIGATION_OBSERVED
        elif persistent_at_tick is not None:
            status = EpisodeStatus.PERSISTENT_WAIT_CYCLE
        else:
            status = EpisodeStatus.SUSPECTED_CYCLE
        episodes[episode.episode_id] = replace(
            episode,
            persistent_at_tick=persistent_at_tick,
            last_observed_tick=tick,
            waiting_edges=cycle.waiting_edges,
            involved_cell_ids=tuple(sorted(set(episode.involved_cell_ids) | set(cycle.involved_cell_ids))),
            max_wait_ticks=max(episode.max_wait_ticks, max_wait_ticks),
            mitigation_actions=actions,
            status=status,
        )
        active[fingerprint] = replace(
            active_cycle,
            consecutive_cycle_ticks=consecutive_ticks,
            last_cycle_tick=tick,
            absent_ticks=0,
            last_participant_state=participant_state,
            pending_resolution_evidence=(),
        )

    for fingerprint in sorted(set(active) - set(cycle_by_fingerprint)):
        active_cycle = active[fingerprint]
        episode = episodes[active_cycle.episode_id]
        absent_ticks = active_cycle.absent_ticks + 1
        evidence, participant_complete = _progress_evidence(
            active_cycle.last_participant_state,
            snapshot_by_id,
            valid_cell_ids=valid_cell_ids,
        )
        pending_evidence = tuple(
            sorted(set(active_cycle.pending_resolution_evidence) | set(evidence))
        )
        participant_actions = tuple(
            action for action in ordered_actions
            if action.robot_id in episode.participant_robot_ids
        )
        actions = _merge_actions(episode.mitigation_actions, participant_actions)
        episode_quality = (
            episode.data_quality if participant_complete else DataQuality.INCOMPLETE
        )
        if absent_ticks >= config.quiet_window_ticks:
            if pending_evidence and episode_quality is DataQuality.COMPLETE:
                status = EpisodeStatus.RESOLVED
                resolved_at_tick: int | None = tick
                resolution_evidence = pending_evidence
            else:
                status = EpisodeStatus.ENDED_UNVERIFIED
                resolved_at_tick = None
                resolution_evidence = pending_evidence or (
                    "CYCLE_ABSENT_WITHOUT_PROGRESS_EVIDENCE",
                )
            episodes[episode.episode_id] = replace(
                episode,
                resolved_at_tick=resolved_at_tick,
                mitigation_actions=actions,
                status=status,
                resolution_evidence=resolution_evidence,
                data_quality=episode_quality,
            )
            del active[fingerprint]
        else:
            episodes[episode.episode_id] = replace(
                episode,
                mitigation_actions=actions,
                status=(
                    EpisodeStatus.MITIGATION_OBSERVED
                    if actions
                    else episode.status
                ),
                data_quality=episode_quality,
            )
            active[fingerprint] = replace(
                active_cycle,
                absent_ticks=absent_ticks,
                pending_resolution_evidence=pending_evidence,
            )

    return DeadlockState(
        run_id=state.run_id,
        episodes=tuple(sorted(episodes.values(), key=lambda item: (item.first_observed_tick, item.episode_id))),
        active_cycles=tuple(sorted(active.values(), key=lambda item: item.fingerprint)),
        robot_block_stats=robot_stats,
        last_tick=tick,
        data_quality=data_quality,
        quality_issues=tuple(issues),
        ended=False,
    )


def finish_deadlock_state(state: DeadlockState) -> DeadlockState:
    """Close every open episode without inventing additional Tick observations."""

    if state.ended:
        return state
    episodes = {episode.episode_id: episode for episode in state.episodes}
    for active_cycle in state.active_cycles:
        episode = episodes[active_cycle.episode_id]
        if active_cycle.absent_ticks:
            status = EpisodeStatus.ENDED_UNVERIFIED
            evidence = tuple(
                sorted(
                    set(active_cycle.pending_resolution_evidence)
                    | {"RUN_ENDED_BEFORE_QUIET_WINDOW_COMPLETED"}
                )
            )
        else:
            status = EpisodeStatus.UNRESOLVED_AT_RUN_END
            evidence = ("WAIT_CYCLE_PRESENT_AT_RUN_END",)
        episodes[episode.episode_id] = replace(
            episode,
            status=status,
            resolution_evidence=evidence,
        )
    return replace(
        state,
        episodes=tuple(sorted(episodes.values(), key=lambda item: (item.first_observed_tick, item.episode_id))),
        active_cycles=(),
        ended=True,
    )


def summarize_deadlock_metrics(state: DeadlockState) -> DeadlockMetrics:
    stats = state.robot_block_stats
    episodes = state.episodes
    persistent = tuple(episode for episode in episodes if episode.persistent_at_tick is not None)
    resolved = tuple(episode for episode in persistent if episode.status is EpisodeStatus.RESOLVED)
    durations = tuple(
        episode.resolved_at_tick - episode.persistent_at_tick
        for episode in resolved
        if episode.resolved_at_tick is not None and episode.persistent_at_tick is not None
    )
    hotspot_counts: dict[str, int] = {}
    for episode in persistent:
        for cell_id in episode.involved_cell_ids:
            hotspot_counts[cell_id] = hotspot_counts.get(cell_id, 0) + 1
    return DeadlockMetrics(
        blocked_event_count=sum(item.blocked_event_count for item in stats),
        cumulative_blocked_robot_ticks=sum(item.cumulative_blocked_robot_ticks for item in stats),
        max_continuous_blocked_ticks=max(
            (item.max_continuous_blocked_ticks for item in stats),
            default=0,
        ),
        suspected_cycle_count=len(episodes),
        persistent_wait_cycle_count=len(persistent),
        resolved_count=sum(episode.status is EpisodeStatus.RESOLVED for episode in persistent),
        unresolved_at_run_end_count=sum(
            episode.status is EpisodeStatus.UNRESOLVED_AT_RUN_END for episode in persistent
        ),
        ended_unverified_count=sum(
            episode.status is EpisodeStatus.ENDED_UNVERIFIED for episode in episodes
        ),
        confirmed_cycle_duration_ticks=durations,
        average_resolution_ticks=(sum(durations) / len(durations)) if durations else None,
        hotspot_cells=tuple(
            sorted(hotspot_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        data_quality=state.data_quality,
    )


def _validate_and_sort_snapshots(
    snapshots: Sequence[RobotSnapshot],
) -> tuple[RobotSnapshot, ...]:
    ordered = tuple(sorted(snapshots, key=lambda item: item.robot_id))
    robot_ids = [snapshot.robot_id for snapshot in ordered]
    if len(robot_ids) != len(set(robot_ids)):
        raise ValueError("robot snapshots must have unique robot_id values")
    return ordered


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _unknown_edge(
    snapshot: RobotSnapshot,
    blocked_cell_id: str | None,
    next_cell_id: str | None,
    evidence: str,
) -> WaitEdge:
    return WaitEdge(
        waiter_robot_id=snapshot.robot_id,
        blocker_robot_id=None,
        resource_type=ResourceType.UNKNOWN_BLOCKER,
        resource_id=_clean(snapshot.wait_resource_id),
        blocked_cell_id=blocked_cell_id,
        next_cell_id=next_cell_id,
        evidence=evidence,
    )


def _cycle_fingerprint(
    participant_robot_ids: Iterable[str],
    edges: Iterable[WaitEdge],
) -> str:
    payload = {
        "participants": sorted(participant_robot_ids),
        "edges": [edge.canonical_tuple() for edge in sorted(edges, key=WaitEdge.canonical_tuple)],
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _episode_id(run_id: str, fingerprint: str, first_tick: int) -> str:
    encoded = f"{run_id}|{fingerprint}|{first_tick}".encode("utf-8")
    return f"deadlock-{hashlib.sha256(encoded).hexdigest()[:24]}"


def _merge_actions(
    existing: Sequence[MitigationAction],
    additions: Sequence[MitigationAction],
) -> tuple[MitigationAction, ...]:
    return tuple(
        sorted(
            set(existing) | set(additions),
            key=lambda item: (item.tick, item.robot_id, item.action, item.details or ""),
        )
    )


def _participant_state(
    participant_robot_ids: Iterable[str],
    snapshot_by_id: dict[str, RobotSnapshot],
) -> tuple[tuple[str, str | None, str | None], ...]:
    return tuple(
        (
            robot_id,
            snapshot_by_id[robot_id].current_cell_id,
            snapshot_by_id[robot_id].task_progress_token,
        )
        for robot_id in sorted(participant_robot_ids)
    )


def _progress_evidence(
    previous: Sequence[tuple[str, str | None, str | None]],
    snapshot_by_id: dict[str, RobotSnapshot],
    *,
    valid_cell_ids: Collection[str] | None = None,
) -> tuple[tuple[str, ...], bool]:
    evidence: list[str] = []
    complete = True
    valid_cells = frozenset(valid_cell_ids) if valid_cell_ids is not None else None
    for robot_id, previous_cell, previous_progress in previous:
        current = snapshot_by_id.get(robot_id)
        if current is None:
            complete = False
            continue
        current_cell = _clean(current.current_cell_id)
        if current_cell is None or (
            valid_cells is not None and current_cell not in valid_cells
        ):
            complete = False
        elif previous_cell is not None and current_cell != previous_cell:
            evidence.append(
                f"ROBOT_MOVED:{robot_id}:{previous_cell}->{current_cell}"
            )
        if (
            previous_progress is not None
            and current.task_progress_token is not None
            and current.task_progress_token != previous_progress
        ):
            evidence.append(
                f"TASK_PROGRESS:{robot_id}:{previous_progress}->{current.task_progress_token}"
            )
    return tuple(sorted(set(evidence))), complete


def _update_robot_block_stats(
    existing: Sequence[RobotBlockStats],
    snapshots: Sequence[RobotSnapshot],
    graph: WaitForGraph,
    tick: int,
) -> tuple[tuple[RobotBlockStats, ...], bool]:
    existing_by_id = {item.robot_id: item for item in existing}
    snapshot_by_id = {snapshot.robot_id: snapshot for snapshot in snapshots}
    robot_edge_by_waiter = {
        edge.waiter_robot_id: edge for edge in graph.robot_edges
    }
    updated: dict[str, RobotBlockStats] = {}
    for robot_id, snapshot in snapshot_by_id.items():
        previous = existing_by_id.get(robot_id, RobotBlockStats(robot_id=robot_id))
        if snapshot.is_waiting:
            is_continuation = (
                previous.continuous_blocked_ticks > 0
                and previous.last_observed_tick == tick - 1
            )
            continuous = previous.continuous_blocked_ticks + 1 if is_continuation else 1
            edge = robot_edge_by_waiter.get(robot_id)
            updated[robot_id] = replace(
                previous,
                blocked_start_tick=(
                    previous.blocked_start_tick if is_continuation else tick
                ),
                blocked_end_tick=None,
                continuous_blocked_ticks=continuous,
                max_continuous_blocked_ticks=max(
                    previous.max_continuous_blocked_ticks,
                    continuous,
                ),
                cumulative_blocked_robot_ticks=previous.cumulative_blocked_robot_ticks + 1,
                blocker_robot_id=edge.blocker_robot_id if edge else None,
                blocked_cell_id=(
                    edge.blocked_cell_id if edge else snapshot.blocked_cell_id or snapshot.next_cell_id
                ),
                next_cell_id=snapshot.next_cell_id,
                blocked_event_count=previous.blocked_event_count + (0 if is_continuation else 1),
                last_observed_tick=tick,
                status=EpisodeStatus.OBSERVED_BLOCK,
            )
        else:
            updated[robot_id] = replace(
                previous,
                blocked_start_tick=None,
                blocked_end_tick=(
                    previous.last_observed_tick
                    if previous.continuous_blocked_ticks > 0
                    else previous.blocked_end_tick
                ),
                continuous_blocked_ticks=0,
                blocker_robot_id=None,
                blocked_cell_id=None,
                next_cell_id=snapshot.next_cell_id,
                last_observed_tick=tick,
                status=None,
            )

    missing = False
    for robot_id in sorted(set(existing_by_id) - set(snapshot_by_id)):
        missing = True
        previous = existing_by_id[robot_id]
        updated[robot_id] = replace(
            previous,
            blocked_start_tick=None,
            blocked_end_tick=None,
            continuous_blocked_ticks=0,
            blocker_robot_id=None,
            status=None,
            data_quality=DataQuality.INCOMPLETE,
        )
    return tuple(sorted(updated.values(), key=lambda item: item.robot_id)), missing


def _terminate_for_tick_gap(state: DeadlockState, next_tick: int) -> DeadlockState:
    episodes = {episode.episode_id: episode for episode in state.episodes}
    for active_cycle in state.active_cycles:
        episode = episodes[active_cycle.episode_id]
        episodes[episode.episode_id] = replace(
            episode,
            status=EpisodeStatus.ENDED_UNVERIFIED,
            resolution_evidence=(
                f"TICK_GAP_AFTER:{state.last_tick}",
                f"NEXT_OBSERVED_TICK:{next_tick}",
            ),
            data_quality=DataQuality.INCOMPLETE,
        )
    reset_stats = tuple(
        replace(
            item,
            blocked_start_tick=None,
            blocked_end_tick=None,
            continuous_blocked_ticks=0,
            blocker_robot_id=None,
            status=None,
            data_quality=DataQuality.INCOMPLETE,
        )
        for item in state.robot_block_stats
    )
    return replace(
        state,
        episodes=tuple(sorted(episodes.values(), key=lambda item: (item.first_observed_tick, item.episode_id))),
        active_cycles=(),
        robot_block_stats=reset_stats,
        data_quality=DataQuality.INCOMPLETE,
    )
