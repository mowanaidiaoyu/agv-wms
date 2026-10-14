import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../lib/api'
import type { ABCResponse, HeatForecastMethod, HeatPredictionResponse } from './types'
import { SYNTHETIC_DEMO_END_DATE, SYNTHETIC_DEMO_START_DATE } from './types'

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'SKU 热度预测请求失败'
}

function isAbortError(error: unknown) {
  return error instanceof Error && error.name === 'AbortError'
}

export function HeatPredictionPanel({
  active,
  classification,
}: {
  active: boolean
  classification: ABCResponse | null
}) {
  const [asOfDate, setAsOfDate] = useState(SYNTHETIC_DEMO_END_DATE)
  const [method, setMethod] = useState<HeatForecastMethod>('weighted_moving_average')
  const [lookbackDays, setLookbackDays] = useState<7 | 14 | 30>(7)
  const [selectedSkuIds, setSelectedSkuIds] = useState<string[]>([])
  const [result, setResult] = useState<HeatPredictionResponse | null>(null)
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
  const [error, setError] = useState('')
  const requestSequence = useRef(0)
  const controllerRef = useRef<AbortController | null>(null)

  const invalidate = () => {
    requestSequence.current += 1
    controllerRef.current?.abort()
    controllerRef.current = null
    setResult(null)
    setStatus('idle')
    setError('')
  }

  useEffect(() => () => controllerRef.current?.abort(), [])
  useEffect(() => {
    invalidate()
    const validIds = new Set(classification?.items.map((item) => item.sku_id) ?? [])
    setSelectedSkuIds((current) => {
      const retained = current.filter((skuId) => validIds.has(skuId))
      return retained.length ? retained : (classification?.items.slice(0, 5).map((item) => item.sku_id) ?? [])
    })
  }, [classification])

  const classBySku = useMemo(
    () => new Map(classification?.items.map((item) => [item.sku_id, item]) ?? []),
    [classification?.items],
  )

  const setConfig = (update: () => void) => {
    invalidate()
    update()
  }

  const toggleSku = (skuId: string) => {
    invalidate()
    setSelectedSkuIds((current) => current.includes(skuId)
      ? current.filter((item) => item !== skuId)
      : [...current, skuId])
  }

  const runForecast = async () => {
    if (!selectedSkuIds.length) {
      setStatus('error')
      setError('请至少选择一个 SKU')
      return
    }
    const sequence = requestSequence.current + 1
    requestSequence.current = sequence
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    setResult(null)
    setStatus('loading')
    setError('')
    try {
      const response = await api.predictSkuHeat({
        sku_ids: selectedSkuIds,
        as_of_date: asOfDate,
        method,
        lookback_days: lookbackDays,
        horizon_hours: 24,
      }, controller.signal)
      if (requestSequence.current !== sequence || controller.signal.aborted) return
      setResult(response)
      setStatus('success')
    } catch (requestError) {
      if (requestSequence.current !== sequence || isAbortError(requestError)) return
      setResult(null)
      setStatus('error')
      setError(errorMessage(requestError))
    }
  }

  return (
    <section className="sku-algorithm-panel" role="tabpanel" aria-label="SKU 次日热度预测" hidden={!active}>
      <div className="sku-config-grid">
        <label>预测方法<select aria-label="热度预测方法" value={method} onChange={(event) => setConfig(() => setMethod(event.target.value as HeatForecastMethod))}><option value="moving_average">SMA（移动平均）</option><option value="weighted_moving_average">WMA（加权移动平均）</option></select></label>
        <label>预测窗口<select aria-label="热度预测窗口" value={lookbackDays} onChange={(event) => setConfig(() => setLookbackDays(Number(event.target.value) as 7 | 14 | 30))}><option value={7}>7 天</option><option value={14}>14 天</option><option value={30}>30 天</option></select></label>
        <label>预测截止日期<input aria-label="热度预测截止日期" type="date" min={SYNTHETIC_DEMO_START_DATE} max={SYNTHETIC_DEMO_END_DATE} value={asOfDate} onChange={(event) => setConfig(() => setAsOfDate(event.target.value))} /></label>
        <button className="accent-button" type="button" disabled={status === 'loading' || !classification?.items.length} onClick={() => void runForecast()}>{status === 'loading' ? '正在预测…' : '运行热度预测'}</button>
      </div>
      <p className="sku-coverage-note">预测目标是下一自然日预计 SKU 出库数量，不是概率、ABC score、实时库存量或库位热度。</p>
      {!classification ? <p className="muted-copy">请先在“动态 ABC 分类”页签执行分类，以获取服务端返回的真实 SKU 集合。</p> : null}
      {classification && !classification.items.length ? <p className="muted-copy">ABC 接口未返回可预测 SKU。</p> : null}
      {classification?.items.length ? (
        <div className="sku-selection">
          <div className="sku-selection-heading">
            <strong>选择 SKU（已选 {selectedSkuIds.length}）</strong>
            <div><button type="button" className="ghost-button" onClick={() => setConfig(() => setSelectedSkuIds(classification.items.slice(0, 10).map((item) => item.sku_id)))}>选择前 10 个</button><button type="button" className="ghost-button" onClick={() => setConfig(() => setSelectedSkuIds([]))}>清空</button></div>
          </div>
          <div className="sku-checkbox-grid">
            {classification.items.map((item) => <label key={item.sku_id}><input type="checkbox" checked={selectedSkuIds.includes(item.sku_id)} onChange={() => toggleSku(item.sku_id)} /><span>{item.sku_id}</span><small>{item.abc_class} 类</small></label>)}
          </div>
        </div>
      ) : null}
      {status === 'loading' ? <p role="status" className="muted-copy">正在调用 SKU 热度预测 API…</p> : null}
      {status === 'error' ? <p role="alert" className="multi-path-lab-error">热度预测失败：{error}</p> : null}
      {result ? (
        <>
          <div className="sku-algorithm-meta">
            <span>版本：{result.algorithm_version}</span><span>数据源：{result.data_source}</span><span>方法：{result.method}</span><span>窗口：{result.lookback_days} 天</span><span>截止日期：{result.as_of_date}</span><span>预测日期：{result.forecast_date}</span>
          </div>
          {result.predictions.length ? (
            <div className="table-wrap">
              <table aria-label="SKU 次日热度预测结果"><thead><tr><th>SKU ID</th><th>当前 ABC</th><th>ABC score</th><th>预测方法</th><th>历史窗口</th><th>截止日期</th><th>预测日期</th><th>下一自然日预计出库数量</th></tr></thead><tbody>{result.predictions.map((item) => { const currentClass = classBySku.get(item.sku_id); return <tr key={item.sku_id}><td>{item.sku_id}</td><td>{currentClass?.abc_class ?? '—'}</td><td>{currentClass?.score.toFixed(2) ?? '—'}</td><td>{result.method}</td><td>{result.lookback_days} 天</td><td>{result.as_of_date}</td><td>{result.forecast_date}</td><td>{item.predicted_heat.toFixed(2)}（预计出库数量）</td></tr> })}</tbody></table>
            </div>
          ) : <p className="muted-copy">本次预测没有返回结果。</p>}
        </>
      ) : null}
    </section>
  )
}
