from __future__ import annotations

from ..core.repository import Repository
from .seeder import seed_defaults
from .simulation import SimulationService


repo = Repository()
seed_defaults(repo)
simulation_service = SimulationService(repo)
