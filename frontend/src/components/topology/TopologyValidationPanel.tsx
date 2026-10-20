import { useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronUp, Loader2, RefreshCw, XCircle } from 'lucide-react'
import { useTopologyEditorStore } from '../../store/useTopologyEditorStore'
import type { TopologyValidationCategory, TopologyValidationIssue } from '../../types'

const PAGE_SIZE = 10

type EntityTarget = { kind: 'node' | 'cell' | 'zone' | 'resource'; id: string }

function LegacyValidationRow({ message, level }: { message: string; level: 'error' | 'warning' }) {
  const draft = useTopologyEditorStore((state) => state.draft)
  const issue = useMemo<TopologyValidationIssue>(() => {
    const candidates: Array<EntityTarget> = [
      ...draft.nodes.map((item) => ({ kind: 'node' as const, id: item.id })),
      ...draft.zones.map((item) => ({ kind: 'zone' as const, id: item.id })),
      ...draft.cells.map((item) => ({ kind: 'cell' as const, id: item.id })),
      ...draft.resources.map((item) => ({ kind: 'resource' as const, id: item.id })),
    ]
    const target = candidates
      .filter((item) => item.id.length > 1 && message.includes(item.id))
      .sort((a, b) => b.id.length - a.id.length)[0]
    return {
      category: 'structure', severity: level, code: 'LEGACY', message,
      entity_kind: target?.kind ?? null, entity_id: target?.id ?? null,
    }
  }, [draft.cells, draft.nodes, draft.resources, draft.zones, level, message])
  return <ValidationIssueRow issue={issue} />
}

function ValidationIssueRow({ issue }: { issue: TopologyValidationIssue }) {
  const selectNode = useTopologyEditorStore((state) => state.selectNode)
  const selectCellById = useTopologyEditorStore((state) => state.selectCellById)
  const selectZone = useTopologyEditorStore((state) => state.selectZone)

  const locate = () => {
    if (!issue.entity_id) return
    if (issue.entity_kind === 'node') selectNode(issue.entity_id)
    else if (issue.entity_kind === 'cell') selectCellById(issue.entity_id)
    else if (issue.entity_kind === 'zone') selectZone(issue.entity_id)
  }

  return (
    <li className={`topo-validation-row topo-validation-${issue.severity}`}>
      <div className="topo-validation-text">
        {issue.severity === 'error' ? <XCircle size={15} /> : <AlertTriangle size={15} />}
        <p>{issue.message}</p>
      </div>
      {issue.entity_id ? (
        <button type="button" className="topo-validation-chip" onClick={locate} title="定位并选中">
          {issue.entity_id}
        </button>
      ) : null}
    </li>
  )
}

function CategorySection({ category }: { category: TopologyValidationCategory }) {
  const [expanded, setExpanded] = useState(false)
  const [page, setPage] = useState(0)
  const codeGroups = useMemo(() => {
    const groups = new Map<string, { count: number; sample: TopologyValidationIssue }>()
    for (const issue of category.issues) {
      const current = groups.get(issue.code)
      if (current) current.count += 1
      else groups.set(issue.code, { count: 1, sample: issue })
    }
    return [...groups.entries()]
  }, [category.issues])
  const pageCount = Math.max(1, Math.ceil(category.issues.length / PAGE_SIZE))
  const pageItems = category.issues.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

  return (
    <section className={`topo-validation-category is-${category.category}`}>
      <button
        type="button"
        className="topo-validation-category-heading"
        onClick={() => setExpanded((current) => !current)}
        aria-expanded={expanded}
      >
        <span>{category.label}</span>
        <small>{category.error_count} 错误 / {category.warning_count} 警告</small>
        {expanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
      </button>
      {codeGroups.length ? (
        <div className="topo-validation-code-summary">
          {codeGroups.map(([code, group]) => (
            <div key={code}>
              <strong>{group.count} × {code}</strong>
              <span>{group.sample.message}</span>
            </div>
          ))}
        </div>
      ) : <p className="muted-copy">此分类没有问题。</p>}
      {expanded && category.issues.length ? (
        <>
          <ul className="topo-validation-list">
            {pageItems.map((issue, index) => (
              <ValidationIssueRow key={`${issue.code}-${issue.entity_id ?? index}-${page * PAGE_SIZE + index}`} issue={issue} />
            ))}
          </ul>
          {pageCount > 1 ? (
            <div className="topo-validation-pagination">
              <button type="button" className="ghost-button" disabled={page === 0} onClick={() => setPage((value) => value - 1)}>上一页</button>
              <span>{page + 1} / {pageCount}</span>
              <button type="button" className="ghost-button" disabled={page + 1 >= pageCount} onClick={() => setPage((value) => value + 1)}>下一页</button>
            </div>
          ) : null}
        </>
      ) : null}
    </section>
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
        <button className="ghost-button" type="button" onClick={onValidate}>运行校验</button>
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

  const categories = validation.categories ?? []
  return (
    <div className="topo-validation-panel">
      <div className="topo-validation-summary">
        <span className={`validation-chip ${validation.valid ? 'valid' : 'invalid'}`}>
          {validation.valid ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
          {validation.valid ? '结构校验通过' : '存在错误'}
        </span>
        <span className="topo-validation-counts">{validation.errors.length} 错误 / {validation.warnings.length} 警告</span>
        <button className="ghost-button" type="button" onClick={onValidate} title="重新校验">
          <RefreshCw size={14} className={validating ? 'spin' : ''} />重校
        </button>
      </div>

      {categories.length ? categories.map((category) => (
        <CategorySection key={category.category} category={category} />
      )) : (
        <>
          {validation.errors.length ? (
            <ul className="topo-validation-list">
              {validation.errors.map((message, index) => <LegacyValidationRow key={`e-${index}`} message={message} level="error" />)}
            </ul>
          ) : null}
          {validation.warnings.length ? (
            <ul className="topo-validation-list">
              {validation.warnings.map((message, index) => <LegacyValidationRow key={`w-${index}`} message={message} level="warning" />)}
            </ul>
          ) : null}
        </>
      )}
    </div>
  )
}
