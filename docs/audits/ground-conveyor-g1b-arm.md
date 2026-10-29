# Gate G1-B-ARM：人工拣选出口机械臂接驳与 3D 动画

## 1. 结论

本 Gate 提供 `proposed` 机械臂布局和独立开发预览。根据用户箭头截图，机械臂位于两条真实横向 Conveyor 之间：从 `cell-r92-c48` 末端取货，放到 `cell-r86-c50`，后者可继续向右。正式货物交接仍为 `PARTIAL`：拓扑没有 `pick_station`、`breakpack_exit` 或机械臂 handoff。

动画完成不扣减库存、不累计订单交付数，也不把订单标记为 completed。

## 2. 真实拓扑与候选坐标

绑定拓扑：

- topologyId：`topology-6ceb4920`
- topologyRevision：`2.0.0:fe9fd41bf5cec86b`
- grid：216×100，前端 `CELL=1`；世界坐标由 `worldX/worldZ` 转换，不使用屏幕像素。

| 对象 | ID | row/col/level | 状态与证据 |
|---|---|---:|---|
| 机械臂底座 | `proposed-picking-arm-r89-c49` | r89,c49,L1 | proposed；该位置无 cell、node、zone、货架或车辆占位 |
| 取货点 | `cell-r92-c48` | r92,c48,L1，ground y=0.6 | topology；right，真实末端，c49 缺失 |
| 放货点 | `cell-r86-c50` | r86,c50,L2，upper deck y=3.0 | topology；双层 conveyor 起点，`direction=right` |
| 下游格 | `cell-r86-c51` | r86,c51,L2 | topology；真实相邻且继续向右 |
| Lift-B | `shuttle-lift-b` | r98,c49,L1-L4 | Shuttle 配置真实节点；与底座平面中心距 9 格 |

`cell-r86-c50` 的拓扑字段为 `levels=2`。预览选择上层物理带面 y=3.0 放货，避免夹爪从上方穿过上层结构去访问下层带面。正式数据仍缺少 deck-specific handoff，因此不能只凭动画判断合法交接。

## 3. 空间约束

- 推荐工作半径：4.55 个世界单位。
- 大臂长度：2.35；小臂长度：2.2。
- 底座位于两条真实横向线之间的 r89,c49 空白区。
- 取货与放货均在推荐臂展内，旋转阶段先抬升到上方安全姿态，再进行腰部旋转。
- 预览不移动货架、Port 或 Conveyor。
- 运动轨迹属于几何预检查，护栏、人员通道、动态扫掠体和现场净空仍需 G1-B2/现场复核。

## 4. 动作状态机

严格顺序：

`idle → approach → grip → lift → rotate → place → release → return_home → idle`

使用腰部、肩部、肘部和腕部关节旋转。夹取后唯一的周转箱实例跟随末端目标；release 后同一实例切换到输出带面，不会同时渲染在夹爪和 Conveyor。

`place` 阶段在 `outputPositionAvailable=false` 时停止，不会提前 release。相同 `cargoToken` 只能执行一次；reset 消费当前 token，必须使用新 token 才能重新装载。

## 5. 订单周转箱契约

```ts
interface PickingArmCargo {
  toteId: string
  orderId: string
  skuId: string
  pickedQuantity: number
}
```

`pickedQuantity` 表示订单已拣商品件数，例如 SKU-A 已拣 10 件；周转箱本身不是一个库存计量单位。

预览状态：

- `picked_goods_ready`
- `robot_arm_transfer`
- `waiting_handoff_confirmation`

## 6. 预览与真实 Run

开发预览通过仿真页面“机械臂预览”开关启用。它使用示例 cargo token，但 `authoritativeHandoffCompleted` 永远为 false。

动画复用当前 Run 的：

- `status=paused`：机械臂与周转箱同时冻结；
- `speedMultiplier=1/2/4`：仅缩放视觉状态机进度；
- 页面统一 reset：回到 home，且不会重复放置同一 token。

React Three Fiber `useFrame` 只做连续视觉插值，不是新的业务 Tick。

## 7. G3 待接入权威事件

后端至少需要提供：

1. `pickedGoodsReady`：人工拣选完成，绑定 toteId/orderId/skuId/pickedQuantity。
2. `outputPositionAvailable`：`cell-r86-c50` 指定 deck 无占用且已预约。
3. `handoffAccepted`：机械臂控制资源和 Conveyor 接收资源共同接受交接。
4. `handoffCompleted`：放货传感器确认，带幂等 cargo token。
5. `handoffFailed/cancelled`：失败恢复与周转箱权属回滚。

只有权威 `handoffCompleted` 可以推动后续货物状态；动画阶段本身不得更新库存或订单完成条件。

## 8. 当前阻断

- 取货点 `cell-r92-c48` 是真实 Conveyor，但没有正式 pick_station/breakpack 事件把已拣商品送到此处。
- 没有机械臂设备资源、容量、占用和互锁契约。
- `cell-r86-c50` 没有 deck-specific handoff。
- 未有权威 cargo-ready/output-available/handoff-completed 事件。

因此 G1-A 路径不能因本预览改为 PASS。

## 9. Gate G1-B-ARM-2：放货后连续输送修复

### 9.1 原因

原实现只在 `PickingArmHandoff` 的 `return_home` 阶段将输出周转箱沿 x 方向偏移最多 0.34 格。它没有 Conveyor 播放状态、没有真实节点序列，也没有在 release 后转交 cargoToken 的视觉控制权，所以机械臂复位后周转箱停在放货点。

现有 `DeliveredBox` 不能直接复用：它从真实 Run task 派生新的 box，使用 `clock.elapsedTime`，没有本预览 cargoToken 的暂停、倍速和 reset 契约。与机械臂周转箱同时使用会生成两个可见实例。

### 9.2 严格有向路径

机械臂位置保持 `r89,c49` 不变。release 后先沿 row86 到达真实设备 `turn_transfer-15`，再通过 proposed/PARTIAL 设备交接进入红箭头 row84 主线：

```text
cell-r86-c50 → … → cell-r86-c60
→ cell-r85-c60
→ cell-r84-c60 → … → cell-r84-c91
```

- 所有动画 waypoint 都是真实 L2 Conveyor cell。
- `cell-r86-c50..c60` 和 `cell-r84-c60..c91` 分别遵守真实 right 方向。
- `cell-r86-c60 → cell-r85-c60 → cell-r84-c60` 使用真实 `turn_transfer-15`，但正式拓扑尚无这条设备边，因此标记 proposed/PARTIAL。
- 全部 `direction=right`、`levels>=2`、物理带面 y=3.0。
- `cell-r84-c92` 不存在，预览必须停在 `cell-r84-c91`。
- 不跨空白格，不跨层，不使用屏幕像素路径。

经过的设备节点：

| Cell | 设备 | 路径动作 |
|---|---|---|
| c52 | `turn_transfer-18` | 直行；当前及下一格均 right |
| c56 | `turn_transfer-17` | 直行 |
| row86,c60 | `turn_transfer-15` | proposed 向上移载至 row84 主线 |
| row84,c66 | `turn_transfer-08` | 直行 |
| row84,c74 | `turn_transfer-06` | 直行 |
| row84,c82 | `turn_transfer-04` | 直行 |
| row84,c90 | `turn_transfer-01` | 直行 |

row86 和 row84 内部保持真实 right 方向；只有 `turn_transfer-15` 段改变方向。该设备真实存在，但正式设备边缺失，所以预览允许播放并保持 PARTIAL，不能据此把运输网络审计升级为 PASS。

### 9.3 单实例视觉交接

货物播放状态：

```text
waiting_for_release
→ conveyor_moving
→ conveyor_waiting（路径无效或不可用）
→ preview_endpoint_reached
```

release 完成时，同一个周转箱实例从夹爪坐标切换到路径起点；随后按路径累计距离连续插值。没有创建第二个 DeliveredBox。到达 c91 后显示“已到达预览路径终点”，不会显示订单已完成。

暂停、倍速、继续和重置继续使用机械臂相同的视觉帧驱动与 Run 控制，不新增业务 Tick。
