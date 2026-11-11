from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
TOPOLOGY_PATH = ROOT / "data" / "topology_666.json"
SHUTTLE_CONFIG_PATH = (
    ROOT / "data" / "shuttle_configs" / "topology-6ceb4920.shuttle-v2.json"
)
DEFAULT_DB_PATH = BACKEND / "app_data" / "agv_platform.db"
CURRENT_REVISION = "2.0.0:16e70cca04bee0ef"
PREVIOUS_REVISION = "2.0.0:9f7141eb74a4a963"
EXPECTED_LIFTS = {
    "shuttle-lift-a": (98, 21, "zone-shuttle-2"),
    "shuttle-lift-b": (98, 49, "zone-shuttle-2"),
}


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class SecondaryArmShuttleCompatibilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.default_db_before = (
            DEFAULT_DB_PATH.exists(),
            DEFAULT_DB_PATH.stat().st_size if DEFAULT_DB_PATH.exists() else None,
            DEFAULT_DB_PATH.stat().st_mtime_ns if DEFAULT_DB_PATH.exists() else None,
            _sha256(DEFAULT_DB_PATH),
        )
        cls.isolated_directory = Path(tempfile.gettempdir()) / (
            f"arm-r2b-{uuid.uuid4().hex}"
        )
        cls.isolated_db = cls.isolated_directory / "isolated.db"
        if cls.isolated_directory.exists():
            raise AssertionError("The unique database-isolation canary already exists")

        sys.path.insert(0, str(BACKEND))
        import app.core.config as config_module
        import app.core.db as db_module

        # Redirect both path bindings before importing any service under test.
        config_module.DB_PATH = cls.isolated_db
        db_module.DB_PATH = cls.isolated_db

        from app.services import shuttle_topology, unified_transfer

        cls.shuttle_topology = shuttle_topology
        cls.unified_transfer = unified_transfer
        cls.topology = json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))
        cls.config = json.loads(SHUTTLE_CONFIG_PATH.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls) -> None:
        default_db_after = (
            DEFAULT_DB_PATH.exists(),
            DEFAULT_DB_PATH.stat().st_size if DEFAULT_DB_PATH.exists() else None,
            DEFAULT_DB_PATH.stat().st_mtime_ns if DEFAULT_DB_PATH.exists() else None,
            _sha256(DEFAULT_DB_PATH),
        )
        if default_db_after != cls.default_db_before:
            raise AssertionError("The default database changed during the static contract test")
        if cls.isolated_directory.exists():
            raise AssertionError("Application code attempted to initialize the isolated database")

    def test_seed_revision_and_formal_config_change_scope(self) -> None:
        self.assertEqual(len(self.topology["nodes"]), 2520)
        self.assertEqual(
            self.shuttle_topology.topology_revision(self.topology), CURRENT_REVISION
        )
        arms = {
            node["id"]: (node["row"], node["col"], node["floor"])
            for node in self.topology["nodes"]
            if node.get("type") == "picking_arm"
        }
        self.assertEqual(
            arms,
            {
                "picking-arm-01": (91, 54, "1F"),
                "arm-01": (109, 63, "1F"),
                "arm-02": (117, 63, "1F"),
            },
        )
        self.assertEqual(self.config["topology_id"], self.topology["id"])
        self.assertEqual(self.config["source_topology_revision"], CURRENT_REVISION)
        self.assertEqual(
            self.config["config_revision"], "shuttle-4l-lift-slotlifts-v6"
        )
        self.assertEqual(
            self.config["review_status"], "program_verified_pending_human"
        )
        previous = copy.deepcopy(self.config)
        previous["source_topology_revision"] = PREVIOUS_REVISION
        changed_keys = {
            key
            for key in set(previous) | set(self.config)
            if previous.get(key) != self.config.get(key)
        }
        self.assertEqual(changed_keys, {"source_topology_revision"})

    def test_lift_candidates_footprints_and_four_level_contract(self) -> None:
        topology_before = _canonical_sha256(self.topology)
        graph = self.shuttle_topology.build_shuttle_track_graph(self.topology)
        candidates = self.shuttle_topology.scan_lift_candidates(self.topology, graph)
        candidate_keys = {
            (item["row"], item["col"], item["zone_id"]) for item in candidates
        }
        self.assertEqual(candidate_keys, set(EXPECTED_LIFTS.values()))
        self.assertTrue(
            all(
                graph[neighbor]["shuttle_level"] == node["shuttle_level"]
                for node in graph.values()
                for neighbor in node["neighbors"]
            )
        )

        arm_coords = {
            (int(node["row"]), int(node["col"]))
            for node in self.topology["nodes"]
            if node.get("id") in {"arm-01", "arm-02"}
        }
        footprints: list[set[tuple[int, int]]] = []
        for lift in self.config["lifts"]:
            lift_id = lift["lift_id"]
            row, col, zone_id = EXPECTED_LIFTS[lift_id]
            self.assertEqual((lift["row"], lift["col"], lift["zone_id"]), (row, col, zone_id))
            self.assertEqual(lift["supported_levels"], [1, 2, 3, 4])
            self.assertEqual(lift["capacity"], 1)
            self.assertEqual(lift["enter_time_seconds"], 1.0)
            self.assertEqual(lift["travel_time_per_level_seconds"], 3.0)
            self.assertEqual(lift["exit_time_seconds"], 1.0)
            self.assertEqual(lift["timing_source"], "synthetic_demo")
            self.assertEqual(
                lift["verification_status"], "program_verified_pending_human"
            )
            footprint = {
                (row + row_offset, col + col_offset)
                for row_offset in (0, 1)
                for col_offset in (0, 1)
            }
            self.assertFalse(footprint & arm_coords)
            footprints.append(footprint)
            expected_level_nodes = {
                f"L{level}": f"L{level}-r{row}-c{col}" for level in (1, 2, 3, 4)
            }
            self.assertEqual(lift["level_entrances"], expected_level_nodes)
            self.assertEqual(lift["level_exits"], expected_level_nodes)
            for level, node_id in ((level, expected_level_nodes[f"L{level}"]) for level in (1, 2, 3, 4)):
                node = graph[node_id]
                self.assertEqual(node["zone_id"], zone_id)
                self.assertGreaterEqual(len(node["neighbors"]), 2)
                self.assertTrue(
                    all(
                        graph[neighbor]["shuttle_level"] == level
                        for neighbor in node["neighbors"]
                    )
                )
        self.assertTrue(footprints[0].isdisjoint(footprints[1]))
        for level in (1, 2, 3, 4):
            self.assertEqual(
                len(self.shuttle_topology.connected_components(graph, level)), 1
            )
        self.assertEqual(_canonical_sha256(self.topology), topology_before)

    def test_bound_configuration_passes_existing_validator(self) -> None:
        topology_before = _canonical_sha256(self.topology)
        config_before = _canonical_sha256(self.config)
        graph = self.shuttle_topology.build_shuttle_track_graph(self.topology)
        result = self.shuttle_topology.validate_shuttle_configuration(
            self.topology, copy.deepcopy(self.config), graph
        )
        self.assertTrue(result["planning_enabled"])
        self.assertEqual(result["topology_revision"], CURRENT_REVISION)
        self.assertEqual(
            result["verification_status"], "program_verified_pending_human"
        )
        self.assertEqual(
            {lift["lift_id"] for lift in result["lifts"]}, set(EXPECTED_LIFTS)
        )
        self.assertTrue(
            all(lift["vehicle_type"] == "shuttle" for lift in result["lifts"])
        )
        self.assertEqual(result["lift_candidate_count"], 2)
        self.assertTrue(any("synthetic_demo" in item for item in result["warnings"]))
        self.assertTrue(any("human review" in item for item in result["warnings"]))
        self.assertEqual(_canonical_sha256(self.topology), topology_before)
        self.assertEqual(_canonical_sha256(self.config), config_before)

    def test_fresh_shuttle_context_and_contract(self) -> None:
        topology_before = _canonical_sha256(self.topology)
        graph, validation = self.unified_transfer.shuttle_context(self.topology)
        contract = self.unified_transfer.shuttle_contract(self.topology)
        self.assertEqual(len(validation["lifts"]), 2)
        self.assertEqual(len(graph), len(contract["tracks"]))
        self.assertEqual(contract["topologyRevision"], CURRENT_REVISION)
        self.assertTrue(contract["planningEnabled"])
        self.assertEqual(
            contract["verificationStatus"], "program_verified_pending_human"
        )
        self.assertEqual(
            {lift["lift_id"] for lift in contract["lifts"]}, set(EXPECTED_LIFTS)
        )
        self.assertEqual(_canonical_sha256(self.topology), topology_before)

    def test_database_paths_remain_isolated_and_uninitialized(self) -> None:
        self.assertEqual(str(self.shuttle_topology.DATA_DIR), str(ROOT / "data"))
        self.assertFalse(self.isolated_directory.exists())
        self.assertFalse(self.isolated_db.exists())
        current_default_db = (
            DEFAULT_DB_PATH.exists(),
            DEFAULT_DB_PATH.stat().st_size if DEFAULT_DB_PATH.exists() else None,
            DEFAULT_DB_PATH.stat().st_mtime_ns if DEFAULT_DB_PATH.exists() else None,
            _sha256(DEFAULT_DB_PATH),
        )
        self.assertEqual(current_default_db, self.default_db_before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
