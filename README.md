# AGV Simulation Platform

This repository contains a local-first MVP for an intelligent warehouse AGV
simulation and optimization platform.

## Layout

- `frontend/`: React + Vite + TypeScript console
- `backend/`: FastAPI simulation service
- `data/`: sample topology and scenario JSON
- `shared/`: design tokens and JSON schemas
- `scripts/`: bootstrap and run scripts
- `.venv/`: Python virtual environment

## Quick start

```powershell
.\scripts\bootstrap.ps1
.\scripts\run-backend.ps1
.\scripts\run-frontend.ps1
```

## URLs

- Frontend: `http://127.0.0.1:5173`
- Backend: `http://127.0.0.1:8000`
- Health: `http://127.0.0.1:8000/api/v1/health`

## Data files (large, not in git)

- 源数据（`.dxf` / `.dwg` / `.xlsx`）体积大（约 187MB），**不放在仓库里**。
- 请把数据文件放到仓库**上一级**的 `data/` 目录（与 `web/` 同级），后端从 `../data` 读取。
- 数据文件列表与下载方式：见团队共享网盘（`启动方法.md` / `docs/启动方法.md`）。

## Verification

Backend:

```powershell
cd .\backend
..\.venv\Scripts\python.exe -m pytest
```

Frontend:

```powershell
cd .\frontend
npm run test
npm run build
```
