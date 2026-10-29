# AGV 6.6 拓扑与仿真平台

项目只保留两个业务模块：

- 拓扑编辑器：维护 6.6 仓库拓扑、区域、道路、输送带与设备节点。
- 仿真工作台：直接根据所选拓扑生成运行配置并启动实时仿真。

## 启动

首次运行：

```powershell
.\scripts\bootstrap.ps1
```

分别启动后端和前端：

```powershell
.\scripts\run-backend.ps1
.\scripts\run-frontend.ps1
```

## 地址

- 拓扑编辑器：`http://127.0.0.1:5173/topology`
- 仿真工作台：`http://127.0.0.1:5173/simulation`
- 后端健康检查：`http://127.0.0.1:8000/api/v1/health`

6.6 拓扑源数据位于 `data/topology_666.json`，首次启动后端时会自动导入。
