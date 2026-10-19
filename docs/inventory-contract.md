# C-INV-1 库位、SKU 与库存契约

## 数据边界

- `TopologyPayload.nodes[type=storage]` 是物理库位身份来源；库存以 `topology_id + storage_node_id + storage_layer + sku_id` 关联，Three.js 世界坐标不是主键。
- `sku_catalog` 是商品静态资料，不表示库存余额、订单历史或真实 WMS 数据。
- `warehouse_inventory` 保存现有、预留和可用库存，其中 `available = on_hand - reserved`。
- 当前演示数据源为 `synthetic_demo`。它只通过显式初始化接口写入，固定 seed 可重复，应用启动和读取接口不会重置库存。
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
