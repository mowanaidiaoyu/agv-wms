"""Static seed contract for the two side-output picking arms.

This module intentionally uses only the Python standard library.  Run it
directly so test collection cannot import the application runtime or open a
database connection.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOPOLOGY_PATH = ROOT / "data" / "topology_666.json"
PLAN_PATH = ROOT / "data" / "transport_layout_plans" / "topology-6ceb4920.g1b1.json"


class SecondaryArmSeedContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.topology = json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))
        cls.plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))

    def test_seed_contains_three_unique_picking_arms(self) -> None:
        topology = self.topology
        self.assertEqual(topology["id"], "topology-6ceb4920")
        self.assertEqual(len(topology["cells"]), 2332)
        self.assertEqual(len(topology["zones"]), 183)
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

        primary_keys = set(arms["picking-arm-01"])
        self.assertEqual(set(arms["arm-01"]), primary_keys)
        self.assertEqual(set(arms["arm-02"]), primary_keys)

    def test_side_arm_installations_are_unoccupied(self) -> None:
        topology = self.topology
        rows = int(topology["grid"]["rows"])
        cols = int(topology["grid"]["cols"])
        for arm_id, row, col in (("arm-01", 109, 63), ("arm-02", 117, 63)):
            self.assertTrue(0 <= row < rows and 0 <= col < cols)
            self.assertFalse(
                any(
                    int(cell["row"]) == row
                    and int(cell["col"]) == col
                    and str(cell.get("floor") or "1F") == "1F"
                    for cell in topology["cells"]
                )
            )
            self.assertFalse(
                any(
                    int(zone.get("x", -1)) <= col < int(zone.get("x", -1)) + int(zone.get("width", 0))
                    and int(zone.get("y", -1)) <= row < int(zone.get("y", -1)) + int(zone.get("height", 0))
                    for zone in topology["zones"]
                )
            )
            occupants = [
                str(node["id"])
                for node in topology["nodes"]
                if int(node.get("row", -1)) == row
                and int(node.get("col", -1)) == col
                and str(node.get("floor") or "1F") == "1F"
            ]
            self.assertEqual(occupants, [arm_id])

    def test_existing_nearest_arm_assignment_matches_design(self) -> None:
        topology = self.topology
        arms = [
            node
            for node in topology["nodes"]
            if node.get("id") in {"arm-01", "arm-02"}
        ]
        work_radius = float(self.plan["pickingArmPreview"]["installation"]["workRadius"])
        ports = [(102, 62), (106, 62), (110, 62), (114, 62), (118, 62)]
        conveyor_positions = {
            (int(cell["row"]), int(cell["col"]))
            for cell in topology["cells"]
            if cell.get("type") == "conveyor"
        }
        self.assertTrue(all(port in conveyor_positions for port in ports))

        assignments = {"arm-01": [], "arm-02": []}
        for row, col in ports:
            owner = min(
                arms,
                key=lambda arm: (
                    abs(row - int(arm["row"])) + abs(col - int(arm["col"])),
                    int(arm["row"]),
                ),
            )
            distance = abs(row - int(owner["row"])) + abs(col - int(owner["col"]))
            self.assertLessEqual(distance, work_radius)
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


if __name__ == "__main__":
    unittest.main()
