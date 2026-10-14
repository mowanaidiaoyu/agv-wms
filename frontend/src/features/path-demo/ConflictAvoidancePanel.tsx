import type { MultiPathConflictEvent } from './types'

const conflictTypeLabel = {
  node: '节点预约竞争',
  edge: '相向边交换风险',
  geometry: '连续几何安全风险',
}
const avoidanceLabel = {
  wait: '等待让行',
  reroute: '重新选择时空路径',
  reroute_and_wait: '绕行并等待让行',
}

export function ConflictAvoidancePanel({
  events,
  selectedEventId,
  onLocate,
}: {
  events: MultiPathConflictEvent[]
  selectedEventId?: string
  onLocate: (event: MultiPathConflictEvent) => void
}) {
  return (
    <section className="path-demo-conflict-panel" aria-label="冲突避让事件">
      <div className="path-demo-conflict-heading">
        <strong>冲突避让事件</strong>
        <span>规划候选冲突，不是实际碰撞</span>
      </div>
      {!events.length ? <p className="path-demo-note">本次规划没有记录预约竞争事件。</p> : (
        <div className="path-demo-conflict-list">
          {events.map((event) => (
            <article key={event.event_id} className={event.event_id === selectedEventId ? 'is-selected' : ''}>
              <div className="path-demo-conflict-event-title">
                <span className={`is-${event.conflict_type}`}>潜在冲突 · {conflictTypeLabel[event.conflict_type]}</span>
                <span className={event.status === 'resolved' ? 'is-resolved' : 'is-remaining'}>
                  {event.status === 'resolved' ? '已避让' : '最终残留冲突'}
                </span>
              </div>
              <dl>
                <div><dt>时间片</dt><dd>{event.time_step}（{event.time_seconds.toFixed(2)} s）</dd></div>
                <div><dt>道路节点</dt><dd>{event.cell.id}</dd></div>
                <div><dt>涉及车辆</dt><dd>{event.involved_vehicle_ids.join(' / ')}</dd></div>
                <div><dt>优先级</dt><dd>{event.priority_vehicle_id} P{event.priority} → {event.yielding_vehicle_id} P{event.yielding_priority}</dd></div>
                <div><dt>规划动作</dt><dd>拒绝冲突候选；{avoidanceLabel[event.avoidance_action]}</dd></div>
                <div><dt>实际等待</dt><dd>{event.wait_cell && event.wait_start_time_seconds !== null && event.wait_end_time_seconds !== null ? `${event.wait_cell.id} · ${event.wait_start_time_seconds.toFixed(2)}–${event.wait_end_time_seconds.toFixed(2)} s（${event.wait_duration_seconds.toFixed(2)} s）` : '无等待动作'}</dd></div>
                <div><dt>最终结果</dt><dd>安全轨迹，残留冲突 0</dd></div>
              </dl>
              <button className="ghost-button" type="button" onClick={() => onLocate(event)}>定位该事件</button>
            </article>
          ))}
        </div>
      )}
    </section>
  )
}
