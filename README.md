# Online Exam Cheating Detection using GNN

A real-time hackathon prototype that detects collaborative exam cheating using Graph Neural Network simulation.

## Quick Start

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 5000
```

Then open:
- **Student Exam**: http://localhost:5000/
- **Admin Dashboard**: http://localhost:5000/admin.html (login: admin / admin)

## Demo Script (for judges)

1. Open the Admin Dashboard and click **Run Demo**
2. Watch as 10 students are seeded — 7 legitimate, 3 cheaters in a cluster
3. The GNN-simulated GraphSAGE algorithm builds a student similarity graph
4. Within 10 seconds, edges form between cheating students (shared IP group 7, near-identical answers, synchronized submit times)
5. The graph nodes turn **red** for the cheating cluster, scores spike above 70%
6. An **alert popup fires** identifying the flagged students by name
7. Open http://localhost:5000/ in a new tab, enter a student ID, and use the **Demo Controls** to simulate cheating behaviors in real-time

## Architecture

- **Backend**: FastAPI + WebSockets (Python)
- **GNN**: Simulated GraphSAGE via numpy (2-layer message passing + community detection)
- **Graph**: NetworkX — edges based on answer cosine similarity >0.8, timing <5s, shared IP group, behavior matching
- **Database**: SQLite (auto-created)
- **Frontend**: Vanilla JS, dark theme, Cytoscape.js graph visualization

## Cheat Detection Logic

Edges form between students when:
- Answer cosine similarity > 0.8
- Submit time difference < 5 seconds
- Same IP group (simulated)
- Similar tab-switch rate

GraphSAGE aggregates neighbor embeddings over 2 layers, and Louvain community detection identifies clusters. Risk scores combine degree centrality, edge weights, and community density.
