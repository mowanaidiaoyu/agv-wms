# Gate G1-B-ARM-3：位置、尺寸、避碰与正确运输方向

绑定拓扑：`topology-6ceb4920`，Revision `2.0.0:fe9fd41bf5cec86b`。

## 真实坐标与路径

- 旧底座：`r89,c49,L1`，世界坐标 `(-0.5, 0, 18.5)`。
- 新底座：`r88,c57,L1`，世界坐标 `(7.5, 0, 19.5)`；相对上一候选向右移动 3 格，该格无 cell、node、zone、rack 或 robot 占位。
- 取货：`cell-r92-c48,L1`，ground belt `y=0.6`。
- 放货：`cell-r84-c60,L2`，upper deck `y=3.0`。
- 转向交叉口：`turn_transfer-15` / `cell-r86-c60,L2`。
- 当前可证明的下游终点：`cell-r86-c91,L2`；`cell-r86-c92` 缺失。

完整预览节点序列：

```text
cell-r84-c60
-> cell-r85-c60
-> cell-r86-c60 (turn_transfer-15)
-> cell-r86-c61 -> cell-r86-c62 -> cell-r86-c63 -> cell-r86-c64
-> cell-r86-c65 -> cell-r86-c66 -> cell-r86-c67 -> cell-r86-c68
-> cell-r86-c69 -> cell-r86-c70 -> cell-r86-c71 -> cell-r86-c72
-> cell-r86-c73 -> cell-r86-c74 -> cell-r86-c75 -> cell-r86-c76
-> cell-r86-c77 -> cell-r86-c78 -> cell-r86-c79 -> cell-r86-c80
-> cell-r86-c81 -> cell-r86-c82 -> cell-r86-c83 -> cell-r86-c84
-> cell-r86-c85 -> cell-r86-c86 -> cell-r86-c87 -> cell-r86-c88
-> cell-r86-c89 -> cell-r86-c90 -> cell-r86-c91
```

`r86,c60..c91` 的正式方向均为 `right`，并保持在 L2、带面 y=3.0。`r84,c60` 与 `r85,c60` 虽然都是真实 L2 Conveyor cell，但正式字段仍为 `right`，与要求的向下设备输送冲突。预览只通过独立方案把这两条边标记为 `device_transfer / turn_transfer-15 / PARTIAL`；在 G1-B2 正式批准并补充设备边之前，不得宣称该竖向段 PASS。

## 机械臂尺寸与工作空间

| 参数 | 旧值 | ARM-3 值 |
|---|---:|---:|
| 大臂长度 | 2.35 | 5.3 |
| 小臂长度 | 2.2 | 5.2 |
| 最大工作半径 | 4.55 | 10.5 |
| 肩高 | 0.58 | 4.0（高基座） |
| 底座半径 | 0.45 | 0.78 |
| 大臂厚度 | 0.28 | 0.52 |
| 小臂厚度 | 0.24 | 0.48 |
| 安全运输高度 | 3.65 | 7.0 |

新尺寸不是对整个 Group 做 scale。连杆长度、IK、关节位置、夹爪比例、工作半径及碰撞半径均由独立 `kinematics` 契约驱动。底座至取货点的平面距离约 9.85，至红框放货点约 5.0，均处于 10.5 工作半径内；新底座至 Lift-B 的平面距离约 12.81 格。肩部采用 4.0 高基座，是因为低肩轨迹会扫到邻近 L2 输送带；不是为了视觉夸大模型。

## 安全轨迹与碰撞契约

动作改为：home -> 取货点上方安全位 -> 下降取货 -> 抬升并收臂 -> 高位旋转 -> 放货点上方 -> 下降放货 -> 再抬升 -> home。高位旋转不再使用取放点之间的低空直线。

运行时为大臂和小臂计算真实关节端点，并针对工作半径内每一层 Conveyor 带面构造 AABB。包围盒按 `linkCollisionRadius=0.24` 扩张；候选帧若 IK 不可解或任一主要连杆与带面包围盒相交，则该帧不提交，机械臂冻结并显示 `upper_arm/forearm + obstacleId`。夹爪对目标带面的正常接近不被误当作主要连杆碰撞。

对右移后的七段轨迹各 101 个姿态（共 707 个姿态）的静态几何采样结果：IK 不可达姿态为 0，主要连杆与已建模 Conveyor AABB 的碰撞为 0。安全高度为 7.0。该检查是预览层几何防护，不替代现场护栏、立柱、电缆和动态人员的安全评估。

## G3 待接入

预览不写库存、订单、Run 或 SQLite。正式业务仍需 `pickedGoodsReady`、`outputPositionAvailable`、`handoffAccepted`、`handoffCompleted`、`handoffFailed/cancelled`，以及 Conveyor 占用/释放事件。动画到达 `cell-r86-c91` 只表示“已到达预览路径终点”，不表示订单完成。
