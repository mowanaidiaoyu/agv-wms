import { useMemo } from 'react'
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw, XCircle } from 'lucide-react'
import { useTopologyEditorStore } from '../../store/useTopologyEditorStore'

type EntityTarget =
  | { kind: 'node'; id: string }
  | { kind: 'cell'; id: string }
  | { kind: 'zone'; id: string }
  | { kind: 'resource'; id: string }

function findTargets(message: string, entityIds: Record<'node' | 'cell' | 'zone' | 'resource', string[]>): EntityTarget[] {
  const targets: EntityTarget[] = []
  const kindOrder: Array<keyof typeof entityIds> = ['node', 'zone', 'cell', 'resource']
  for (const kind of kindOrder) {
    let best: { id: string; length: number } | null = null
    for (const id of entityIds[kind]) {
      if (id.length > 1 && message.includes(id) && (!best || id.length > best.length)) {
        best = { id, length: id.length }
      }
    }
    if (best) targets.push({ kind, id: best.id })
  }
  return targets
}

function ValidationRow({
  message,
  level,
}: {
  message: string
  level: 'error' | 'warning'
}) {
  const draft = useTopologyEditorStore((state) => state.draft)
  const selectNode = useTopologyEditorStore((state) => state.selectNode)
  const selectCellById = useTopologyEditorStore((state) => state.selectCellById)
  const selectZone = useTopologyEditorStore((state) => state.selectZone)

  const targets = useMemo(() => {
    const ids: Record<'node' | 'cell' | 'zone' | 'resource', string[]> = {
      node: draft.nodes.map((node) => node.id),
      cell: draft.cells.map((cell) => cell.id),
      zone: draft.zones.map((zone) => zone.id),
      resource: draft.resources.map((resource) => resource.id),
    }
    return findTargets(message, ids)
  }, [message, draft.cells, draft.nodes, draft.zones, draft.resources])

  function handlePick(target: EntityTarget) {
    if (target.kind === 'node') selectNode(target.id)
    else if (target.kind === 'cell') selectCellById(target.id)
    else if (target.kind === 'zone') selectZone(target.id)
  }

  return (
    <li className={`topo-validation-row topo-validation-${level}`}>
      <div className="topo-validation-text">
        {level === 'error' ? <XCircle size={15} /> : <AlertTriangle size={15} />}
        <p>{message}</p>
      </div>
      {targets.length ? (
        <div className="topo-validation-targets">
          {targets.map((target) => (
            <button key={`${target.kind}:${target.id}`} type="button" className="topo-validation-chip" onClick={() => handlePick(target)} title="定位并选中">
              {target.id}
            </button>
          ))}
        </div>
      ) : null}
    </li>
  )
}

export function TopologyValidationPanel({ onValidate }: { onValidate: () => void }) {
  const validation = useTopologyEditorStore((state) => state.validation)
  const validating = useTopologyEditorStore((state) => state.validating)

  if (!validation && !validating) {
    return (
      <div className="topo-validation-empty">
        <CheckCircle2 size={30} strokeWidth={1.2} />
        <p className="muted-copy">还没有校验结果。</p>
        <button className="ghost-button" type="button" onClick={onValidate}>
          运行校验
        </button>
      </div>
    )
  }

  if (validating && !validation) {
    return (
      <div className="topo-validation-empty">
        <Loader2 className="spin" size={30} strokeWidth={1.2} />
        <p className="muted-copy">校验中…</p>
      </div>
    )
  }

  if (!validation) return null

  return (
    <div className="topo-validation-panel">
      <div className="topo-validation-summary">
        <span className={`validation-chip ${validation.valid ? 'valid' : 'invalid'}`}>
          {validation.valid ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
          {validation.valid ? '校验通过' : '存在错误'}
        </span>
        <span className="topo-validation-counts">
          {validation.errors.length} 错误 / {validation.warnings.length} 警告
        </span>
        <button className="ghost-button" type="button" onClick={onValidate} title="重新校验">
          <RefreshCw size={14} className={validating ? 'spin' : ''} />
          重校
        </button>
      </div>

      {validation.errors.length ? (
        <div className="topo-validation-section">
          <p className="topology-property-eyebrow">错误</p>
          <ul className="topo-validation-list">
            {validation.errors.map((message, index) => (
              <ValidationRow key={`e-${index}`} message={message} level="error" />
            ))}
          </ul>
        </div>
      ) : null}

      {validation.warnings.length ? (
        <div className="topo-validation-section">
          <p className="topology-property-eyebrow">警告</p>
          <ul className="topo-validation-list">
            {validation.warnings.map((message, index) => (
              <ValidationRow key={`w-${index}`} message={message} level="warning" />
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}
