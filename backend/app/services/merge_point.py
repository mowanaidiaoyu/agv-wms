from __future__ import annotations

from ..schemas import PickingArmTransportCargo, RightWarehouseMergePoint


class MergePointError(ValueError):
    pass


class MergePointCapacityError(MergePointError):
    pass


class DuplicateCargoError(MergePointError):
    pass


def accept_right_warehouse_cargo(
    merge_point: RightWarehouseMergePoint,
    cargo: PickingArmTransportCargo,
) -> RightWarehouseMergePoint:
    """Return a new merge-point state; preview callers never mutate shared state."""
    if merge_point.status == "blocked":
        raise MergePointError(f"Merge point {merge_point.mergePointId} is blocked")
    if cargo.targetMergePointId != merge_point.mergePointId:
        raise MergePointError(
            f"Cargo {cargo.cargoId} targets {cargo.targetMergePointId}, not {merge_point.mergePointId}"
        )
    if cargo.sourceWarehouse != merge_point.acceptedWarehouse:
        raise MergePointError(
            f"Merge point {merge_point.mergePointId} only accepts right-warehouse cargo"
        )
    if cargo.cargoId in merge_point.acceptedCargoIds or cargo.cargoId in merge_point.occupiedCargoIds:
        raise DuplicateCargoError(f"Cargo {cargo.cargoId} has already been accepted")
    if len(merge_point.occupiedCargoIds) >= merge_point.capacity:
        raise MergePointCapacityError(f"Merge point {merge_point.mergePointId} is full")

    next_state = merge_point.model_copy(deep=True)
    next_state.occupiedCargoIds.append(cargo.cargoId)
    next_state.acceptedCargoIds.append(cargo.cargoId)
    next_state.cargoOrderIds[cargo.cargoId] = cargo.orderId
    next_state.status = (
        "full"
        if len(next_state.occupiedCargoIds) >= next_state.capacity
        else "waiting"
    )
    return next_state


def release_right_warehouse_cargo(
    merge_point: RightWarehouseMergePoint,
    cargo_id: str,
) -> RightWarehouseMergePoint:
    if cargo_id not in merge_point.occupiedCargoIds:
        raise MergePointError(f"Cargo {cargo_id} is not waiting at {merge_point.mergePointId}")
    next_state = merge_point.model_copy(deep=True)
    next_state.occupiedCargoIds.remove(cargo_id)
    next_state.status = "waiting" if next_state.occupiedCargoIds else "available"
    return next_state
