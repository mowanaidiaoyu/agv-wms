from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app.api import shuttle as shuttle_api
from app.core.db import get_conn, utc_now
from app.core.repository import Repository
from app.main import app
from app.services.shuttle_storage_review import (
    ShuttleStorageConflictError,
    ShuttleStorageReviewService,
)
from app.services.shuttle_topology import ShuttleConfigStore, build_shuttle_track_graph


ROOT = Path(__file__).resolve().parents[2]
TOPOLOGY_PATH = ROOT / "docs" / "topologies" / "666.json"


@pytest.fixture
def topology() -> dict:
    return json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8-sig"))


@pytest.fixture
def repository(tmp_path: Path, topology: dict) -> Repository:
    repository = Repository(tmp_path / "shuttle-storage-review.db")
    repository.upsert_topology(topology["id"], topology["name"], topology)
    return repository


@pytest.fixture
def service(repository: Repository) -> ShuttleStorageReviewService:
    return ShuttleStorageReviewService(repository)


def _valid_trial(service: ShuttleStorageReviewService, topology_id: str):
    trial = service.trial_area(topology_id)
    assert len(trial["candidates"]) == 3
    candidate = trial["candidates"][0]
    option = next(
        item for item in candidate["access_options"]
        if item["geometry_clear"] and item["connected_to_lift_network"]
    )
    return candidate, option


def _approve_trial(
    service: ShuttleStorageReviewService, topology_id: str
) -> tuple[dict, dict]:
    candidate, option = _valid_trial(service, topology_id)
    service.save_draft(
        topology_id, candidate["location_id"], option["required_pick_face"],
        option["track_node_id"], "researcher-b", "verified in test",
    )
    approved = service.review_mapping(
        topology_id, candidate["location_id"], "simulation_approved",
        "researcher-b", "explicit simulation review", True,
    )
    return approved, option


def test_real_candidate_catalog_is_stable_and_separates_levels_and_entity_types(
    service: ShuttleStorageReviewService, topology: dict
):
    first = service.list_candidates(topology["id"], limit=5000)
    second = service.list_candidates(topology["id"], limit=5000)

    assert first["total"] == 4238
    assert [item["location_id"] for item in first["candidates"]] == [
        item["location_id"] for item in second["candidates"]
    ]
    assert all(item["location_id"].startswith("shuttle-location:") for item in first["candidates"])
    assert not any(item["location_id"].startswith("storage-") for item in first["candidates"])
    assert not any(item["location_id"].startswith("L1-r") for item in first["candidates"])
    same_slot = [
        item for item in first["candidates"]
        if item["zone_id"] == "zone-shuttle" and item["row"] == 96 and item["col"] == 43
    ]
    assert {item["storage_level"] for item in same_slot} == {2, 3, 4}
    assert len({item["location_id"] for item in same_slot}) == 3


def test_trial_area_uses_three_real_candidates_but_never_auto_approves(
    service: ShuttleStorageReviewService, topology: dict
):
    trial = service.trial_area(topology["id"])

    assert [item["storage_level"] for item in trial["candidates"]] == [1, 2, 4]
    assert trial["execution_allowed"] is False
    assert all(item["persisted_status"] is None for item in trial["candidates"])
    assert all(item["access_options"] for item in trial["candidates"])


def test_mapping_validation_rejects_missing_face_cross_level_and_nonadjacent_track(
    service: ShuttleStorageReviewService, topology: dict
):
    candidate, option = _valid_trial(service, topology["id"])
    missing_face = service.validate_mapping(
        topology["id"], candidate["location_id"], None, option["track_node_id"]
    )
    other_level = 2 if candidate["storage_level"] != 2 else 3
    cross_level_id = f"L{other_level}-{option['track_node_id'].split('-', 1)[1]}"
    cross_level = service.validate_mapping(
        topology["id"], candidate["location_id"], option["required_pick_face"], cross_level_id
    )
    nonadjacent = service.validate_mapping(
        topology["id"], candidate["location_id"], option["required_pick_face"], "L1-r100-c51"
    )

    assert missing_face["valid"] is False
    assert missing_face["pick_face_matches"] is False
    assert cross_level["valid"] is False
    assert cross_level["same_level"] is False
    assert nonadjacent["valid"] is False
    assert nonadjacent["geometry_clear"] is False
    assert nonadjacent["adjacent_track"] is False


def test_draft_review_history_and_inventory_isolation(
    service: ShuttleStorageReviewService, repository: Repository, topology: dict
):
    candidate, option = _valid_trial(service, topology["id"])
    now = utc_now()
    with get_conn(repository.db_path) as conn:
        conn.execute(
            "INSERT INTO sku_catalog VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("SKU-KEEP", "keep", "test", "test", "piece", None, None, "manual", now),
        )
        conn.execute(
            """
            INSERT INTO warehouse_inventory VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (topology["id"], "zone-ctu", "storage-01", 1, "SKU-KEEP", 11, 2, "manual", now),
        )
    before = _inventory_snapshot(repository)

    draft = service.save_draft(
        topology["id"], candidate["location_id"], option["required_pick_face"],
        option["track_node_id"], "researcher-b", "draft only",
    )
    assert draft["status"] == "draft"
    assert draft["execution_allowed"] is False
    with pytest.raises(ShuttleStorageConflictError, match="Explicit human confirmation"):
        service.review_mapping(
            topology["id"], candidate["location_id"], "simulation_approved",
            "researcher-b", None, False,
        )

    approved = service.review_mapping(
        topology["id"], candidate["location_id"], "simulation_approved",
        "researcher-b", "geometry reviewed", True,
    )
    history = service.review_history(topology["id"], candidate["location_id"])

    assert approved["effective_status"] == "simulation_approved"
    assert approved["execution_allowed"] is True
    assert [item["decision"] for item in history] == ["simulation_approved", "draft"]
    assert _inventory_snapshot(repository) == before
    assert ShuttleStorageReviewService(repository).get_mapping(
        topology["id"], candidate["location_id"]
    )["execution_allowed"] is True


def test_rejected_mapping_is_never_executable(
    service: ShuttleStorageReviewService, topology: dict
):
    candidate, option = _valid_trial(service, topology["id"])
    service.save_draft(
        topology["id"], candidate["location_id"], option["required_pick_face"],
        option["track_node_id"], "researcher-b", None,
    )
    rejected = service.review_mapping(
        topology["id"], candidate["location_id"], "rejected",
        "researcher-b", "insufficient physical evidence", True,
    )
    assert rejected["status"] == "rejected"
    assert rejected["execution_allowed"] is False


def test_topology_change_invalidates_an_approved_mapping(
    service: ShuttleStorageReviewService, repository: Repository, topology: dict,
    monkeypatch: pytest.MonkeyPatch,
):
    approved, _ = _approve_trial(service, topology["id"])
    original_config = ShuttleConfigStore().load(topology["id"])
    changed_config = deepcopy(original_config)
    changed_config["config_revision"] = "shuttle-review-test-revision"
    monkeypatch.setattr(ShuttleConfigStore, "load", lambda _self, _topology_id: changed_config)
    stale_config = service.get_mapping(topology["id"], approved["location_id"])
    assert stale_config["effective_status"] == "topology_version_mismatch"
    assert stale_config["execution_allowed"] is False

    changed = deepcopy(topology)
    changed["meta"]["version"] = "2.0.1"
    repository.upsert_topology(changed["id"], changed["name"], changed)

    stale = service.get_mapping(topology["id"], approved["location_id"])
    assert stale["effective_status"] == "topology_version_mismatch"
    assert stale["execution_allowed"] is False

    removed = deepcopy(changed)
    removed["zones"] = [
        zone for zone in removed["zones"] if zone["id"] != approved["zone_id"]
    ]
    repository.upsert_topology(removed["id"], removed["name"], removed)
    missing_candidate = service.get_mapping(topology["id"], approved["location_id"])
    assert missing_candidate["execution_allowed"] is False
    assert any("no longer exists" in reason for reason in missing_candidate["validation"]["reasons"])


def test_demo_dropoff_requires_real_safe_track_and_approved_pickup(
    service: ShuttleStorageReviewService, topology: dict
):
    no_pickup = service.validate_dropoff(topology["id"], "L1-r100-c7")
    assert no_pickup["valid"] is False
    assert any("simulation-approved pickup" in reason for reason in no_pickup["reasons"])
    approved, _ = _approve_trial(service, topology["id"])
    graph = build_shuttle_track_graph(topology)
    dropoff_validation = next(
        service.validate_dropoff(topology["id"], node_id)
        for node_id, node in graph.items()
        if node["track_type"] == "straight"
        and len(node["neighbors"]) >= 2
        and node_id != approved["access_track_node_id"]
        and service.validate_dropoff(topology["id"], node_id)["valid"]
    )
    draft = service.save_dropoff_draft(
        topology["id"], dropoff_validation["track_node_id"],
        "离线演示交接点", "researcher-b", "synthetic demo only",
    )
    reviewed = service.review_dropoff(
        topology["id"], draft["dropoff_id"], "simulation_approved",
        "researcher-b", "explicitly reviewed", True,
    )

    assert reviewed["data_source"] == "synthetic_demo"
    assert reviewed["execution_allowed"] is True
    assert service.validate_dropoff(topology["id"], "not-a-track")["valid"] is False


def test_review_api_saves_draft_but_requires_explicit_approval(
    monkeypatch: pytest.MonkeyPatch,
    repository: Repository,
    topology: dict,
):
    monkeypatch.setattr(shuttle_api, "repo", repository)
    candidate, option = _valid_trial(ShuttleStorageReviewService(repository), topology["id"])
    with TestClient(app) as client:
        draft_response = client.post(
            f"/api/v1/shuttle/topologies/{topology['id']}/storage-mappings/drafts",
            json={
                "location_id": candidate["location_id"],
                "pick_face": option["required_pick_face"],
                "access_track_node_id": option["track_node_id"],
                "reviewer": "researcher-b",
                "review_note": "browser draft",
            },
        )
        rejected_approval = client.post(
            "/api/v1/shuttle/topologies/"
            f"{topology['id']}/storage-mappings/{quote(candidate['location_id'], safe='')}/review",
            json={
                "decision": "simulation_approved",
                "reviewer": "researcher-b",
                "explicit_confirmation": False,
            },
        )

    assert draft_response.status_code == 200
    assert draft_response.json()["execution_allowed"] is False
    assert rejected_approval.status_code == 409


def _inventory_snapshot(repository: Repository) -> list[tuple]:
    with get_conn(repository.db_path) as conn:
        rows = conn.execute(
            """
            SELECT topology_id, storage_node_id, storage_layer, sku_id,
                   quantity_on_hand, quantity_reserved
            FROM warehouse_inventory ORDER BY topology_id, storage_node_id, sku_id
            """
        ).fetchall()
    return [tuple(row) for row in rows]
