"""ARM-R2-A layout compatibility checks with database isolation.

Run this module directly.  It performs no application import until both
database path variables have been redirected to a unique temporary path.
"""

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
PLAN_PATH = ROOT / "data" / "transport_layout_plans" / "topology-6ceb4920.g1b1.json"
NEW_REVISION = "2.0.0:16e70cca04bee0ef"
ORIGINAL_COMPATIBLE_REVISIONS = [
    "2.0.0:0d09199ffb195429",
    "2.0.0:eba4f06663c543a5",
    "2.0.0:9f7141eb74a4a963",
    "2.0.0:1ec8cf4490635105",
    "2.0.0:e4d9934f44d05265",
    "2.0.0:02792a5522a0e77d",
]


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class SecondaryArmLayoutCompatibilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.topology = json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))
        cls.raw_plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))

    def test_revision_and_plan_contract_are_preserved(self) -> None:
        topology = self.topology
        plan = self.raw_plan
        revision = f"{topology['meta']['version']}:{canonical_sha256(topology)[:16]}"

        self.assertEqual(revision, NEW_REVISION)
        self.assertEqual(topology["id"], plan["topologyId"])
        self.assertEqual(
            plan["compatibleTopologyRevisions"],
            [*ORIGINAL_COMPATIBLE_REVISIONS, NEW_REVISION],
        )
        self.assertEqual(plan["topologyRevision"], "2.0.0:1ce72c707e5aa20f")
        self.assertEqual(plan["overallStatus"], "PARTIAL")
        self.assertEqual(plan["recommendedOptionId"], "scheme-a-height-transfer")
        self.assertEqual(
            canonical_sha256(plan["pickingArmPreview"]),
            "5fe57063bef713b087b49d941cbe2953756522b2000a4169b1f8c04edf7c3fd2",
        )
        self.assertEqual(plan["pickingArmPreview"]["armId"], "picking-arm-01")
        self.assertEqual(plan["pickingArmPreview"]["installation"]["row"], 91)
        self.assertEqual(plan["pickingArmPreview"]["installation"]["col"], 54)
        self.assertEqual(plan["pickingArmPreview"]["pickup"]["topologyCellId"], "cell-r92-c48")
        self.assertEqual(plan["pickingArmPreview"]["dropoff"]["topologyCellId"], "cell-r84-c50")

    def test_secondary_arm_resources_and_port_ownership_are_compatible(self) -> None:
        topology = self.topology
        self.assertEqual(len(topology["nodes"]), 2520)
        node_ids = [str(node["id"]) for node in topology["nodes"]]
        self.assertEqual(len(node_ids), len(set(node_ids)))

        arms = {
            str(node["id"]): node
            for node in topology["nodes"]
            if node.get("type") == "picking_arm"
        }
        self.assertEqual(set(arms), {"picking-arm-01", "arm-01", "arm-02"})
        self.assertEqual(
            {
                arm_id: (node.get("floor"), int(node["row"]), int(node["col"]))
                for arm_id, node in arms.items()
            },
            {
                "picking-arm-01": ("1F", 91, 54),
                "arm-01": ("1F", 109, 63),
                "arm-02": ("1F", 117, 63),
            },
        )

        rows = int(topology["grid"]["rows"])
        cols = int(topology["grid"]["cols"])
        for arm_id in ("arm-01", "arm-02"):
            arm = arms[arm_id]
            row, col = int(arm["row"]), int(arm["col"])
            self.assertTrue(0 <= row < rows and 0 <= col < cols)
            self.assertFalse(
                any(int(cell["row"]) == row and int(cell["col"]) == col for cell in topology["cells"])
            )
            self.assertFalse(
                any(
                    int(zone["x"]) <= col < int(zone["x"]) + int(zone["width"])
                    and int(zone["y"]) <= row < int(zone["y"]) + int(zone["height"])
                    for zone in topology["zones"]
                )
            )
            self.assertEqual(
                [
                    str(node["id"])
                    for node in topology["nodes"]
                    if int(node.get("row", -1)) == row and int(node.get("col", -1)) == col
                ],
                [arm_id],
            )

        ports = [(102, 62), (106, 62), (110, 62), (114, 62), (118, 62)]
        conveyor_positions = {
            (int(cell["row"]), int(cell["col"]))
            for cell in topology["cells"]
            if cell.get("type") == "conveyor"
        }
        self.assertTrue(all(port in conveyor_positions for port in ports))
        secondary_arms = [arms["arm-01"], arms["arm-02"]]
        radius = float(self.raw_plan["pickingArmPreview"]["installation"]["workRadius"])
        assignments = {"arm-01": [], "arm-02": []}
        for row, col in ports:
            owner = min(
                secondary_arms,
                key=lambda arm: (
                    abs(row - int(arm["row"])) + abs(col - int(arm["col"])),
                    int(arm["row"]),
                ),
            )
            distance = abs(row - int(owner["row"])) + abs(col - int(owner["col"]))
            self.assertLessEqual(distance, radius)
            assignments[str(owner["id"])].append((row, col))
        self.assertEqual(
            assignments,
            {
                "arm-01": [(102, 62), (106, 62), (110, 62)],
                "arm-02": [(114, 62), (118, 62)],
            },
        )

        drop_zone = next(zone for zone in topology["zones"] if zone.get("type") == "drop_zone")
        self.assertTrue(
            all(
                int(drop_zone["x"]) <= 68 < int(drop_zone["x"]) + int(drop_zone["width"])
                and int(drop_zone["y"]) <= row < int(drop_zone["y"]) + int(drop_zone["height"])
                for row, _ in ports
            )
        )

    def test_loader_accepts_revision_without_runtime_or_database(self) -> None:
        self.assertNotIn("app.services.runtime", sys.modules)
        if str(BACKEND) not in sys.path:
            sys.path.insert(0, str(BACKEND))

        isolated_directory = Path(tempfile.gettempdir()) / f"arm-r2a-{uuid.uuid4().hex}"
        isolated_db = isolated_directory / "isolated.db"
        self.assertFalse(isolated_directory.exists())
        import app.core.config as config
        import app.core.db as db

        config.DB_PATH = isolated_db
        db.DB_PATH = isolated_db
        from app.services.transport_layout import load_transport_layout_plan

        self.assertNotIn("app.services.runtime", sys.modules)
        topology = copy.deepcopy(self.topology)
        snapshot = copy.deepcopy(topology)
        result = load_transport_layout_plan(topology)

        self.assertEqual(topology, snapshot)
        self.assertFalse(isolated_directory.exists())
        self.assertFalse(isolated_db.exists())
        self.assertEqual(result.topologyRevision, NEW_REVISION)
        self.assertIsNotNone(result.pickingArm)
        self.assertEqual(result.pickingArm.armId, "picking-arm-01")
        self.assertEqual(
            (result.pickingArm.installation.row, result.pickingArm.installation.col),
            (91, 54),
        )
        self.assertEqual(result.overallStatus, "BLOCKED")
        issue_codes = {issue.code for issue in result.issues}
        self.assertIn("LAYOUT_SHUTTLE_GRAPH_UNAVAILABLE", issue_codes)
        self.assertIn("PICKING_ARM_BASE_OCCUPIED", issue_codes)
        self.assertNotIn("app.services.runtime", sys.modules)


if __name__ == "__main__":
    unittest.main()
