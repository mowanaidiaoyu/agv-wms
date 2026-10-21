from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import shuttle as shuttle_api
from app.main import app
from app.services.shuttle_storage_audit import (
    audit_shuttle_storage,
    build_shuttle_storage_candidates,
    page_shuttle_storage_candidates,
)


ROOT = Path(__file__).resolve().parents[2]
TOPOLOGY_PATH = ROOT / "docs" / "topologies" / "666.json"


def _topology() -> dict:
    return json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8-sig"))


class _FakeRepository:
    def __init__(self, topology: dict):
        self.topology = topology

    def get_topology(self, topology_id: str):
        if topology_id != self.topology["id"]:
            return None
        return {"id": topology_id, "name": self.topology["name"], "payload": self.topology}


def test_real_666_has_ctu_storage_but_no_formal_shuttle_storage_identity():
    audit = audit_shuttle_storage(_topology())

    assert audit["ctu_storage_node_count"] == 2496
    assert audit["shuttle_storage_node_count"] == 0
    assert audit["shuttle_zone_count"] == 31
    assert audit["execution_allowed"] is False
    assert audit["verified_access_mapping_count"] == 0
    assert audit["declared_pick_face_count"] == 0
    assert audit["formal_dropoff_count"] == 0
    assert audit["audit_status"] == "blocked_unverified_mappings"


def test_visual_rack_candidates_are_stable_but_never_executable_locations():
    topology = _topology()
    first = build_shuttle_storage_candidates(topology)
    second = build_shuttle_storage_candidates(topology)

    assert first == second
    assert first
    assert len({item["location_id"] for item in first}) == len(first)
    assert all(item["review_status"] == "draft" for item in first)
    assert all(item["pick_face"] == "unknown" for item in first)
    assert all(item["access_track_node_id"] is None for item in first)
    assert all(item["execution_allowed"] is False for item in first)
    assert all(item["data_source"] == "topology_shuttle_slot_candidate" for item in first)


def test_slot_aisle_bits_are_tracks_not_storage_candidates():
    topology = _topology()
    candidates = build_shuttle_storage_candidates(topology)
    candidate_keys = {
        (item["zone_id"], item["storage_level"], item["row"], item["col"])
        for item in candidates
    }

    # This real slot is an aisle on all four levels and therefore cannot become storage.
    assert all(("zone-shuttle-3", level, 100, 19) not in candidate_keys for level in (1, 2, 3, 4))
    # The real mask at 96,43 declares only L1 as an aisle: L2-L4 remain rack candidates.
    assert ("zone-shuttle", 1, 96, 43) not in candidate_keys
    assert all(("zone-shuttle", level, 96, 43) in candidate_keys for level in (2, 3, 4))


def test_candidate_paging_filters_levels_without_approving_mappings():
    page = page_shuttle_storage_candidates(_topology(), level=4, offset=1, limit=3)

    assert page["total"] > 3
    assert page["offset"] == 1
    assert page["limit"] == 3
    assert len(page["candidates"]) == 3
    assert all(item["storage_level"] == 4 for item in page["candidates"])
    assert all(item["access_mapping_status"] == "unverified" for item in page["candidates"])


def test_shuttle_storage_audit_api_is_read_only_and_reports_the_blocker(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(shuttle_api, "repo", _FakeRepository(_topology()))
    with TestClient(app) as client:
        response = client.get("/api/v1/shuttle/topologies/topology-6ceb4920/storage-audit")

    assert response.status_code == 200
    payload = response.json()
    assert payload["contract_version"] == "shuttle-storage-contract/v1"
    assert payload["execution_allowed"] is False
    assert payload["shuttle_storage_node_count"] == 0
    assert payload["rack_level_candidate_count"] > 0


def test_shuttle_storage_candidate_api_never_returns_verified_access_mapping(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(shuttle_api, "repo", _FakeRepository(_topology()))
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/shuttle/topologies/topology-6ceb4920/storage-candidates",
            params={"level": 2, "limit": 5},
        )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["candidates"]) == 5
    assert all(item["access_mapping_status"] == "unverified" for item in payload["candidates"])
    assert all(item["execution_allowed"] is False for item in payload["candidates"])
