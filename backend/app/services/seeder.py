from __future__ import annotations

import json

from ..core.config import DATA_DIR
from ..core.repository import Repository


def seed_defaults(repo: Repository) -> None:
    topology_path = DATA_DIR / "sample_topology.json"
    scenario_path = DATA_DIR / "sample_scenario.json"
    if topology_path.exists():
        topology = json.loads(topology_path.read_text(encoding="utf-8"))
        repo.upsert_topology(topology["id"], topology["name"], topology)
    if scenario_path.exists():
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        repo.upsert_scenario(scenario["id"], scenario["name"], scenario)
