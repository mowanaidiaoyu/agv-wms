from __future__ import annotations

import math
from copy import deepcopy
from typing import Any


SPEED_CONTRACT_VERSION = "motion-speed-v1"
EPSILON_METERS = 1e-9


def freeze_motion_speed_contract(
    speed_contract: dict[str, Any] | None,
    physics_contract: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Validate cross-contract constraints and return an isolated frozen value.

    The runtime commits at most one occupancy edge per robot per logical tick.
    Therefore the maximum declared speed is the minimum horizontal/vertical
    edge divided by the logical tick duration. Values above that limit would
    advertise a speed the discrete scheduler cannot safely deliver.
    """

    if speed_contract is None:
        return None
    if not isinstance(physics_contract, dict):
        raise ValueError("motionSpeedContract requires motionPhysicsContract")

    frozen = deepcopy(speed_contract)
    if frozen.get("source") != "USER_DEFINED_SIMULATION":
        raise ValueError("motionSpeedContract.source must be USER_DEFINED_SIMULATION")
    if frozen.get("calibrationStatus") != "UNVERIFIED":
        raise ValueError("motionSpeedContract.calibrationStatus must be UNVERIFIED")

    tick_seconds = float(frozen["simTickSeconds"])
    meters_per_cell = float(physics_contract["metersPerCell"])
    level_height = float(physics_contract["shuttleLevelHeightMeters"])
    horizontal_limit = meters_per_cell / tick_seconds
    lift_limit = level_height / tick_seconds
    for field in ("ctuSpeedMetersPerSecond", "shuttleSpeedMetersPerSecond"):
        if float(frozen[field]) > horizontal_limit + EPSILON_METERS:
            raise ValueError(
                f"{field} exceeds the safe discrete scheduler limit "
                f"{horizontal_limit:.6g} m/s for the frozen metersPerCell and simTickSeconds"
            )
    if float(frozen["liftSpeedMetersPerSecond"]) > lift_limit + EPSILON_METERS:
        raise ValueError(
            "liftSpeedMetersPerSecond exceeds the safe one-level-per-tick limit "
            f"{lift_limit:.6g} m/s"
        )
    frozen["schedulerLimits"] = {
        "maxHorizontalMetersPerSecond": horizontal_limit,
        "maxLiftMetersPerSecond": lift_limit,
        "maxCommittedEdgesPerRobotPerTick": 1,
    }
    return frozen


def sim_tick_seconds(config: dict[str, Any], legacy_tick_seconds: float) -> float:
    contract = config.get("motionSpeedContract")
    if isinstance(contract, dict):
        return float(contract["simTickSeconds"])
    return float(legacy_tick_seconds)


def edge_distance_meters(
    physics_contract: dict[str, Any],
    from_row: int,
    from_col: int,
    from_layer: float,
    to_row: int,
    to_col: int,
    to_layer: float,
) -> float:
    horizontal = math.hypot(to_row - from_row, to_col - from_col) * float(
        physics_contract["metersPerCell"]
    )
    vertical = abs(float(to_layer) - float(from_layer)) * float(
        physics_contract["shuttleLevelHeightMeters"]
    )
    return horizontal + vertical


def speed_for_edge(
    speed_contract: dict[str, Any],
    robot_type: str,
    from_layer: float,
    to_layer: float,
) -> float:
    if abs(float(to_layer) - float(from_layer)) > EPSILON_METERS:
        return float(speed_contract["liftSpeedMetersPerSecond"])
    if robot_type == "shuttle":
        return float(speed_contract["shuttleSpeedMetersPerSecond"])
    return float(speed_contract["ctuSpeedMetersPerSecond"])
