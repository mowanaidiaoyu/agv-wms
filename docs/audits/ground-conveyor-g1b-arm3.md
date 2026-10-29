# Gate G1-B-ARM-3：用户确认路径修订

绑定拓扑：`topology-6ceb4920`，Revision `2.0.0:fe9fd41bf5cec86b`。

## 固定位置

- 机械臂底座：`r91,c54,L1`，世界坐标 `(4.5,0,16.5)`。
- 取货点：`cell-r92-c48,L1`，ground belt `y=0.6`。
- 放货点：`cell-r86-c50,L2`，upper deck `y=3.0`。
- 转向设备：`turn_transfer-15` / `cell-r86-c60,L2`。
- 预览终点：`turn_transfer-13` / `cell-r108-c60,L2`。

## 用户确认路径

```text
cell-r86-c50 -> cell-r86-c51 -> ... -> cell-r86-c60
-> turn_transfer-15
-> cell-r87-c60 -> cell-r88-c60 -> ... -> cell-r108-c60
```

完整路径包含 33 个真实 L2 Conveyor cell。前 11 个节点沿 row86、列号递增；转向后固定 col60、行号递增，最终到达 `(108,60)`。动画只在相邻节点中心之间做连续插值，因此不会斜穿或瞬移。

## 正式方向冲突

`r87..r108,c60` 全部是真实 L2 Conveyor，但正式 `conveyorDirection` 仍为 `right`。按 topology row/col 解释，用户指定的转向后路线是 `down / row+`；在当前相机画面中表现为向右。

因此预览契约使用：

- `cell-r86-c60 -> cell-r87-c60`：真实 `turn_transfer-15` 设备转向。
- `cell-r87-c60 -> ... -> cell-r108-c60`：`proposed_direction`，来源为独立 layout plan。
- 路径状态保持 `PARTIAL`，不修改 `topology_666.json` 或 SQLite。

## 机械臂与业务边界

机械臂大臂 4.0、小臂 3.9、工作半径 7.9、肩高 4.0、安全高度 6.3。底座至取货点和放货点均可达，预览仍使用唯一 cargoToken。

预览不会写库存、订单、Run 或 SQLite。到达 `cell-r108-c60` 只表示到达预览路径终点，不表示订单完成。正式业务仍需 G3 接入权威货物就绪、位置占用和交接完成事件。
