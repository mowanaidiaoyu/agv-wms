import type {
  LayoutReferenceNode,
  LayoutRoute,
  ProposedLayoutConnection,
  ProposedLayoutFacility,
  TransportLayoutPlan,
} from '../types'

export type LayoutPreviewSelection = {
  references: LayoutReferenceNode[]
  facilities: ProposedLayoutFacility[]
  connections: ProposedLayoutConnection[]
  routes: LayoutRoute[]
}

export function assertTransportLayoutCompatible(
  plan: TransportLayoutPlan,
  topologyId: string,
  topologyRevision?: string,
): void {
  if (plan.topologyId !== topologyId) {
    throw new Error(`Layout topologyId=${plan.topologyId} does not match ${topologyId}`)
  }
  if (topologyRevision && plan.topologyRevision !== topologyRevision) {
    throw new Error(
      `Layout topologyRevision=${plan.topologyRevision} does not match ${topologyRevision}`,
    )
  }
}

export function selectLayoutPreview(
  plan: TransportLayoutPlan,
  optionId: string,
): LayoutPreviewSelection {
  const option = plan.options.find((item) => item.optionId === optionId)
  if (!option) return { references: [], facilities: [], connections: [], routes: [] }
  const facilityIds = new Set(option.facilityIds)
  const connectionIds = new Set(option.connectionIds)
  return {
    references: [...plan.references],
    facilities: plan.facilities.filter((item) => facilityIds.has(item.facilityId)),
    connections: plan.connections.filter((item) => connectionIds.has(item.connectionId)),
    routes: plan.routes.filter((item) => item.optionId === optionId),
  }
}

export function findLayoutPreviewItem(
  plan: TransportLayoutPlan,
  itemId: string,
): LayoutReferenceNode | ProposedLayoutFacility | ProposedLayoutConnection | undefined {
  return plan.references.find((item) => item.referenceId === itemId)
    ?? plan.facilities.find((item) => item.facilityId === itemId)
    ?? plan.connections.find((item) => item.connectionId === itemId)
}
