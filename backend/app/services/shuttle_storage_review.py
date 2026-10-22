from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..core.db import decode_json, encode_json, get_conn, utc_now
from ..core.repository import Repository
from .shuttle_pathfinding import plan_shuttle_path
from .shuttle_storage_audit import build_shuttle_storage_candidates
from .shuttle_topology import (
    SHUTTLE_COORDINATE_CONTRACT_REVISION,
    ShuttleConfigStore,
    build_shuttle_track_graph,
    topology_revision,
    validate_shuttle_configuration,
)


_REVIEW_GEOMETRY_CONTEXT: tuple[str, dict[str, Any]] | None = None


def _geometry_context(topology: dict[str, Any]) -> dict[str, Any]:
    global _REVIEW_GEOMETRY_CONTEXT
    revision = topology_revision(topology)
    shuttle_config = ShuttleConfigStore().load(str(topology["id"]))
    config_digest = hashlib.sha256(
        json.dumps(
            shuttle_config, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()[:16]
    context_key = f"{revision}:{config_digest}"
    if _REVIEW_GEOMETRY_CONTEXT and _REVIEW_GEOMETRY_CONTEXT[0] == context_key:
        return _REVIEW_GEOMETRY_CONTEXT[1]
    # Drop references to the old, large catalog before constructing a changed revision.
    _REVIEW_GEOMETRY_CONTEXT = None
    graph = build_shuttle_track_graph(topology)
    shuttle_validation = validate_shuttle_configuration(topology, shuttle_config, graph)
    context = {
        "candidate_lookup": {
            item["location_id"]: item for item in build_shuttle_storage_candidates(topology)
        },
        "graph": graph,
        "shuttle_validation": shuttle_validation,
        "shuttle_config_revision": f"{shuttle_validation['config_revision']}:{config_digest}",
        "sha256": hashlib.sha256(
            json.dumps(
                topology, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest(),
    }
    _REVIEW_GEOMETRY_CONTEXT = (context_key, context)
    return context


class ShuttleStorageReviewError(ValueError):
    pass


class ShuttleStorageNotFoundError(ShuttleStorageReviewError):
    pass


class ShuttleStorageConflictError(ShuttleStorageReviewError):
    pass


class ShuttleStorageReviewService:
    """Persistent, version-protected review workflow for Shuttle rack candidates.

    This service never writes warehouse_inventory. It only stores location/access
    review metadata and synthetic-demo drop-off reviews.
    """

    def __init__(self, repository: Repository):
        self.repository = repository
        self.db_path = Path(repository.db_path)

    def _record(self, topology_id: str) -> dict[str, Any]:
        record = self.repository.get_topology(topology_id)
        if record is None:
            raise ShuttleStorageNotFoundError(f"Topology {topology_id} not found")
        return record

    @staticmethod
    def _candidate_lookup(topology: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return _geometry_context(topology)["candidate_lookup"]

    def list_candidates(
        self,
        topology_id: str,
        *,
        level: int | None = None,
        zone_id: str | None = None,
        search: str | None = None,
        has_adjacent_track: bool | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        topology = self._record(topology_id)["payload"]
        candidates = list(self._candidate_lookup(topology).values())
        persisted = self._persisted_statuses(topology_id, topology)
        if level is not None:
            candidates = [item for item in candidates if item["storage_level"] == level]
        if zone_id is not None:
            candidates = [item for item in candidates if item["zone_id"] == zone_id]
        if search:
            term = search.casefold()
            candidates = [
                item for item in candidates
                if term in item["location_id"].casefold() or term in item["zone_id"].casefold()
            ]
        if has_adjacent_track is not None:
            candidates = [
                item for item in candidates
                if bool(item["potential_access_track_node_ids"]) is has_adjacent_track
            ]
        page = [
            {**item, "persisted_status": persisted.get(item["location_id"])}
            for item in candidates[offset : offset + limit]
        ]
        return {
            "topology_id": topology_id,
            "topology_revision": topology_revision(topology),
            "contract_version": "shuttle-storage-contract/v1",
            "total": len(candidates),
            "offset": offset,
            "limit": limit,
            "candidates": page,
        }

    def get_candidate(self, topology_id: str, location_id: str) -> dict[str, Any]:
        topology = self._record(topology_id)["payload"]
        candidate = self._candidate_lookup(topology).get(location_id)
        if candidate is None:
            raise ShuttleStorageNotFoundError(
                f"Shuttle storage candidate {location_id} not found in topology {topology_id}"
            )
        persisted = self._persisted_statuses(topology_id, topology)
        return {**candidate, "persisted_status": persisted.get(location_id)}

    def trial_area(self, topology_id: str) -> dict[str, Any]:
        topology = self._record(topology_id)["payload"]
        candidates = build_shuttle_storage_candidates(topology)
        persisted = self._persisted_statuses(topology_id, topology)
        selected: list[dict[str, Any]] = []
        for level in (1, 2, 4):
            candidate = next(
                (
                    item for item in candidates
                    if item["storage_level"] == level
                    and any(
                        option["geometry_clear"] and option["connected_to_lift_network"]
                        for option in item["access_options"]
                    )
                ),
                None,
            )
            if candidate:
                selected.append(
                    {**candidate, "persisted_status": persisted.get(candidate["location_id"])}
                )
        return {
            "topology_id": topology_id,
            "topology_revision": topology_revision(topology),
            "candidates": selected,
            "selection_rule": (
                "Deterministic first L1/L2/L4 candidates with a real same-level adjacent "
                "track, clear vehicle AABB, and connectivity to the configured lift network."
            ),
            "execution_allowed": False,
        }

    def validate_mapping(
        self,
        topology_id: str,
        location_id: str,
        pick_face: str | None,
        access_track_node_id: str | None,
        *,
        exclude_location_id: str | None = None,
    ) -> dict[str, Any]:
        topology = self._record(topology_id)["payload"]
        revision = topology_revision(topology)
        sha256 = _geometry_context(topology)["sha256"]
        candidate = self._candidate_lookup(topology).get(location_id)
        if candidate is None:
            raise ShuttleStorageNotFoundError(f"Unknown Shuttle storage candidate {location_id}")
        context = _geometry_context(topology)
        graph = context["graph"]
        access = graph.get(access_track_node_id or "")
        same_level = bool(access and int(access["shuttle_level"]) == candidate["storage_level"])
        option = next(
            (
                item for item in candidate["access_options"]
                if item["track_node_id"] == access_track_node_id
            ),
            None,
        )
        adjacent = option is not None
        face_matches = bool(option and pick_face == option["required_pick_face"])
        geometry_clear = bool(option and option["geometry_clear"])
        connected = bool(option and option["connected_to_lift_network"])
        validation = context["shuttle_validation"]
        reachable = bool(validation["planning_enabled"] and access and connected)
        conflict = self._approved_mapping_conflict(
            topology_id, access_track_node_id, exclude_location_id or location_id
        )
        codes: list[str] = []
        reasons: list[str] = []
        checks = (
            (pick_face is not None, "pick_face_present", "Pick face is required."),
            (access is not None, "real_track_node", "Access Track Node does not exist in ShuttleGraph."),
            (same_level, "same_level", "Storage level and Access Track Node level differ."),
            (adjacent, "orthogonally_adjacent", "Access Track Node is not a real adjacent track candidate."),
            (face_matches, "pick_face_matches", "Pick face does not face the selected Access Track Node."),
            (geometry_clear, "vehicle_bbox_clear", "Vehicle bounding box overlaps the rack or lacks operation clearance."),
            (connected, "connected_to_lift_network", "Access Track Node is not connected to the configured lift network."),
            (reachable, "reachable", "Access Track Node cannot enter verified Shuttle planning."),
            (not conflict, "mapping_unique", "Another approved location already occupies this Access Track Node."),
        )
        for passed, code, reason in checks:
            if passed:
                codes.append(code)
            else:
                reasons.append(reason)
        return {
            "valid": not reasons,
            "location_id": location_id,
            "access_track_node_id": access_track_node_id,
            "pick_face": pick_face,
            "topology_revision": revision,
            "topology_content_sha256": sha256,
            "shuttle_config_revision": context["shuttle_config_revision"],
            "same_level": same_level,
            "adjacent_track": adjacent,
            "pick_face_matches": face_matches,
            "geometry_clear": geometry_clear,
            "operation_clearance_cells": option["vehicle_clearance_cells"] if option else None,
            "connected_to_lift_network": connected,
            "reachable": reachable,
            "mapping_conflict": conflict,
            "codes": codes,
            "reasons": reasons,
        }

    def save_draft(
        self,
        topology_id: str,
        location_id: str,
        pick_face: str,
        access_track_node_id: str,
        reviewer: str,
        review_note: str | None,
    ) -> dict[str, Any]:
        candidate = self.get_candidate(topology_id, location_id)
        current = self._mapping_row(topology_id, location_id)
        if current and current["status"] in {"simulation_approved", "physical_verified"}:
            raise ShuttleStorageConflictError("Approved mappings cannot be silently downgraded to draft")
        validation = self.validate_mapping(
            topology_id, location_id, pick_face, access_track_node_id
        )
        now = utc_now()
        created_at = current["created_at"] if current else now
        with get_conn(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO shuttle_storage_locations (
                  topology_id, location_id, topology_revision, topology_content_sha256,
                  shuttle_config_revision, zone_id, level, row, col, geometry_source, pick_face,
                  access_track_node_id, status, validation_payload, reviewer,
                  review_note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?)
                ON CONFLICT(topology_id, location_id) DO UPDATE SET
                  topology_revision=excluded.topology_revision,
                  topology_content_sha256=excluded.topology_content_sha256,
                  shuttle_config_revision=excluded.shuttle_config_revision,
                  pick_face=excluded.pick_face,
                  access_track_node_id=excluded.access_track_node_id,
                  status='draft',
                  validation_payload=excluded.validation_payload,
                  reviewer=excluded.reviewer,
                  review_note=excluded.review_note,
                  updated_at=excluded.updated_at
                """,
                (
                    topology_id, location_id, validation["topology_revision"],
                    validation["topology_content_sha256"], validation["shuttle_config_revision"],
                    candidate["zone_id"],
                    candidate["storage_level"], candidate["row"], candidate["col"],
                    "topology_shuttle_slot_candidate", pick_face, access_track_node_id,
                    encode_json(validation), reviewer, review_note, created_at, now,
                ),
            )
            self._insert_review(
                conn, topology_id, location_id, "draft", reviewer, review_note,
                pick_face, access_track_node_id, validation, now,
            )
        return self.get_mapping(topology_id, location_id)

    def review_mapping(
        self,
        topology_id: str,
        location_id: str,
        decision: str,
        reviewer: str,
        review_note: str | None,
        explicit_confirmation: bool,
    ) -> dict[str, Any]:
        if not explicit_confirmation:
            raise ShuttleStorageConflictError("Explicit human confirmation is required")
        row = self._mapping_row(topology_id, location_id)
        if row is None:
            raise ShuttleStorageNotFoundError("Save a Shuttle storage mapping draft before review")
        validation = self.validate_mapping(
            topology_id, location_id, row["pick_face"], row["access_track_node_id"]
        )
        if decision == "simulation_approved" and not validation["valid"]:
            raise ShuttleStorageConflictError("; ".join(validation["reasons"]))
        if decision not in {"simulation_approved", "rejected"}:
            raise ShuttleStorageConflictError("physical_verified is not available in this workflow")
        now = utc_now()
        with get_conn(self.db_path) as conn:
            conn.execute(
                """
                UPDATE shuttle_storage_locations
                SET status=?, topology_revision=?, topology_content_sha256=?,
                    shuttle_config_revision=?, validation_payload=?, reviewer=?, review_note=?, updated_at=?
                WHERE topology_id=? AND location_id=?
                """,
                (
                    decision, validation["topology_revision"], validation["topology_content_sha256"],
                    validation["shuttle_config_revision"], encode_json(validation), reviewer,
                    review_note, now, topology_id, location_id,
                ),
            )
            self._insert_review(
                conn, topology_id, location_id, decision, reviewer, review_note,
                row["pick_face"], row["access_track_node_id"], validation, now,
            )
        return self.get_mapping(topology_id, location_id)

    def list_mappings(self, topology_id: str) -> list[dict[str, Any]]:
        topology = self._record(topology_id)["payload"]
        with get_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM shuttle_storage_locations WHERE topology_id=? ORDER BY updated_at DESC",
                (topology_id,),
            ).fetchall()
        return [self._mapping_payload(topology, dict(row)) for row in rows]

    def get_mapping(self, topology_id: str, location_id: str) -> dict[str, Any]:
        topology = self._record(topology_id)["payload"]
        row = self._mapping_row(topology_id, location_id)
        if row is None:
            raise ShuttleStorageNotFoundError(f"Shuttle storage mapping {location_id} not found")
        return self._mapping_payload(topology, row)

    def review_history(self, topology_id: str, location_id: str) -> list[dict[str, Any]]:
        self._record(topology_id)
        with get_conn(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT * FROM shuttle_storage_reviews
                WHERE topology_id=? AND location_id=? ORDER BY review_id DESC
                """,
                (topology_id, location_id),
            ).fetchall()
        return [
            {**dict(row), "validation": decode_json(row["validation_payload"])}
            for row in rows
        ]

    def save_dropoff_draft(
        self,
        topology_id: str,
        track_node_id: str,
        label: str,
        reviewer: str,
        review_note: str | None,
    ) -> dict[str, Any]:
        validation = self.validate_dropoff(topology_id, track_node_id)
        dropoff_id = f"shuttle-demo-dropoff:{track_node_id}"
        current = self._dropoff_row(topology_id, dropoff_id)
        if current and current["status"] == "simulation_approved":
            raise ShuttleStorageConflictError("Approved drop-offs cannot be silently downgraded")
        now = utc_now()
        created_at = current["created_at"] if current else now
        with get_conn(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO shuttle_demo_dropoffs (
                  topology_id, dropoff_id, topology_revision, topology_content_sha256,
                  shuttle_config_revision, track_node_id, label, status, validation_payload, reviewer,
                  review_note, data_source, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, 'synthetic_demo', ?, ?)
                ON CONFLICT(topology_id, dropoff_id) DO UPDATE SET
                  topology_revision=excluded.topology_revision,
                  topology_content_sha256=excluded.topology_content_sha256,
                  shuttle_config_revision=excluded.shuttle_config_revision,
                  label=excluded.label,
                  status='draft', validation_payload=excluded.validation_payload,
                  reviewer=excluded.reviewer, review_note=excluded.review_note,
                  updated_at=excluded.updated_at
                """,
                (
                    topology_id, dropoff_id, validation["topology_revision"],
                    validation["topology_content_sha256"], validation["shuttle_config_revision"],
                    track_node_id, label,
                    encode_json(validation), reviewer, review_note, created_at, now,
                ),
            )
            conn.execute(
                """
                INSERT INTO shuttle_demo_dropoff_reviews (
                  topology_id, dropoff_id, decision, reviewer, review_note,
                  track_node_id, topology_revision, shuttle_config_revision,
                  validation_payload, created_at
                ) VALUES (?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    topology_id, dropoff_id, reviewer, review_note, track_node_id,
                    validation["topology_revision"], validation["shuttle_config_revision"],
                    encode_json(validation), now,
                ),
            )
        return self.get_dropoff(topology_id, dropoff_id)

    def review_dropoff(
        self,
        topology_id: str,
        dropoff_id: str,
        decision: str,
        reviewer: str,
        review_note: str | None,
        explicit_confirmation: bool,
    ) -> dict[str, Any]:
        if not explicit_confirmation:
            raise ShuttleStorageConflictError("Explicit human confirmation is required")
        if decision not in {"simulation_approved", "rejected"}:
            raise ShuttleStorageConflictError("Unsupported demo drop-off review decision")
        row = self._dropoff_row(topology_id, dropoff_id)
        if row is None:
            raise ShuttleStorageNotFoundError("Save a demo drop-off draft before review")
        validation = self.validate_dropoff(topology_id, row["track_node_id"])
        if decision == "simulation_approved" and not validation["valid"]:
            raise ShuttleStorageConflictError("; ".join(validation["reasons"]))
        now = utc_now()
        with get_conn(self.db_path) as conn:
            conn.execute(
                """
                UPDATE shuttle_demo_dropoffs SET status=?, topology_revision=?,
                  topology_content_sha256=?, shuttle_config_revision=?, validation_payload=?, reviewer=?,
                  review_note=?, updated_at=? WHERE topology_id=? AND dropoff_id=?
                """,
                (
                    decision, validation["topology_revision"], validation["topology_content_sha256"],
                    validation["shuttle_config_revision"], encode_json(validation), reviewer,
                    review_note, now, topology_id, dropoff_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO shuttle_demo_dropoff_reviews (
                  topology_id, dropoff_id, decision, reviewer, review_note,
                  track_node_id, topology_revision, shuttle_config_revision,
                  validation_payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    topology_id, dropoff_id, decision, reviewer, review_note,
                    row["track_node_id"], validation["topology_revision"],
                    validation["shuttle_config_revision"], encode_json(validation), now,
                ),
            )
        return self.get_dropoff(topology_id, dropoff_id)

    def validate_dropoff(self, topology_id: str, track_node_id: str) -> dict[str, Any]:
        topology = self._record(topology_id)["payload"]
        context = _geometry_context(topology)
        graph = context["graph"]
        node = graph.get(track_node_id)
        validation = context["shuttle_validation"]
        approved = [
            item for item in self.list_mappings(topology_id)
            if item["execution_allowed"]
        ]
        parking_clear = bool(node and node["track_type"] == "straight" and len(node["neighbors"]) >= 2)
        conflicts_pickup = any(item["access_track_node_id"] == track_node_id for item in approved)
        reachable_from: list[str] = []
        if node and validation["planning_enabled"]:
            for mapping in approved:
                result = plan_shuttle_path(
                    graph, validation["lifts"], mapping["access_track_node_id"],
                    track_node_id, "astar",
                )
                if result["result_status"] == "success":
                    reachable_from.append(mapping["location_id"])
        reasons: list[str] = []
        if node is None:
            reasons.append("Drop-off must be a real ShuttleGraph node.")
        if not parking_clear:
            reasons.append("Drop-off requires a straight track node with two same-level neighbors.")
        if conflicts_pickup:
            reasons.append("Drop-off cannot occupy an approved pickup Access Track Node.")
        if not approved:
            reasons.append("At least one simulation-approved pickup mapping is required.")
        elif not reachable_from:
            reasons.append("No simulation-approved pickup mapping can reach this drop-off.")
        return {
            "valid": not reasons,
            "topology_revision": topology_revision(topology),
            "topology_content_sha256": _geometry_context(topology)["sha256"],
            "shuttle_config_revision": context["shuttle_config_revision"],
            "coordinate_contract_revision": SHUTTLE_COORDINATE_CONTRACT_REVISION,
            "track_node_id": track_node_id,
            "real_track_node": node is not None,
            "parking_space_clear": parking_clear,
            "conflicts_pickup_mapping": conflicts_pickup,
            "reachable_from_location_ids": reachable_from,
            "reasons": reasons,
        }

    def list_dropoffs(self, topology_id: str) -> list[dict[str, Any]]:
        topology = self._record(topology_id)["payload"]
        with get_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM shuttle_demo_dropoffs WHERE topology_id=? ORDER BY updated_at DESC",
                (topology_id,),
            ).fetchall()
        return [self._dropoff_payload(topology, dict(row)) for row in rows]

    def get_dropoff(self, topology_id: str, dropoff_id: str) -> dict[str, Any]:
        topology = self._record(topology_id)["payload"]
        row = self._dropoff_row(topology_id, dropoff_id)
        if row is None:
            raise ShuttleStorageNotFoundError(f"Demo drop-off {dropoff_id} not found")
        return self._dropoff_payload(topology, row)

    def _approved_mapping_conflict(
        self, topology_id: str, track_node_id: str | None, exclude_location_id: str
    ) -> bool:
        if not track_node_id:
            return False
        with get_conn(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM shuttle_storage_locations
                WHERE topology_id=? AND access_track_node_id=? AND location_id<>?
                  AND status IN ('simulation_approved', 'physical_verified')
                LIMIT 1
                """,
                (topology_id, track_node_id, exclude_location_id),
            ).fetchone()
        return row is not None

    def _persisted_statuses(
        self, topology_id: str, topology: dict[str, Any]
    ) -> dict[str, str]:
        current_revision = topology_revision(topology)
        current_sha = _geometry_context(topology)["sha256"]
        with get_conn(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT location_id, status, topology_revision, topology_content_sha256,
                       shuttle_config_revision
                FROM shuttle_storage_locations WHERE topology_id=?
                """,
                (topology_id,),
            ).fetchall()
        return {
            row["location_id"]: (
                row["status"]
                if row["topology_revision"] == current_revision
                and row["topology_content_sha256"] == current_sha
                and row["shuttle_config_revision"]
                == _geometry_context(topology)["shuttle_config_revision"]
                else "topology_version_mismatch"
            )
            for row in rows
        }

    def _mapping_row(self, topology_id: str, location_id: str) -> dict[str, Any] | None:
        with get_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM shuttle_storage_locations WHERE topology_id=? AND location_id=?",
                (topology_id, location_id),
            ).fetchone()
        return dict(row) if row else None

    def _dropoff_row(self, topology_id: str, dropoff_id: str) -> dict[str, Any] | None:
        with get_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM shuttle_demo_dropoffs WHERE topology_id=? AND dropoff_id=?",
                (topology_id, dropoff_id),
            ).fetchone()
        return dict(row) if row else None

    def _mapping_payload(self, topology: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
        current_revision = topology_revision(topology)
        context = _geometry_context(topology)
        current_sha = context["sha256"]
        version_current = (
            row["topology_revision"] == current_revision
            and row["topology_content_sha256"] == current_sha
            and row["shuttle_config_revision"] == context["shuttle_config_revision"]
        )
        try:
            validation = self.validate_mapping(
                str(topology["id"]), row["location_id"], row["pick_face"],
                row["access_track_node_id"], exclude_location_id=row["location_id"],
            )
        except ShuttleStorageNotFoundError:
            validation = decode_json(row["validation_payload"])
            validation.update(
                {
                    "valid": False,
                    "topology_revision": current_revision,
                    "topology_content_sha256": current_sha,
                    "shuttle_config_revision": context["shuttle_config_revision"],
                    "reachable": False,
                    "reasons": list(validation.get("reasons", []))
                    + ["Storage candidate no longer exists in the current topology."],
                }
            )
        effective_status = row["status"] if version_current else "topology_version_mismatch"
        execution_allowed = bool(
            version_current
            and row["status"] in {"simulation_approved", "physical_verified"}
            and validation["valid"]
        )
        return {
            **row,
            "validation": validation,
            "effective_status": effective_status,
            "execution_allowed": execution_allowed,
        }

    def _dropoff_payload(self, topology: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
        current_revision = topology_revision(topology)
        context = _geometry_context(topology)
        current_sha = context["sha256"]
        version_current = (
            row["topology_revision"] == current_revision
            and row["topology_content_sha256"] == current_sha
            and row["shuttle_config_revision"] == context["shuttle_config_revision"]
        )
        validation = self.validate_dropoff(str(topology["id"]), row["track_node_id"])
        effective_status = row["status"] if version_current else "topology_version_mismatch"
        return {
            **row,
            "validation": validation,
            "effective_status": effective_status,
            "execution_allowed": bool(
                version_current and row["status"] == "simulation_approved" and validation["valid"]
            ),
        }

    @staticmethod
    def _insert_review(
        conn,
        topology_id: str,
        location_id: str,
        decision: str,
        reviewer: str,
        review_note: str | None,
        pick_face: str,
        access_track_node_id: str,
        validation: dict[str, Any],
        created_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO shuttle_storage_reviews (
              topology_id, location_id, decision, reviewer, review_note,
              pick_face, access_track_node_id, topology_revision,
              shuttle_config_revision, validation_payload, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                topology_id, location_id, decision, reviewer, review_note,
                pick_face, access_track_node_id, validation["topology_revision"],
                validation["shuttle_config_revision"], encode_json(validation), created_at,
            ),
        )
