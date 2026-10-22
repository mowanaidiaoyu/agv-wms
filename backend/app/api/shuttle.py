from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Query

from ..services.runtime import repo
from ..services.shuttle_pathfinding import plan_shuttle_path
from ..services.shuttle_storage_audit import (
    audit_shuttle_storage,
)
from ..services.shuttle_storage_review import (
    ShuttleStorageConflictError,
    ShuttleStorageNotFoundError,
    ShuttleStorageReviewService,
)
from ..services.shuttle_demo_transfer import (
    ShuttleDemoTransferConflict,
    ShuttleDemoTransferNotFound,
    ShuttleDemoTransferService,
)
from ..services.shuttle_multi_planner import (
    ShuttleMultiPlanningError,
    build_shuttle_multi_scenarios,
    failed_shuttle_multi_response,
    plan_shuttle_multi,
)
from ..services.shuttle_topology import (
    SHUTTLE_COORDINATE_CONTRACT_REVISION,
    ShuttleConfigStore,
    build_shuttle_track_graph,
    level_summaries,
    validate_shuttle_configuration,
)
from ..shuttle_lift_schemas import (
    ShuttleLiftsResponse,
    ShuttlePlanRequest,
    ShuttlePlanResponse,
    ShuttleTopologyLevelsResponse,
    ShuttleTracksResponse,
)
from ..shuttle_multi_schemas import (
    ShuttleMultiPlanRequest,
    ShuttleMultiPlanResponse,
    ShuttleMultiScenariosResponse,
)
from ..shuttle_storage_schemas import (
    ShuttleStorageAuditResponse,
    ShuttleStorageCandidatePage,
    ShuttleStorageCandidate,
    ShuttleStorageDraftRequest,
    ShuttleStorageLocationRecord,
    ShuttleStorageReviewHistory,
    ShuttleStorageReviewRequest,
    ShuttleTrialAreaResponse,
    ShuttleDemoDropoffDraftRequest,
    ShuttleDemoDropoffRecord,
    ShuttleDemoDropoffReviewRequest,
)
from ..shuttle_transfer_schemas import (
    ShuttleDemoInventoryInitializeRequest,
    ShuttleDemoInventoryInitializeResponse,
    ShuttleDemoInventoryLedgerEntry,
    ShuttleDemoLocationInventory,
    ShuttleDemoLocationPage,
    ShuttleDemoSkuSummary,
    ShuttleTransferAdvanceRequest,
    ShuttleTransferCreateRequest,
    ShuttleTransferFailRequest,
    ShuttleTransferTask,
)


router = APIRouter(prefix="/shuttle", tags=["shuttle-offline-demo"])


def _review_service() -> ShuttleStorageReviewService:
    return ShuttleStorageReviewService(repo)


def _transfer_service() -> ShuttleDemoTransferService:
    return ShuttleDemoTransferService(repo)


def _translate_review_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ShuttleStorageNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ShuttleStorageConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _translate_transfer_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ShuttleDemoTransferNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ShuttleDemoTransferConflict):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _context(topology_id: str):
    record = repo.get_topology(topology_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Topology not found")
    graph = build_shuttle_track_graph(record["payload"])
    validation = validate_shuttle_configuration(
        record["payload"], ShuttleConfigStore().load(topology_id), graph
    )
    return record, graph, validation


@router.get("/topologies/{topology_id}/levels", response_model=ShuttleTopologyLevelsResponse)
def get_shuttle_levels(topology_id: str):
    record, graph, validation = _context(topology_id)
    return {
        "topology_id": topology_id,
        "topology_name": record["name"],
        "topology_revision": validation["topology_revision"],
        "topology_version": str(record["payload"].get("meta", {}).get("version", "unknown")),
        "config_revision": validation["config_revision"],
        "coordinate_contract_revision": SHUTTLE_COORDINATE_CONTRACT_REVISION,
        "verification_status": validation["verification_status"],
        "planning_enabled": validation["planning_enabled"],
        "lift_candidate_count": validation["lift_candidate_count"],
        "lift_selection_basis": validation["lift_selection_basis"],
        "levels": level_summaries(graph),
        "warnings": validation["warnings"],
    }


@router.get("/topologies/{topology_id}/tracks", response_model=ShuttleTracksResponse)
def get_shuttle_tracks(topology_id: str, level: int | None = Query(default=None, ge=1, le=4)):
    _, graph, validation = _context(topology_id)
    nodes = [
        node for node in graph.values() if level is None or int(node["shuttle_level"]) == level
    ]
    return {
        "topology_id": topology_id,
        "topology_revision": validation["topology_revision"],
        "config_revision": validation["config_revision"],
        "coordinate_contract_revision": SHUTTLE_COORDINATE_CONTRACT_REVISION,
        "nodes": sorted(nodes, key=lambda item: item["track_node_id"]),
    }


@router.get("/topologies/{topology_id}/lifts", response_model=ShuttleLiftsResponse)
def get_shuttle_lifts(topology_id: str):
    _, _, validation = _context(topology_id)
    return {
        "topology_id": topology_id,
        "topology_revision": validation["topology_revision"],
        "config_revision": validation["config_revision"],
        "coordinate_contract_revision": SHUTTLE_COORDINATE_CONTRACT_REVISION,
        "planning_enabled": validation["planning_enabled"],
        "lifts": validation["lifts"],
        "warnings": validation["warnings"],
    }


@router.get(
    "/topologies/{topology_id}/storage-audit",
    response_model=ShuttleStorageAuditResponse,
)
def get_shuttle_storage_audit(topology_id: str):
    record, _, _ = _context(topology_id)
    return audit_shuttle_storage(record["payload"])


@router.get(
    "/topologies/{topology_id}/storage-candidates",
    response_model=ShuttleStorageCandidatePage,
)
def get_shuttle_storage_candidates(
    topology_id: str,
    level: int | None = Query(default=None, ge=1, le=4),
    zone_id: str | None = Query(default=None, min_length=1),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    search: str | None = Query(default=None, min_length=1),
    has_adjacent_track: bool | None = Query(default=None),
):
    try:
        return _review_service().list_candidates(
            topology_id, level=level, zone_id=zone_id, search=search,
            has_adjacent_track=has_adjacent_track, offset=offset, limit=limit,
        )
    except Exception as exc:
        raise _translate_review_error(exc) from exc


@router.get(
    "/topologies/{topology_id}/storage-candidates/{location_id}",
    response_model=ShuttleStorageCandidate,
)
def get_shuttle_storage_candidate(topology_id: str, location_id: str):
    try:
        return _review_service().get_candidate(topology_id, location_id)
    except Exception as exc:
        raise _translate_review_error(exc) from exc


@router.get(
    "/topologies/{topology_id}/storage-trial-area",
    response_model=ShuttleTrialAreaResponse,
)
def get_shuttle_storage_trial_area(topology_id: str):
    try:
        return _review_service().trial_area(topology_id)
    except Exception as exc:
        raise _translate_review_error(exc) from exc


@router.get(
    "/topologies/{topology_id}/storage-mappings",
    response_model=list[ShuttleStorageLocationRecord],
)
def list_shuttle_storage_mappings(topology_id: str):
    try:
        return _review_service().list_mappings(topology_id)
    except Exception as exc:
        raise _translate_review_error(exc) from exc


@router.post(
    "/topologies/{topology_id}/storage-mappings/drafts",
    response_model=ShuttleStorageLocationRecord,
)
def save_shuttle_storage_mapping_draft(topology_id: str, request: ShuttleStorageDraftRequest):
    try:
        return _review_service().save_draft(
            topology_id, request.location_id, request.pick_face,
            request.access_track_node_id, request.reviewer, request.review_note,
        )
    except Exception as exc:
        raise _translate_review_error(exc) from exc


@router.post(
    "/topologies/{topology_id}/storage-mappings/{location_id}/review",
    response_model=ShuttleStorageLocationRecord,
)
def review_shuttle_storage_mapping(
    topology_id: str, location_id: str, request: ShuttleStorageReviewRequest
):
    try:
        return _review_service().review_mapping(
            topology_id, location_id, request.decision, request.reviewer,
            request.review_note, request.explicit_confirmation,
        )
    except Exception as exc:
        raise _translate_review_error(exc) from exc


@router.get(
    "/topologies/{topology_id}/storage-mappings/{location_id}/history",
    response_model=list[ShuttleStorageReviewHistory],
)
def get_shuttle_storage_mapping_history(topology_id: str, location_id: str):
    try:
        return _review_service().review_history(topology_id, location_id)
    except Exception as exc:
        raise _translate_review_error(exc) from exc


@router.get(
    "/topologies/{topology_id}/demo-dropoffs",
    response_model=list[ShuttleDemoDropoffRecord],
)
def list_shuttle_demo_dropoffs(topology_id: str):
    try:
        return _review_service().list_dropoffs(topology_id)
    except Exception as exc:
        raise _translate_review_error(exc) from exc


@router.post(
    "/topologies/{topology_id}/demo-dropoffs/drafts",
    response_model=ShuttleDemoDropoffRecord,
)
def save_shuttle_demo_dropoff_draft(topology_id: str, request: ShuttleDemoDropoffDraftRequest):
    try:
        return _review_service().save_dropoff_draft(
            topology_id, request.track_node_id, request.label,
            request.reviewer, request.review_note,
        )
    except Exception as exc:
        raise _translate_review_error(exc) from exc


@router.post(
    "/topologies/{topology_id}/demo-dropoffs/{dropoff_id}/review",
    response_model=ShuttleDemoDropoffRecord,
)
def review_shuttle_demo_dropoff(
    topology_id: str, dropoff_id: str, request: ShuttleDemoDropoffReviewRequest
):
    try:
        return _review_service().review_dropoff(
            topology_id, dropoff_id, request.decision, request.reviewer,
            request.review_note, request.explicit_confirmation,
        )
    except Exception as exc:
        raise _translate_review_error(exc) from exc


@router.post(
    "/demo-inventory/initialize",
    response_model=ShuttleDemoInventoryInitializeResponse,
)
def initialize_shuttle_demo_inventory(request: ShuttleDemoInventoryInitializeRequest):
    try:
        return _transfer_service().initialize_inventory(request.topology_id)
    except Exception as exc:
        raise _translate_transfer_error(exc) from exc


@router.get(
    "/topologies/{topology_id}/demo-inventory/skus",
    response_model=list[ShuttleDemoSkuSummary],
)
def list_shuttle_demo_skus(
    topology_id: str,
    search: str | None = Query(default=None, min_length=1),
):
    try:
        return _transfer_service().list_skus(topology_id, search)
    except Exception as exc:
        raise _translate_transfer_error(exc) from exc


@router.get(
    "/topologies/{topology_id}/demo-inventory/locations",
    response_model=ShuttleDemoLocationPage,
)
def list_shuttle_demo_inventory_locations(
    topology_id: str,
    level: int | None = Query(default=None, ge=1, le=4),
    sku_id: str | None = Query(default=None, min_length=1),
    search: str | None = Query(default=None, min_length=1),
    transfer_eligible: bool | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=1000, ge=1, le=5000),
):
    try:
        return _transfer_service().list_locations(
            topology_id,
            level=level,
            sku_id=sku_id,
            search=search,
            transfer_eligible=transfer_eligible,
            offset=offset,
            limit=limit,
        )
    except Exception as exc:
        raise _translate_transfer_error(exc) from exc


@router.get(
    "/topologies/{topology_id}/demo-inventory/locations/{location_id}",
    response_model=ShuttleDemoLocationInventory,
)
def get_shuttle_demo_inventory_location(topology_id: str, location_id: str):
    try:
        return _transfer_service().get_location(topology_id, location_id)
    except Exception as exc:
        raise _translate_transfer_error(exc) from exc


@router.post("/demo-transfers", response_model=ShuttleTransferTask)
def create_shuttle_demo_transfer(request: ShuttleTransferCreateRequest):
    try:
        return _transfer_service().create_task(
            request.topology_id,
            request.source_location_id,
            request.destination_location_id,
            request.sku_id,
            request.quantity,
            request.algorithm,
            request.vehicle_id,
            request.current_track_node_id,
        )
    except Exception as exc:
        raise _translate_transfer_error(exc) from exc


@router.get("/demo-transfers/{task_id}", response_model=ShuttleTransferTask)
def get_shuttle_demo_transfer(task_id: str):
    try:
        return _transfer_service().get_task(task_id)
    except Exception as exc:
        raise _translate_transfer_error(exc) from exc


@router.post("/demo-transfers/{task_id}/plan-start", response_model=ShuttleTransferTask)
def plan_start_shuttle_demo_transfer(task_id: str):
    try:
        return _transfer_service().plan_and_start(task_id)
    except Exception as exc:
        raise _translate_transfer_error(exc) from exc


@router.post("/demo-transfers/{task_id}/advance", response_model=ShuttleTransferTask)
def advance_shuttle_demo_transfer(task_id: str, request: ShuttleTransferAdvanceRequest):
    try:
        return _transfer_service().advance(task_id, request.elapsed_seconds)
    except Exception as exc:
        raise _translate_transfer_error(exc) from exc


@router.post("/demo-transfers/{task_id}/cancel", response_model=ShuttleTransferTask)
def cancel_shuttle_demo_transfer(task_id: str):
    try:
        return _transfer_service().cancel(task_id)
    except Exception as exc:
        raise _translate_transfer_error(exc) from exc


@router.post("/demo-transfers/{task_id}/fail", response_model=ShuttleTransferTask)
def fail_shuttle_demo_transfer(task_id: str, request: ShuttleTransferFailRequest):
    try:
        return _transfer_service().fail(task_id, request.reason)
    except Exception as exc:
        raise _translate_transfer_error(exc) from exc


@router.get(
    "/demo-transfers/{task_id}/ledger",
    response_model=list[ShuttleDemoInventoryLedgerEntry],
)
def get_shuttle_demo_transfer_ledger(task_id: str):
    try:
        return _transfer_service().list_ledger(task_id)
    except Exception as exc:
        raise _translate_transfer_error(exc) from exc


@router.post("/plan", response_model=ShuttlePlanResponse)
def create_shuttle_plan(request: ShuttlePlanRequest):
    _, graph, validation = _context(request.topology_id)
    if not validation["planning_enabled"]:
        reason = "; ".join(validation["warnings"]) or "Shuttle topology is not verified for planning."
        return {
            "topology_id": request.topology_id,
            "topology_revision": validation["topology_revision"],
            "config_revision": validation["config_revision"],
            "coordinate_contract_revision": SHUTTLE_COORDINATE_CONTRACT_REVISION,
            "algorithm": request.algorithm,
            "result_status": "failed",
            "failure_reason": reason,
        }
    result = plan_shuttle_path(
        graph,
        validation["lifts"],
        request.start_track_node,
        request.end_track_node,
        request.algorithm,
    )
    return {
        "topology_id": request.topology_id,
        "topology_revision": validation["topology_revision"],
        "config_revision": validation["config_revision"],
        "coordinate_contract_revision": SHUTTLE_COORDINATE_CONTRACT_REVISION,
        **result,
    }


@router.get("/multi-scenarios", response_model=ShuttleMultiScenariosResponse)
def get_shuttle_multi_scenarios(topology_id: str = Query(min_length=1)):
    _, graph, validation = _context(topology_id)
    if not validation["planning_enabled"]:
        raise HTTPException(
            status_code=409,
            detail="The ShuttleGraph and both lifts must pass configuration validation.",
        )
    try:
        scenarios = build_shuttle_multi_scenarios(graph, validation["lifts"])
    except ShuttleMultiPlanningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "topology_id": topology_id,
        "topology_revision": validation["topology_revision"],
        "shuttle_config_revision": validation["config_revision"],
        "coordinate_contract_version": SHUTTLE_COORDINATE_CONTRACT_REVISION,
        "scenarios": scenarios,
    }


@router.post("/plan-multi", response_model=ShuttleMultiPlanResponse)
def create_shuttle_multi_plan(request: ShuttleMultiPlanRequest):
    started = time.perf_counter()
    record, graph, validation = _context(request.topology_id)
    topology_version = str(record["payload"].get("meta", {}).get("version", "unknown"))
    vehicles = [vehicle.model_dump() for vehicle in request.vehicle_requests]
    options = request.planning_options.model_dump()
    mismatch = None
    if request.topology_revision != validation["topology_revision"]:
        mismatch = (
            f"Topology revision mismatch: request={request.topology_revision}, "
            f"current={validation['topology_revision']}"
        )
    elif request.shuttle_config_revision != validation["config_revision"]:
        mismatch = (
            f"Shuttle config revision mismatch: request={request.shuttle_config_revision}, "
            f"current={validation['config_revision']}"
        )
    elif not validation["planning_enabled"]:
        mismatch = "; ".join(validation["warnings"]) or "Shuttle planning is not enabled."
    if mismatch:
        return failed_shuttle_multi_response(
            topology_id=request.topology_id,
            topology_version=topology_version,
            topology_revision=validation["topology_revision"],
            config_revision=validation["config_revision"],
            vehicles=vehicles,
            options=options,
            reason=mismatch,
            started=started,
            failure_code="configuration_mismatch",
            failure_details={
                "request_topology_revision": request.topology_revision,
                "current_topology_revision": validation["topology_revision"],
                "request_shuttle_config_revision": request.shuttle_config_revision,
                "current_shuttle_config_revision": validation["config_revision"],
            },
        )
    try:
        return plan_shuttle_multi(
            graph,
            validation["lifts"],
            vehicles,
            options,
            topology_id=request.topology_id,
            topology_version=topology_version,
            topology_revision=validation["topology_revision"],
            config_revision=validation["config_revision"],
        )
    except ShuttleMultiPlanningError as exc:
        return failed_shuttle_multi_response(
            topology_id=request.topology_id,
            topology_version=topology_version,
            topology_revision=validation["topology_revision"],
            config_revision=validation["config_revision"],
            vehicles=vehicles,
            options=options,
            reason=str(exc),
            started=started,
            failure_code=exc.failure_code,
            vehicle_id=exc.vehicle_id,
            expanded_states=exc.expanded_states,
            max_expanded_states=exc.max_expanded_states,
            failure_details=exc.details,
        )
