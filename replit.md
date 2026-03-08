# Online Exam Cheating Detection using GNN

## Project Overview
A real-time web-based hackathon prototype that detects collaborative exam cheating by constructing dynamic student graphs and analyzing them with a GNN simulation.

## Architecture
- **Backend**: Python FastAPI + WebSockets, runs on port 5000
- **GNN**: Simulated GraphSAGE (numpy, 2-layer message passing + Louvain community detection)
- **Graph**: NetworkX — edges from answer cosine similarity, timing sync, shared IP group, behavior match
- **Database**: SQLite (db.sqlite, auto-created on startup)
- **Frontend**: Vanilla JS, dark theme, Cytoscape.js graph visualization

## File Structure
- `main.py` — FastAPI app, WebSocket handlers, GNN logic, background graph builder
- `requirements.txt` — Python dependencies
- `static/index.html` — Student exam interface
- `static/admin.html` — Admin monitoring dashboard
- `static/exam.js` — Exam logic, behavior logging, cheat simulation
- `static/admin.js` — Admin dashboard, Cytoscape graph, real-time updates
- `static/style.css` — Dark theme styling

## Key Endpoints
- `GET /` — Student exam (static)
- `GET /admin.html` — Admin dashboard (login: admin/admin)
- `WS /ws/student/{id}` — Student behavior stream
- `WS /ws/admin` — Admin real-time updates
- `POST /api/demo/seed` — Seed 10 demo students (7 legit + 3 cheaters)
- `GET /api/graph_data` — Current graph snapshot
- `GET /api/admin/status` — All student statuses

## Running
```bash
uvicorn main:app --host 0.0.0.0 --port 5000
```

## Dependencies
fastapi, uvicorn[standard], networkx, numpy, scipy, python-multipart, python-jose[cryptography], aiofiles
