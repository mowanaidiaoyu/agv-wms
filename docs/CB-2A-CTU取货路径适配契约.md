# CB-2A CTU 取货路径适配契约

## 适用范围

`plan_ctu_task_route` 将 B 的 CTU 取货任务转换为按商品行顺序排列的单车几何路径航段。它是 dry-run 适配层，不修改车辆、任务、拓扑或正式仿真状态，也不确认取货成功。

当前契约版本为 `ctu-routing-adapter/v1`。

## 已确认的 B 运行时语义

- CTU 容量上限来自 `simulation.ROBOT_CARRY_CAPACITY["ctu"]`，当前为 5 箱。
- `lines` 的每一行代表一箱；`currentLineIndex` 指向下一条待取商品行，`picked` 是已确认取货行数。
- 正式仿真到达取货航段后才推进 `currentLineIndex`、`picked` 和车辆 `load`。规划成功不等于取货成功。
- 正式仿真推进间隔来自 `simulation.TICK_INTERVAL_SECONDS`，当前为 1.5 秒。

## 停靠点规则

取货库位和可行驶 Cell 是不同对象。适配器仅接受以下两类停靠点：

1. 与 storage/buffer 节点同楼层、同行、同列且真实存在的可通行 Cell；
2. topology resource 的 `nodeIds` 与 `cellIds` 明确声明、且唯一可确定的可通行 Cell。

任务中的 `cellId` 若只是由库位坐标拼出的 ID、但拓扑中没有该 Cell，不会被当作合法路径节点。适配器不会选择“最近道路”，没有明确映射时返回 `missing_docking_mapping`。

## 路径与时间边界

- 每一航段调用现有 `shortest_path_result`，支持 `astar` 和 `dijkstra`，固定启用 `avoid_storage=True`。
- 航段必须首尾正确、内部 Cell 连续，且中间不得穿越 storage/buffer 占用位置。
- 多航段以上一航段的真实终点作为下一航段起点，不生成连接跳跃。
- 输出的 `time_model.planning_basis` 固定为 `geometric_path_only`。
- 输出只记录 B 的 1.5 秒仿真 tick 作为契约说明，不据此计算 ETA。
- 本适配器不生成 C 的 0.25 秒预约时间片，不生成多车预约计划，也不声称已经执行实时轨迹。

## 结果状态

- `success`：所有未完成商品行均获得合法路径；
- `partial_failure`：至少一个前置航段成功，后续航段失败；
- `failed`：输入、容量、停靠点或首个航段校验失败。

失败结果包含稳定的 `failure_code`、中文 `failure_reason`、已成功的航段以及未规划商品行 ID。旧任务没有 `lineId` 时，适配器使用 `task-id:line:index` 作为可追溯引用，并通过 `line_id_source` 明确标记为派生值。

## 后续 CB-2B 边界

CB-2B 可消费本适配器的成功几何航段，将其转换为独立的多车时空预约请求。预约结果、正式仿真执行轨迹和本适配器输出必须继续作为三个不同对象管理。当前 C 多车规划器的 1～6 台限制仍保持不变；是否扩展至 7 台需另行进行状态空间与性能测试。
