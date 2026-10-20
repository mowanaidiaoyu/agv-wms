# C-INV-1 库位、SKU 与库存契约

## 数据边界

- `TopologyPayload.nodes[type=storage]` 是物理库位身份来源；库存以 `topology_id + storage_node_id + storage_layer + sku_id` 关联，Three.js 世界坐标不是主键。
- `sku_catalog` 是商品静态资料，不表示库存余额、订单历史或真实 WMS 数据。
- `warehouse_inventory` 保存现有、预留和可用库存，其中 `available = on_hand - reserved`。
- 当前演示数据源为 `synthetic_demo`。它只通过显式初始化接口写入，固定 seed 可重复，应用启动和读取接口不会重置库存。
- 初始演示初始化继续最多覆盖 32 个真实库位，以保持旧数据库行为；补充接口可按指定数量或明确的 `fill_all` 选择补齐尚无记录的 CTU Storage Node。补充前可预览影响范围，补充过程使用事务和确定性哈希，不覆盖任何已有、已预留或非演示来源记录。
- 统计中的“有记录”表示库位至少存在一条库存余额；“正库存”表示现有量大于零；“零库存”表示有余额记录但现有量为零；“未配置”表示真实 Storage Node 没有任何库存余额。零库存不能显示为未配置。
- 666 的货架层数来自可视化区域定义，现有 Storage Node 没有可靠的逐层库位字段，因此 C-INV-1 不虚构层号，持久化记录暂用 `storage_layer=1`，API 中 `rack_level=null`。

## 取货停靠点

`storage_access_mappings` 独立保存 `storage_node_id -> access_cell_id`，不会写回拓扑 JSON。配置时必须满足：

1. 库位和道路格真实存在于同一拓扑版本；
2. 两者同楼层且正交相邻；
3. 道路格为可通行 `road`，库位与道路均属于 CTU 库区；
4. 道路格连接到至少另一个 CTU 道路格；
5. 映射记录来源和校验时间。

未配置映射返回 `unconfigured`；拓扑版本变化后返回 `topology_version_mismatch`。系统不会用最近距离自动推断合法停靠点，也不会把 Storage Node 当作 road Cell。

## 后续订单与取货事务

C-INV-2/C-INV-3 应复用以下语义：

- `OrderLine`: `sku_id`, `requested_quantity`, `allocated_quantity`, `picked_quantity`。
- `PickingTask`: `task_id`, `robot_id`, `source_storage_node_id`, `access_cell_id`, `sku_id`, `quantity`, `status`。
- 分配订单时事务性调用库存预留；取消或失败时释放预留。
- 只有正式仿真运行时确认 Pick 成功后，才同时减少 `quantity_on_hand` 和 `quantity_reserved`。
- Pick 确认使用唯一 `event_id` 写入 `inventory_movements`，重复事件不重复扣减。
- 失败事件、前端播放和历史回放不得扣减库存。

C-INV-1 没有暴露任意库存扣减 HTTP API；预留、释放和幂等确认只作为后端服务接口，为后续带鉴权的业务流程保留。

## 拓扑与可达性边界

- Storage Node 表示物理库位，不是道路格，也不要求同坐标存在 TopologyCell；它仍必须具有唯一 ID、合法边界、楼层和真实库区引用。
- CTU 可行驶道路由可通行 road Cell 表示。库位能否取货由版本绑定的 `storage_access_mappings` 显式描述，不能把最近道路自动当成合法停靠点。
- 通用拓扑校验分别报告结构完整性、取货可达性和正式仿真就绪度。缺少站台或机器人不阻止库存查询和普通路径演示，但正式仿真启动仍单独执行站台等运行前校验。
