import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../lib/api'
import type { ABCClass, ABCResponse } from './types'
import { SYNTHETIC_DEMO_END_DATE, SYNTHETIC_DEMO_START_DATE } from './types'

const CLASS_ORDER: ABCClass[] = ['A', 'B', 'C']

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'ABC 分类请求失败'
}

function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === 'AbortError'
}

export function AbcClassificationPanel({
  active,
  onResult,
}: {
  active: boolean
  onResult: (result: ABCResponse | null) => void
}) {
  const [asOfDate, setAsOfDate] = useState(SYNTHETIC_DEMO_END_DATE)
  const [lookbackDays, setLookbackDays] = useState(30)
  const [result, setResult] = useState<ABCResponse | null>(null)
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [classFilter, setClassFilter] = useState<ABCClass | 'all'>('all')
  const requestSequence = useRef(0)
  const controllerRef = useRef<AbortController | null>(null)

  useEffect(() => () => controllerRef.current?.abort(), [])

  const invalidate = () => {
    requestSequence.current += 1
    controllerRef.current?.abort()
    controllerRef.current = null
    setResult(null)
    onResult(null)
    setStatus('idle')
    setError('')
  }

  const runClassification = async () => {
    const sequence = requestSequence.current + 1
    requestSequence.current = sequence
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    setResult(null)
    onResult(null)
    setStatus('loading')
    setError('')
    try {
      const response = await api.getSkuAbc(asOfDate, lookbackDays, controller.signal)
      if (requestSequence.current !== sequence || controller.signal.aborted) return
      setResult(response)
      onResult(response)
      setStatus('success')
    } catch (requestError) {
      if (requestSequence.current !== sequence || isAbortError(requestError)) return
      setResult(null)
      onResult(null)
      setStatus('error')
      setError(errorMessage(requestError))
    }
  }

  const filteredItems = useMemo(() => {
    const normalizedSearch = search.trim().toLowerCase()
    return (result?.items ?? []).filter((item) => (
      (!normalizedSearch || item.sku_id.toLowerCase().includes(normalizedSearch))
      && (classFilter === 'all' || item.abc_class === classFilter)
    ))
  }, [classFilter, result?.items, search])

  return (
    <section className="sku-algorithm-panel" role="tabpanel" aria-label="动态 ABC 分类" hidden={!active}>
      <div className="sku-config-grid">
        <label>
          分类截止日期
          <input
            aria-label="ABC 分类截止日期"
            type="date"
            min={SYNTHETIC_DEMO_START_DATE}
            max={SYNTHETIC_DEMO_END_DATE}
            value={asOfDate}
            onChange={(event) => { invalidate(); setAsOfDate(event.target.value) }}
          />
        </label>
        <label>
          回看窗口
          <select
            aria-label="ABC 回看窗口"
            value={lookbackDays}
            onChange={(event) => { invalidate(); setLookbackDays(Number(event.target.value)) }}
          >
            <option value={7}>7 天</option>
            <option value={14}>14 天</option>
            <option value={30}>30 天</option>
          </select>
        </label>
        <button className="accent-button" type="button" disabled={status === 'loading' || !asOfDate} onClick={() => void runClassification()}>
          {status === 'loading' ? '正在分类…' : '执行 ABC 分类'}
        </button>
      </div>
      <p className="sku-coverage-note">synthetic_demo 专用覆盖范围：{SYNTHETIC_DEMO_START_DATE} 至 {SYNTHETIC_DEMO_END_DATE}；未来数据源应由元数据动态提供范围。</p>
      {status === 'loading' ? <p role="status" className="muted-copy">正在调用动态 ABC API…</p> : null}
      {status === 'error' ? <p role="alert" className="multi-path-lab-error">ABC 分类失败：{error}</p> : null}
      {status === 'idle' ? <p className="muted-copy">选择截止日期和窗口后执行服务端分类。</p> : null}
      {result ? (
        <>
          <div className="sku-algorithm-meta">
            <span>版本：{result.algorithm_version}</span>
            <span>数据源：{result.data_source}</span>
            <span>实际窗口：{result.lookback_start_date} 至 {result.lookback_end_date}</span>
            <span>权重：{result.config.frequency_weight} / {result.config.quantity_weight} / {result.config.order_line_weight}</span>
            <span>阈值：A {result.config.a_cumulative_share.toFixed(2)} / B {result.config.b_cumulative_share.toFixed(2)}</span>
          </div>
          {result.items.length ? (
            <>
              <div className="sku-class-overview" aria-label="ABC 分类概览">
                {CLASS_ORDER.map((abcClass) => {
                  const count = result.items.filter((item) => item.abc_class === abcClass).length
                  const ratio = result.items.length ? count / result.items.length : 0
                  return (
                    <article key={abcClass} className={`is-class-${abcClass.toLowerCase()}`}>
                      <span>{abcClass} 类 SKU</span>
                      <strong>{count}</strong>
                      <small>{(ratio * 100).toFixed(1)}%</small>
                    </article>
                  )
                })}
              </div>
              <div className="sku-table-toolbar">
                <label>SKU 搜索<input aria-label="ABC SKU 搜索" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="输入 SKU ID" /></label>
                <label>分类筛选<select aria-label="ABC 分类筛选" value={classFilter} onChange={(event) => setClassFilter(event.target.value as ABCClass | 'all')}><option value="all">全部</option>{CLASS_ORDER.map((item) => <option key={item} value={item}>{item} 类</option>)}</select></label>
              </div>
              <div className="table-wrap">
                <table aria-label="动态 ABC 分类明细">
                  <thead><tr><th>SKU ID</th><th>ABC 类别</th><th>ABC score</th><th>出库频次</th><th>出库数量</th><th>订单行数</th><th>频次归一化</th><th>数量归一化</th><th>订单行归一化</th></tr></thead>
                  <tbody>{filteredItems.map((item) => <tr key={item.sku_id}><td>{item.sku_id}</td><td><span className={`sku-class-pill is-${item.abc_class.toLowerCase()}`}>{item.abc_class}</span></td><td>{item.score.toFixed(2)}</td><td>{item.outbound_frequency}</td><td>{item.outbound_quantity}</td><td>{item.order_line_count}</td><td>{item.frequency_normalized.toFixed(3)}</td><td>{item.quantity_normalized.toFixed(3)}</td><td>{item.order_line_normalized.toFixed(3)}</td></tr>)}</tbody>
                </table>
                {!filteredItems.length ? <p className="muted-copy">当前筛选条件下无匹配 SKU。</p> : null}
              </div>
            </>
          ) : <p className="muted-copy">当前截止日期与窗口内没有 SKU 分类数据。</p>}
        </>
      ) : null}
    </section>
  )
}
