from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..inventory_schemas import (
    AccessCandidateAudit,
    AccessCandidateList,
    AccessMapping,
    AccessMappingRequest,
    CtuRoadSemanticAudit,
    CtuTrialDraft,
    CtuTrialDraftRequest,
    InventorySummary,
    StorageLocationInventory,
    SyntheticInventorySeedRequest,
    SyntheticInventorySeedResult,
    SyntheticInventorySupplementPreview,
    SyntheticInventorySupplementRequest,
    SyntheticInventorySupplementResult,
    StorageAccessPathRequest,
    StorageAccessPathResult,
    ZoneInventorySummary,
)
from ..services.inventory import (
    AccessMappingValidationError,
    InventoryConflictError,
    InventoryNotFoundError,
    InventoryService,
)
from ..services.runtime import repo


router = APIRouter(prefix="/inventory", tags=["inventory"])


def service() -> InventoryService:
    return InventoryService(repo)


def _translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, InventoryNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (InventoryConflictError, AccessMappingValidationError)):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/zones", response_model=list[ZoneInventorySummary])
def list_inventory_zones(topology_id: str = Query(min_length=1)):
    try:
        return service().zone_summaries(topology_id)
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.get("/locations", response_model=list[StorageLocationInventory])
def list_inventory_locations(
    topology_id: str = Query(min_length=1),
    zone_id: str | None = None,
    sku_id: str | None = None,
    search: str | None = None,
):
    try:
        return service().list_locations(topology_id, zone_id=zone_id, sku_id=sku_id, search=search)
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.get("/locations/{storage_node_id}", response_model=StorageLocationInventory)
def get_inventory_location(storage_node_id: str, topology_id: str = Query(min_length=1)):
    try:
        return service().get_location(topology_id, storage_node_id)
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.get("/sku/{sku_id}")
def get_inventory_sku(sku_id: str, topology_id: str = Query(min_length=1)):
    try:
        return service().get_sku_distribution(topology_id, sku_id)
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.get("/summary", response_model=InventorySummary)
def get_inventory_summary(topology_id: str = Query(min_length=1)):
    try:
        return service().summary(topology_id)
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.post("/synthetic-demo", response_model=SyntheticInventorySeedResult)
def initialize_synthetic_inventory(payload: SyntheticInventorySeedRequest):
    """Explicit, idempotent demo initialization; never called during startup."""
    try:
        return service().seed_synthetic_demo(payload.topology_id, payload.seed)
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.get("/synthetic-demo/supplement/preview", response_model=SyntheticInventorySupplementPreview)
def preview_synthetic_inventory_supplement(
    topology_id: str = Query(min_length=1),
    count: int | None = Query(default=None, gt=0),
    fill_all: bool = False,
):
    try:
        return service().preview_synthetic_supplement(topology_id, count=count, fill_all=fill_all)
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.post("/synthetic-demo/supplement", response_model=SyntheticInventorySupplementResult)
def supplement_synthetic_inventory(payload: SyntheticInventorySupplementRequest):
    try:
        return service().supplement_synthetic_demo(
            payload.topology_id,
            count=payload.count,
            fill_all=payload.fill_all,
            seed=payload.seed,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.get("/ctu-road-audit", response_model=CtuRoadSemanticAudit)
def audit_ctu_road_semantics(topology_id: str = Query(min_length=1)):
    try:
        return service().audit_ctu_road_semantics(topology_id)
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.get("/ctu-road-drafts", response_model=list[CtuTrialDraft])
def list_ctu_road_drafts(topology_id: str = Query(min_length=1)):
    try:
        return service().list_ctu_topology_drafts(topology_id)
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.post("/ctu-road-drafts", response_model=CtuTrialDraft)
def create_ctu_road_draft(payload: CtuTrialDraftRequest):
    try:
        return service().create_ctu_trial_draft(payload.topology_id, payload.note)
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.put("/access-mappings/{storage_node_id}", response_model=AccessMapping)
def configure_access_mapping(storage_node_id: str, payload: AccessMappingRequest):
    try:
        return service().configure_access_mapping(
            payload.topology_id,
            storage_node_id,
            payload.access_cell_id,
            payload.source,
            reviewed=payload.reviewed,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.get("/access-mappings/{storage_node_id}/candidates", response_model=AccessCandidateList)
def list_access_mapping_candidates(
    storage_node_id: str,
    topology_id: str = Query(min_length=1),
    limit: int = Query(default=12, ge=1, le=50),
):
    try:
        return service().get_access_candidates(topology_id, storage_node_id, limit=limit)
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.get("/access-mappings/audit", response_model=AccessCandidateAudit)
def audit_access_mapping_candidates(topology_id: str = Query(min_length=1)):
    try:
        return service().audit_access_candidates(topology_id)
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.post("/access-path/plan", response_model=StorageAccessPathResult)
def plan_storage_access_path(payload: StorageAccessPathRequest):
    try:
        return service().plan_storage_access_path(
            payload.topology_id,
            payload.storage_node_id,
            payload.start_cell_id,
            payload.algorithm,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
