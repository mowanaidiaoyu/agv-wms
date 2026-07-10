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
