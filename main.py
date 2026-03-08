import asyncio
import json
import os
import random
import sqlite3
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import networkx as nx
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from scipy.spatial.distance import cosine

# ─── Database ────────────────────────────────────────────────────────────────

DB_PATH = "db.sqlite"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS students (
            id TEXT PRIMARY KEY,
            name TEXT,
            answers TEXT DEFAULT '{}',
            submit_time REAL DEFAULT 0,
            ip_group INTEGER DEFAULT 0,
            behaviors TEXT DEFAULT '{}',
            last_seen REAL DEFAULT 0,
            cheating_score REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS graph_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot TEXT,
            created_at REAL
        );
    """)
    conn.commit()
    conn.close()


# ─── GNN Simulation ──────────────────────────────────────────────────────────

NUM_QUESTIONS = 10
FEATURE_DIM = NUM_QUESTIONS + 4  # answers + tab_rate + paste_rate + key_rate + time_norm


def answers_to_vec(answers: dict) -> np.ndarray:
    vec = np.zeros(NUM_QUESTIONS)
    for i in range(NUM_QUESTIONS):
        ans = answers.get(str(i), answers.get(i, -1))
        if ans is not None and ans != -1:
            vec[i] = int(ans) + 1
    return vec


def build_feature_vector(student: dict) -> np.ndarray:
    behaviors = student.get("behaviors", {})
    if isinstance(behaviors, str):
        try:
            behaviors = json.loads(behaviors)
        except Exception:
            behaviors = {}
    answers = student.get("answers", {})
    if isinstance(answers, str):
        try:
            answers = json.loads(answers)
        except Exception:
            answers = {}
    ans_vec = answers_to_vec(answers)
    tab_rate = min(behaviors.get("tab_switches", 0) / 10.0, 1.0)
    paste_rate = min(behaviors.get("paste_count", 0) / 5.0, 1.0)
    key_rate = min(behaviors.get("keystroke_rate", 100) / 100.0, 1.0)
    submit_t = float(student.get("submit_time", 0) or 0)
    time_norm = min(submit_t / 1800.0, 1.0) if submit_t > 0 else 0.0
    return np.concatenate([ans_vec, [tab_rate, paste_rate, key_rate, time_norm]])


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    if np.linalg.norm(a) == 0 or np.linalg.norm(b) == 0:
        return 0.0
    return float(1.0 - cosine(a, b))


def answer_match_ratio(a: np.ndarray, b: np.ndarray) -> float:
    """Exact question-by-question match ratio (only for answered questions)."""
    both_answered = (a > 0) & (b > 0)
    n = int(both_answered.sum())
    if n < 5:
        return 0.0
    matches = int(((a == b) & both_answered).sum())
    return matches / n


def graphsage_aggregate(G: nx.Graph, features: dict, layers: int = 2) -> dict:
    """Simulate GraphSAGE-style message passing with numpy."""
    rng = np.random.RandomState(42)
    W = rng.randn(FEATURE_DIM, FEATURE_DIM) * 0.1
    np.fill_diagonal(W, 1.0)
    embeddings = {n: features[n].copy() for n in G.nodes()}
    for _ in range(layers):
        new_emb = {}
        for node in G.nodes():
            neighbors = list(G.neighbors(node))
            if neighbors:
                neigh_feats = np.mean([embeddings[n] for n in neighbors], axis=0)
            else:
                neigh_feats = embeddings[node].copy()
            concat = (embeddings[node] + neigh_feats) / 2.0
            new_emb[node] = np.tanh(W @ concat)
        embeddings = new_emb
    return embeddings


def compute_cheating_scores(students: List[dict]) -> Dict[str, float]:
    if not students:
        return {}
    if len(students) < 2:
        return {students[0]["id"]: students[0].get("cheating_score", 0.0)}

    G = nx.Graph()
    features = {}
    for s in students:
        G.add_node(s["id"])
        features[s["id"]] = build_feature_vector(s)

    for i in range(len(students)):
        for j in range(i + 1, len(students)):
            si, sj = students[i], students[j]
            reasons = []

            vi = features[si["id"]][:NUM_QUESTIONS]
            vj = features[sj["id"]][:NUM_QUESTIONS]
            match_r = answer_match_ratio(vi, vj)
            if match_r >= 0.8:
                reasons.append(("answer_similarity", match_r))

            ti = float(si.get("submit_time", 0) or 0)
            tj = float(sj.get("submit_time", 0) or 0)
            if ti > 0 and tj > 0 and abs(ti - tj) < 5:
                reasons.append(("sync_time", 1.0 - abs(ti - tj) / 5.0))

            gi = int(si.get("ip_group", 0) or 0)
            gj = int(sj.get("ip_group", 0) or 0)
            if gi > 0 and gj > 0 and gi == gj:
                reasons.append(("shared_ip", 0.9))

            bi = json.loads(si["behaviors"]) if isinstance(si["behaviors"], str) else si.get("behaviors", {})
            bj = json.loads(sj["behaviors"]) if isinstance(sj["behaviors"], str) else sj.get("behaviors", {})
            tr_i = bi.get("tab_switches", 0) / 10.0
            tr_j = bj.get("tab_switches", 0) / 10.0
            if abs(tr_i - tr_j) < 0.3 and (tr_i + tr_j) > 0.4:
                reasons.append(("behavior_match", 0.7))

            # Require 2+ signals or a single very strong one (answer_sim alone > 0.9)
            strong_answer = any(r[0] == "answer_similarity" and r[1] > 0.9 for r in reasons)
            if len(reasons) >= 2 or strong_answer:
                weight = min(1.0, sum(r[1] for r in reasons) / len(reasons))
                G.add_edge(si["id"], sj["id"], weight=weight, reasons=[r[0] for r in reasons])

    embeddings = graphsage_aggregate(G, features)
    scores = {}
    try:
        communities = list(nx.algorithms.community.greedy_modularity_communities(G))
    except Exception:
        communities = []

    for node in G.nodes():
        emb = embeddings[node]
        degree = G.degree(node)
        edge_weights = [G[node][nb].get("weight", 0) for nb in G.neighbors(node)]
        avg_edge_w = np.mean(edge_weights) if edge_weights else 0.0

        community_penalty = 0.0
        for comm in communities:
            if node in comm and len(comm) >= 2:
                comm_edges = G.subgraph(comm).number_of_edges()
                possible = len(comm) * (len(comm) - 1) / 2
                density = comm_edges / possible if possible > 0 else 0
                community_penalty = density * 0.4

        beh_score = abs(float(emb[NUM_QUESTIONS])) * 0.3 + abs(float(emb[NUM_QUESTIONS + 1])) * 0.4
        # Require meaningful multi-signal evidence before scoring high
        edge_count_factor = min(1.0, degree / 3.0) * 0.25
        risk = min(1.0, edge_count_factor + avg_edge_w * 0.5 + community_penalty * 0.5 + beh_score * 0.15)
        scores[node] = round(risk * 100, 1)

    return scores


# ─── Connection Manager ───────────────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.student_ws: Dict[str, WebSocket] = {}
        self.admin_ws: List[WebSocket] = []

    async def connect_student(self, student_id: str, ws: WebSocket):
        await ws.accept()
        self.student_ws[student_id] = ws

    async def connect_admin(self, ws: WebSocket):
        await ws.accept()
        self.admin_ws.append(ws)

    def disconnect_student(self, student_id: str):
        self.student_ws.pop(student_id, None)

    def disconnect_admin(self, ws: WebSocket):
        if ws in self.admin_ws:
            self.admin_ws.remove(ws)

    async def broadcast_admin(self, data: dict):
        dead = []
        for ws in self.admin_ws:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect_admin(ws)


manager = ConnectionManager()


# ─── Helpers ─────────────────────────────────────────────────────────────────

def build_graph_data(students: List[dict], scores: Dict[str, float]) -> dict:
    features = {}
    for s in students:
        features[s["id"]] = build_feature_vector(s)

    edges_info = []
    seen_edges = set()
    for i in range(len(students)):
        for j in range(i + 1, len(students)):
            si, sj = students[i], students[j]
            reasons = []
            vi = features[si["id"]][:NUM_QUESTIONS]
            vj = features[sj["id"]][:NUM_QUESTIONS]
            if answer_match_ratio(vi, vj) >= 0.8:
                reasons.append("answer_sim")
            ti = float(si.get("submit_time", 0) or 0)
            tj = float(sj.get("submit_time", 0) or 0)
            if ti > 0 and tj > 0 and abs(ti - tj) < 5:
                reasons.append("sync_time")
            gi = int(si.get("ip_group", 0) or 0)
            gj = int(sj.get("ip_group", 0) or 0)
            if gi > 0 and gj > 0 and gi == gj:
                reasons.append("shared_ip")
            if len(reasons) >= 2 or (len(reasons) == 1 and reasons[0] == "answer_sim"):
                key = tuple(sorted([si["id"], sj["id"]]))
                if key not in seen_edges:
                    seen_edges.add(key)
                    edges_info.append({
                        "source": si["id"], "target": sj["id"],
                        "weight": round(min(1.0, len(reasons) * 0.4), 2),
                        "reasons": reasons
                    })

    nodes_info = []
    for s in students:
        score = scores.get(s["id"], s.get("cheating_score", 0))
        behaviors = s.get("behaviors", {})
        if isinstance(behaviors, str):
            try:
                behaviors = json.loads(behaviors)
            except Exception:
                behaviors = {}
        answers = s.get("answers", {})
        if isinstance(answers, str):
            try:
                answers = json.loads(answers)
            except Exception:
                answers = {}
        nodes_info.append({
            "id": s["id"],
            "name": s.get("name", s["id"]),
            "score": float(score),
            "ip_group": int(s.get("ip_group", 0) or 0),
            "tab_switches": behaviors.get("tab_switches", 0),
            "paste_count": behaviors.get("paste_count", 0),
            "answers_count": sum(1 for v in answers.values() if str(v) != "-1")
        })

    return {"nodes": nodes_info, "edges": edges_info}


async def get_admin_status_data() -> dict:
    conn = get_db()
    rows = conn.execute("SELECT * FROM students").fetchall()
    conn.close()
    students = []
    for r in rows:
        s = dict(r)
        behaviors = s.get("behaviors", {})
        if isinstance(behaviors, str):
            try:
                behaviors = json.loads(behaviors)
            except Exception:
                behaviors = {}
        answers = s.get("answers", {})
        if isinstance(answers, str):
            try:
                answers = json.loads(answers)
            except Exception:
                answers = {}
        students.append({
            "id": s["id"],
            "name": s.get("name", s["id"]),
            "score": round(float(s.get("cheating_score", 0) or 0), 1),
            "ip_group": int(s.get("ip_group", 0) or 0),
            "tab_switches": behaviors.get("tab_switches", 0),
            "paste_count": behaviors.get("paste_count", 0),
            "keystroke_rate": behaviors.get("keystroke_rate", 0),
            "answers_count": sum(1 for v in answers.values() if str(v) != "-1"),
            "last_seen": s.get("last_seen", 0)
        })
    return {"students": students}


# ─── Background Graph Builder ─────────────────────────────────────────────────

async def graph_builder_loop():
    while True:
        await asyncio.sleep(10)
        try:
            conn = get_db()
            rows = conn.execute("SELECT * FROM students").fetchall()
            conn.close()
            students = [dict(r) for r in rows]
            if not students:
                continue

            scores = compute_cheating_scores(students)

            conn = get_db()
            for sid, score in scores.items():
                conn.execute("UPDATE students SET cheating_score=? WHERE id=?", (score, sid))
            graph_data = build_graph_data(students, scores)
            conn.execute(
                "INSERT INTO graph_snapshots (snapshot, created_at) VALUES (?, ?)",
                (json.dumps(graph_data), time.time())
            )
            conn.execute("DELETE FROM graph_snapshots WHERE id NOT IN (SELECT id FROM graph_snapshots ORDER BY id DESC LIMIT 20)")
            conn.commit()
            conn.close()

            status = await get_admin_status_data()
            status["graph"] = graph_data
            await manager.broadcast_admin({"type": "update", "data": status})
        except Exception as e:
            print(f"[graph_builder] Error: {e}")


# ─── App Lifespan ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(graph_builder_loop())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ─── REST Endpoints ───────────────────────────────────────────────────────────

@app.post("/api/register")
async def register_student(data: dict):
    student_id = data.get("id", f"student_{int(time.time())}")
    name = data.get("name", student_id)
    ip_group = data.get("ip_group", random.randint(1, 5))
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO students (id,name,ip_group,behaviors,answers,last_seen) VALUES (?,?,?,?,?,?)",
        (student_id, name, ip_group, json.dumps({}), json.dumps({}), time.time())
    )
    conn.commit()
    conn.close()
    return {"status": "ok", "id": student_id}


@app.post("/api/submit_answers")
async def submit_answers(data: dict):
    student_id = data.get("id")
    answers = data.get("answers", {})
    submit_time_val = data.get("submit_time", time.time())
    if not student_id:
        raise HTTPException(400, "Missing id")
    conn = get_db()
    conn.execute(
        "UPDATE students SET answers=?, submit_time=?, last_seen=? WHERE id=?",
        (json.dumps(answers), submit_time_val, time.time(), student_id)
    )
    conn.commit()
    conn.close()
    return {"status": "submitted"}


@app.get("/api/admin/status")
async def admin_status():
    return await get_admin_status_data()


@app.get("/api/graph_data")
async def graph_data_endpoint():
    conn = get_db()
    rows = conn.execute("SELECT * FROM students").fetchall()
    conn.close()
    students = [dict(r) for r in rows]
    scores = {s["id"]: float(s.get("cheating_score", 0) or 0) for s in students}
    return build_graph_data(students, scores)


@app.post("/api/admin/login")
async def admin_login(data: dict):
    if data.get("username") == "admin" and data.get("password") == "admin":
        return {"status": "ok", "token": "admin-demo-token"}
    raise HTTPException(401, "Invalid credentials")


@app.post("/api/demo/seed")
async def seed_demo():
    conn = get_db()
    conn.execute("DELETE FROM students")
    conn.execute("DELETE FROM graph_snapshots")
    conn.commit()
    conn.close()

    legit_answers = [
        {0: 2, 1: 1, 2: 3, 3: 0, 4: 2, 5: 1, 6: 3, 7: 2, 8: 0, 9: 1},
        {0: 1, 1: 3, 2: 0, 3: 2, 4: 1, 5: 3, 6: 0, 7: 1, 8: 3, 9: 2},
        {0: 3, 1: 0, 2: 1, 3: 3, 4: 0, 5: 2, 6: 1, 7: 3, 8: 2, 9: 0},
        {0: 0, 1: 2, 2: 3, 3: 1, 4: 3, 5: 0, 6: 2, 7: 0, 8: 1, 9: 3},
        {0: 2, 1: 3, 2: 0, 3: 1, 4: 3, 5: 1, 6: 0, 7: 2, 8: 3, 9: 0},
        {0: 1, 1: 0, 2: 2, 3: 3, 4: 0, 5: 3, 6: 1, 7: 0, 8: 2, 9: 3},
        {0: 3, 1: 1, 2: 2, 3: 0, 4: 1, 5: 2, 6: 3, 7: 1, 8: 0, 9: 2},
    ]
    cheat_answers = {0: 2, 1: 2, 2: 1, 3: 2, 4: 3, 5: 0, 6: 2, 7: 1, 8: 3, 9: 0}
    base_time = time.time()

    conn = get_db()
    legit_ip_groups = [1, 2, 3, 4, 5, 6, 8]  # Unique groups; group 7 reserved for cheaters
    random.shuffle(legit_ip_groups)
    for i, ans in enumerate(legit_answers):
        sid = f"S{i+1:03d}"
        behaviors = {
            "tab_switches": random.randint(0, 2),
            "paste_count": 0,
            "keystroke_rate": random.randint(40, 80)
        }
        conn.execute(
            "INSERT OR REPLACE INTO students (id,name,answers,submit_time,ip_group,behaviors,last_seen,cheating_score) VALUES (?,?,?,?,?,?,?,?)",
            (sid, f"Student {i+1}", json.dumps(ans),
             base_time - random.randint(120, 900), legit_ip_groups[i],
             json.dumps(behaviors), base_time - random.randint(10, 60), 0.0)
        )

    cheat_time = base_time - random.randint(5, 15)
    for idx in range(3):
        sid = f"S{8+idx:03d}"
        behaviors = {
            "tab_switches": random.randint(6, 14),
            "paste_count": random.randint(4, 9),
            "keystroke_rate": random.randint(5, 18)
        }
        slightly_varied = {k: (v if random.random() > 0.1 else (v + 1) % 4)
                           for k, v in cheat_answers.items()}
        conn.execute(
            "INSERT OR REPLACE INTO students (id,name,answers,submit_time,ip_group,behaviors,last_seen,cheating_score) VALUES (?,?,?,?,?,?,?,?)",
            (sid, f"Student {8+idx}", json.dumps(slightly_varied),
             cheat_time + idx * 1.5, 7,
             json.dumps(behaviors), base_time - random.randint(1, 5), 0.0)
        )
    conn.commit()
    conn.close()

    conn = get_db()
    rows = conn.execute("SELECT * FROM students").fetchall()
    conn.close()
    students = [dict(r) for r in rows]
    scores = compute_cheating_scores(students)
    conn = get_db()
    for sid, score in scores.items():
        conn.execute("UPDATE students SET cheating_score=? WHERE id=?", (score, sid))
    conn.commit()
    conn.close()

    status = await get_admin_status_data()
    conn = get_db()
    rows = conn.execute("SELECT * FROM students").fetchall()
    conn.close()
    students = [dict(r) for r in rows]
    graph_data = build_graph_data(students, scores)
    status["graph"] = graph_data
    await manager.broadcast_admin({"type": "update", "data": status})
    return {"status": "seeded", "count": 10}


@app.delete("/api/admin/clear")
async def clear_students():
    conn = get_db()
    conn.execute("DELETE FROM students")
    conn.execute("DELETE FROM graph_snapshots")
    conn.commit()
    conn.close()
    await manager.broadcast_admin({"type": "update", "data": {"students": [], "graph": {"nodes": [], "edges": []}}})
    return {"status": "cleared"}


# ─── WebSocket: Student ───────────────────────────────────────────────────────

@app.websocket("/ws/student/{student_id}")
async def student_ws(websocket: WebSocket, student_id: str):
    await manager.connect_student(student_id, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            conn = get_db()
            existing = conn.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
            if not existing:
                conn.execute(
                    "INSERT OR IGNORE INTO students (id,name,ip_group,behaviors,answers,last_seen) VALUES (?,?,?,?,?,?)",
                    (student_id, data.get("name", student_id),
                     data.get("ip_group", random.randint(1, 5)),
                     json.dumps({}), json.dumps({}), time.time())
                )
                conn.commit()
                existing = conn.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()

            behaviors = {}
            try:
                behaviors = json.loads(existing["behaviors"] or "{}")
            except Exception:
                pass
            new_behaviors = data.get("behaviors", {})
            behaviors.update(new_behaviors)

            answers = {}
            try:
                answers = json.loads(existing["answers"] or "{}")
            except Exception:
                pass
            answers.update(data.get("answers", {}))

            conn.execute(
                "UPDATE students SET behaviors=?, answers=?, last_seen=?, ip_group=? WHERE id=?",
                (json.dumps(behaviors), json.dumps(answers), time.time(),
                 data.get("ip_group", existing["ip_group"]), student_id)
            )
            conn.commit()
            conn.close()
    except WebSocketDisconnect:
        manager.disconnect_student(student_id)
    except Exception as e:
        print(f"[ws/student] {student_id}: {e}")
        manager.disconnect_student(student_id)


# ─── WebSocket: Admin ─────────────────────────────────────────────────────────

@app.websocket("/ws/admin")
async def admin_ws(websocket: WebSocket):
    await manager.connect_admin(websocket)
    try:
        status = await get_admin_status_data()
        conn = get_db()
        snap = conn.execute("SELECT snapshot FROM graph_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        if snap:
            status["graph"] = json.loads(snap["snapshot"])
        else:
            conn2 = get_db()
            rows = conn2.execute("SELECT * FROM students").fetchall()
            conn2.close()
            students = [dict(r) for r in rows]
            scores = {s["id"]: float(s.get("cheating_score", 0) or 0) for s in students}
            status["graph"] = build_graph_data(students, scores)
        await websocket.send_json({"type": "update", "data": status})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_admin(websocket)
    except Exception as e:
        print(f"[ws/admin]: {e}")
        manager.disconnect_admin(websocket)


# ─── Static Files ─────────────────────────────────────────────────────────────

app.mount("/", StaticFiles(directory="static", html=True), name="static")
