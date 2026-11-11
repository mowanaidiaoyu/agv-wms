# Deadlock Episode Contract v1

## 1. 范围与安全边界

本合同定义 AGV-WMS 仿真中“车辆阻塞”“可验证循环等待”和“循环等待 Episode”的离线计算口径。当前实现位于 `backend/app/services/deadlock_episodes.py`，是无数据库、无 FastAPI、无 runtime 依赖的纯函数模块。

本模块尚未接入真实 Run，不会修改车辆路径、调度优先级或设备状态，也没有实现自动死锁解锁。它不是现场安全控制系统，不能用于替代 PLC、安全回路、急停或人工处置流程。

## 2. 每 Tick 输入合同

每个已观测 Tick 应提供完整车辆集合。每辆车至少包含：

- `robot_id`：稳定且唯一的车辆 ID。
- `current_cell_id`：车辆实际占用网格。
- `next_cell_id`：当前计划尝试进入的下一网格。
- `is_waiting`：该 Tick 是否处于已观察等待状态。
- `wait_ticks`：上游记录的连续等待计数，仅作为最大等待参考，不代替本模块自己的连续 Tick 计算。
- `blocked_cell_id`：可选的被阻塞网格。
- `declared_blocker_robot_id`：可选的旧字段，仅用于质量诊断，不能单独建立车辆等待边。
- `wait_resource_type`、`wait_resource_id`：预约、输送线、升降机、轨道或其他资源等待的明确说明。
- `task_progress_token`：可选的任务阶段/版本标记，用于确认任务实际推进。

调用者可以传入正式拓扑的合法 cell ID 集合。未传入时只能进行非空值和占用关系检查，不能宣称已完成拓扑合法性验证。

## 3. Wait-for Graph 定义

有向边 `A → B` 表示：

1. A 在当前 Tick 被观察为等待；
2. A 的 `next_cell_id` 合法；
3. B 的 `current_cell_id` 与 A 的 `next_cell_id` 相同；
4. 该网格只有一个可确认的其他车辆占用者；
5. A 与 B 不是同一车辆。

`blockedByRobotId` 或 `declared_blocker_robot_id` 不能作为唯一证据。声明的阻塞者未实际占用下一网格时，记录 `UNKNOWN_BLOCKER / DECLARED_BLOCKER_UNVERIFIED`，不建立车辆等待边。

以下情况不建立车辆等待边：

- 下一网格缺失或不在给定合法 cell 集合中；
- 自引用；
- 同一网格存在多个占用者，无法唯一确认；
- 下一网格没有车辆占用者；
- 等待原因明确为预约、输送线、升降机、Shuttle 轨道或其他设备资源。

无法确认占用者时保留 `UNKNOWN_BLOCKER` 边；资源等待保留对应资源类型边。两者都参与阻塞统计，但不进入车辆循环检测。

## 4. 循环检测算法

循环检测只使用已验证的 `ROBOT` 边。实现对有向图执行 Tarjan 强连通分量算法：包含至少两辆车的强连通分量被识别为可验证等待环。该方法支持两车、三车、四车及更多车辆的环，也支持同一 Tick 的多个独立环。

车辆、边和强连通分量均在计算前排序；重复边去重；自引用被排除。因此检测结果和车辆输入顺序无关。

车辆 A 等待车辆 B 或线性等待链都不是循环。现有每 4 Tick 节流的 `deadlock_detected` 告警也不参与环检测或 Episode 去重。

## 5. 稳定指纹与 Episode ID

Cycle 指纹是以下规范化 JSON 的 SHA-256：

- 排序后的参与车辆 ID；
- 排序后的等待边；
- 每条边的车辆、资源、阻塞网格、下一网格和证据类型。

Episode ID 为 `run_id + cycle_fingerprint + first_observed_tick` 的 SHA-256 截断值。它不使用随机数，同一 Run、同一环、同一首次 Tick 的 ID 可重现。

同一个开放环按指纹复用 Episode；重复处理相同 Tick 直接返回原状态，不重复创建事件或累计车辆 Tick。

## 6. 状态机

### `OBSERVED_BLOCK`

单车在一个完整 Tick 被观察为等待。该状态记录在单车阻塞统计中，不等于循环，也不会单独创建 cycle Episode。

### `SUSPECTED_CYCLE`

首次检测到可验证车辆等待环。Episode 在此时建立。

### `PERSISTENT_WAIT_CYCLE`

同一环在真实连续 Tick 中达到 `persistent_cycle_ticks`。它表示仿真中确认的持续循环等待，不自动等于物理设备意义上的不可恢复死锁。

### `MITIGATION_OBSERVED`

参与车辆被观察到绕行、让行、回退等处置动作。处置动作本身不表示已解除，也不会填写 `resolved_at_tick`。

### `RESOLVED`

必须同时满足：

1. 原等待环连续消失达到 `quiet_window_ticks`；
2. 参与车辆观测完整；
3. 至少存在一项真实进展证据：车辆进入新网格，或任务进度标记发生变化。

只把状态从 `blocked` 改成 `moving` 不属于进展证据。

### `UNRESOLVED_AT_RUN_END`

Run 结束时等待环仍在当前观测中存在。该事件计入未解除事件，不能从成功率分母中删除。

### `ENDED_UNVERIFIED`

用于以下情况：

- 环消失但缺少实际进展证据；
- 参与车辆观测缺失；
- Tick 出现缺口；
- Run 在 quiet window 完成前结束。

该状态不能计为成功解除，也不能产生确认解除耗时。

## 7. 持续阈值与 quiet window

默认值：

- `persistent_cycle_ticks = 3`
- `quiet_window_ticks = 2`

默认值仅用于软件合同和单元测试，尚未经现场节拍、车辆控制器或生产数据标定，不能宣称是正式安全阈值。

持续阈值只接受相邻 Tick。环短暂消失但尚未完成 quiet window 时保持同一个开放 Episode；重新出现后不会重复建档，但连续环计数从新观测开始。只有完整 quiet window 确认 Episode 结束后，相同环再次出现才建立新 Episode。

## 8. 单车连续阻塞与累计口径

每辆车记录：

- `blocked_start_tick`
- `blocked_end_tick`
- `continuous_blocked_ticks`
- `max_continuous_blocked_ticks`
- `cumulative_blocked_robot_ticks`
- `blocker_robot_id`
- `blocked_cell_id`
- `next_cell_id`
- `blocked_event_count`

一个完整 Tick 中车辆被观察为等待，累计阻塞车辆 Tick 增加 1。连续相邻 Tick 才增加连续阻塞时长。车辆由等待变为非等待时，`blocked_end_tick` 是最后一个已观察等待 Tick。

示例：两辆车在 Tick 10、11、12 均等待，则累计阻塞车辆 Tick 为 `2 × 3 = 6`；Tick 差值不是这一指标的替代品。

## 9. Tick 缺失、重复和数据完整性

- 重复 Tick：幂等忽略，不重复累计。
- 逆序 Tick：拒绝输入。
- 缺失 Tick：所有开放 Episode 以 `ENDED_UNVERIFIED` 终止；连续阻塞链重置；质量标记为 `INCOMPLETE`。
- 缺失区间：不插值、不补齐，不把未知区间计入累计阻塞车辆 Tick。
- 车辆从完整快照中意外缺失：对应单车和相关 Episode 标记不完整，不能确认解除。

## 10. 非正常停止和 Run 结束

调用 `finish_deadlock_state()` 不会虚构额外 Tick：

- 当前仍存在的环结束为 `UNRESOLVED_AT_RUN_END`；
- 环已经消失但 quiet window 尚未完成，结束为 `ENDED_UNVERIFIED`；
- 已关闭 Episode 保持原状态。

异常停止、人工停止和后端崩溃应在后续运行时接入层显式调用相同封口函数，并另外保存 Run 的停止原因。

## 11. 可从完整新数据计算的指标

在未来接入逐 Tick 完整快照后，本合同可以提供：

- 阻塞事件次数；
- 单车当前及最长连续阻塞 Tick；
- 累计阻塞车辆 Tick；
- 疑似等待环次数；
- 达到持续阈值的循环等待次数；
- 确认循环等待持续时间；
- 有真实进展证据的恢复成功次数；
- Run 结束仍未解除次数；
- 数据不足、无法确认的结束次数；
- 平均确认解除耗时 Tick；
- 持续循环涉及网格的热点计数。

确认循环等待持续时间按 `resolved_at_tick - persistent_at_tick` 计算。无确认恢复时保持 `null`，不能转成 0。

## 12. 后续正式比率所需分母

后续若展示比率，必须同时保存并公开：

- 完整观测 Run 数和 Run 总数；
- 完整观测 Tick 数和应观测 Tick 数；
- 完成父任务数，用于每千任务事件率；
- 达到持续阈值的 Episode 总数，用于恢复成功率；
- `RESOLVED`、`UNRESOLVED_AT_RUN_END`、`ENDED_UNVERIFIED` 的完整分布；
- 车辆可用 Tick 或车辆运行 Tick，用于车辆时间归一化指标。

不得通过排除未解除或数据不足事件来宣称全部解除成功。

## 13. 历史 Run 限制

旧 Run 通常只有节流后的 `deadlock_detected`、部分 `robot_position` 和不完整的阻塞字段，无法可靠回填：

- 每 Tick 的等待边；
- 唯一实际占用车辆；
- 连续循环是否跨越告警间隔；
- quiet window；
- 处置动作与原环的关联；
- 车辆真实位移前后的完整位置；
- 未解除 Episode；
- 确认解除耗时和恢复成功率。

因此旧告警只能继续标注为“疑似死锁告警”，不能迁移成确认 Episode。

## 14. 后续接入约束

P2-2 若接入 `simulation.py`，应由适配器从同一 Tick 的权威车辆状态生成不可变 `RobotSnapshot`，并在状态持久化成功后保存 Episode 事件。适配器必须记录 Run ID、拓扑 revision、采集完整性和停止原因，不得让本分析模块反向修改调度或车辆状态。
