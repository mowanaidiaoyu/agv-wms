from __future__ import annotations

import json

from ..core.config import DATA_DIR
from ..core.repository import Repository


def seed_defaults(repo: Repository) -> None:
    topology_path = DATA_DIR / "topology_666.json"
    if topology_path.exists():
        topology = json.loads(topology_path.read_text(encoding="utf-8"))
        if not repo.get_topology(topology["id"]):
            repo.upsert_topology(topology["id"], topology["name"], topology)
