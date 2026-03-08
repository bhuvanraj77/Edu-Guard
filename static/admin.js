'use strict';

let cy = null;
let ws = null;
let lastAlertedStudents = new Set();
let alertTimeout = null;
let isLoggedIn = false;

// ─── Login ────────────────────────────────────────────────────────────────

function adminLogin() {
  const user = document.getElementById('admin-user').value;
  const pass = document.getElementById('admin-pass').value;
  fetch('/api/admin/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: user, password: pass })
  })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        document.getElementById('admin-login-screen').classList.remove('active');
        document.getElementById('admin-dashboard').classList.add('active');
        isLoggedIn = true;
        initDashboard();
      }
    })
    .catch(() => alert('Login failed'));
}

document.getElementById('admin-user').addEventListener('keydown', e => { if (e.key === 'Enter') adminLogin(); });
document.getElementById('admin-pass').addEventListener('keydown', e => { if (e.key === 'Enter') adminLogin(); });

// ─── Dashboard Init ───────────────────────────────────────────────────────

function initDashboard() {
  initCytoscape();
  connectAdminWS();
}

// ─── Cytoscape.js Graph ───────────────────────────────────────────────────

function initCytoscape() {
  const container = document.getElementById('cy');
  container.innerHTML = '';

  cy = cytoscape({
    container,
    style: [
      {
        selector: 'node',
        style: {
          'background-color': '#3fb950',
          'label': 'data(label)',
          'color': '#fff',
          'font-size': '10px',
          'text-valign': 'bottom',
          'text-margin-y': '4px',
          'width': 36,
          'height': 36,
          'border-width': 2,
          'border-color': '#30363d',
          'transition-property': 'background-color border-color width height',
          'transition-duration': '0.4s'
        }
      },
      {
        selector: 'node.medium',
        style: {
          'background-color': '#d29922',
          'border-color': '#d29922',
          'width': 40,
          'height': 40
        }
      },
      {
        selector: 'node.high',
        style: {
          'background-color': '#f85149',
          'border-color': '#f85149',
          'width': 46,
          'height': 46
        }
      },
      {
        selector: 'node.pulsing',
        style: {
          'border-width': 4,
          'border-color': '#ff453a',
          'overlay-color': '#f85149',
          'overlay-opacity': 0.15,
          'overlay-padding': 6
        }
      },
      {
        selector: 'edge',
        style: {
          'width': 2,
          'line-color': '#30363d',
          'opacity': 0.6,
          'curve-style': 'bezier',
          'label': 'data(label)',
          'font-size': '9px',
          'color': '#8b949e',
          'text-rotation': 'autorotate',
          'text-margin-y': '-6px',
          'transition-property': 'line-color width opacity',
          'transition-duration': '0.4s'
        }
      },
      {
        selector: 'edge.suspicious',
        style: {
          'line-color': '#f85149',
          'width': 3,
          'opacity': 0.9
        }
      },
      {
        selector: 'edge.medium-risk',
        style: {
          'line-color': '#d29922',
          'width': 2.5,
          'opacity': 0.8
        }
      }
    ],
    layout: { name: 'cose', padding: 30, randomize: false, animate: true, animationDuration: 400 },
    userZoomingEnabled: true,
    userPanningEnabled: true,
    minZoom: 0.3,
    maxZoom: 3
  });

  cy.on('mouseover', 'edge', function(e) {
    const reasons = e.target.data('reasons') || [];
    const tooltip = document.getElementById('edge-tooltip');
    tooltip.innerHTML = `<strong>Similarity:</strong> ${reasons.join(', ') || 'unknown'}`;
    tooltip.style.left = e.originalEvent.offsetX + 10 + 'px';
    tooltip.style.top = e.originalEvent.offsetY + 10 + 'px';
    tooltip.classList.remove('hidden');
  });
  cy.on('mouseout', 'edge', () => {
    document.getElementById('edge-tooltip').classList.add('hidden');
  });
}

function updateGraph(graphData) {
  if (!cy) return;
  const { nodes, edges } = graphData;
  if (!nodes || nodes.length === 0) return;

  const cyElements = cy.elements();

  // Update or add nodes
  nodes.forEach(n => {
    const existing = cy.getElementById(n.id);
    const label = `${n.name}\n${n.score.toFixed(0)}%`;
    const riskClass = n.score >= 70 ? 'high' : n.score >= 40 ? 'medium' : 'safe';

    if (existing.length > 0) {
      existing.data('label', label);
      existing.data('score', n.score);
      existing.removeClass('safe medium high');
      existing.addClass(riskClass === 'safe' ? 'safe' : riskClass === 'medium' ? 'medium' : 'high');
    } else {
      cy.add({
        group: 'nodes',
        data: { id: n.id, label, score: n.score, name: n.name },
        classes: riskClass === 'safe' ? 'safe' : riskClass === 'medium' ? 'medium' : 'high'
      });
    }
  });

  // Remove stale nodes
  cy.nodes().forEach(node => {
    if (!nodes.find(n => n.id === node.id())) {
      cy.remove(node);
    }
  });

  // Rebuild edges
  cy.edges().remove();
  edges.forEach(e => {
    const edgeId = `e-${e.source}-${e.target}`;
    const label = (e.reasons || []).slice(0, 1).join(',');
    const riskClass = e.weight > 0.7 ? 'suspicious' : e.weight > 0.4 ? 'medium-risk' : '';
    cy.add({
      group: 'edges',
      data: {
        id: edgeId,
        source: e.source,
        target: e.target,
        weight: e.weight,
        reasons: (e.reasons || []).join(', '),
        label
      },
      classes: riskClass
    });
  });

  // Pulse high-risk nodes
  cy.nodes('.high').addClass('pulsing');
  setTimeout(() => cy.nodes().removeClass('pulsing'), 2000);

  // Re-layout only if big change
  cy.layout({
    name: 'cose',
    padding: 30,
    animate: true,
    animationDuration: 500,
    randomize: false,
    nodeDimensionsIncludeLabels: true
  }).run();
}

function exportGraph() {
  if (!cy) return;
  const png = cy.png({ output: 'blob', bg: '#0a0e14', full: true, scale: 2 });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(png);
  a.download = `cheating-graph-${Date.now()}.png`;
  a.click();
}

// ─── WebSocket Admin ──────────────────────────────────────────────────────

function connectAdminWS() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws/admin`);

  const dot = document.getElementById('connection-indicator');

  ws.onopen = () => {
    dot.className = 'conn-dot connected';
  };

  ws.onclose = () => {
    dot.className = 'conn-dot disconnected';
    setTimeout(connectAdminWS, 3000);
  };

  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'update') {
        handleUpdate(msg.data);
      }
    } catch (e) {
      console.error('[admin ws] parse error:', e);
    }
  };
}

function handleUpdate(data) {
  const { students, graph } = data;
  if (students) {
    updateStudentList(students);
    updateStatsRow(students);
    updateReportTable(students);
  }
  if (graph) {
    updateGraph(graph);
  }
  document.getElementById('last-update').textContent = 'Updated ' + new Date().toLocaleTimeString();
  checkForAlerts(students || []);
}

// ─── Student List Sidebar ─────────────────────────────────────────────────

function updateStudentList(students) {
  const list = document.getElementById('student-list');
  const count = document.getElementById('student-count');
  count.textContent = students.length;

  if (students.length === 0) {
    list.innerHTML = '<div class="empty-state">No students connected.<br>Run demo or open /</div>';
    return;
  }

  const sorted = [...students].sort((a, b) => b.score - a.score);
  list.innerHTML = sorted.map(s => {
    const riskClass = s.score >= 70 ? 'high-risk' : s.score >= 40 ? 'med-risk' : 'safe';
    const initials = (s.name || s.id).slice(0, 2).toUpperCase();
    return `
      <div class="student-item ${riskClass}">
        <div class="student-avatar ${riskClass}">${initials}</div>
        <div class="student-meta">
          <div class="student-name">${s.name || s.id}</div>
          <div class="student-score-line">${s.answers_count}/10 answered</div>
        </div>
        <span class="student-score-badge ${riskClass === 'high-risk' ? 'score-danger' : riskClass === 'med-risk' ? 'score-warn' : 'score-safe'}">${s.score.toFixed(0)}%</span>
      </div>
    `;
  }).join('');
}

// ─── Stats Row ────────────────────────────────────────────────────────────

function updateStatsRow(students) {
  document.getElementById('stat-total').textContent = students.length;
  const highRisk = students.filter(s => s.score >= 70);
  document.getElementById('stat-high-risk').textContent = highRisk.length;
  document.getElementById('stat-safe').textContent = students.filter(s => s.score < 30).length;

  // Estimate clusters by counting students in same ip_group with high scores
  const groups = {};
  students.filter(s => s.score >= 40).forEach(s => {
    if (s.ip_group > 0) {
      groups[s.ip_group] = (groups[s.ip_group] || 0) + 1;
    }
  });
  const clusters = Object.values(groups).filter(c => c >= 2).length;
  document.getElementById('stat-clusters').textContent = clusters;
}

// ─── Report Table ─────────────────────────────────────────────────────────

function updateReportTable(students) {
  const tbody = document.getElementById('report-tbody');
  if (students.length === 0) {
    tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--text3);padding:24px">No student data</td></tr>';
    return;
  }

  const sorted = [...students].sort((a, b) => b.score - a.score);
  tbody.innerHTML = sorted.map(s => {
    const riskClass = s.score >= 70 ? 'high-risk' : s.score >= 40 ? 'med-risk' : '';
    const barColor = s.score >= 70 ? 'var(--danger)' : s.score >= 40 ? 'var(--warn)' : 'var(--success)';
    const statusClass = s.score >= 70 ? 'status-danger' : s.score >= 40 ? 'status-warn' : 'status-safe';
    const statusText = s.score >= 70 ? 'HIGH RISK' : s.score >= 40 ? 'WATCH' : 'SAFE';
    return `
      <tr class="${riskClass}">
        <td>${s.id}</td>
        <td>${s.name || s.id}</td>
        <td>${s.answers_count}/10</td>
        <td>${s.ip_group > 0 ? 'Group ' + s.ip_group : '-'}</td>
        <td>${s.tab_switches}</td>
        <td>${s.paste_count}</td>
        <td>${s.keystroke_rate || 0}</td>
        <td>
          <div class="risk-bar-wrap">
            <div class="risk-bar-bg">
              <div class="risk-bar-fill" style="width:${s.score}%;background:${barColor}"></div>
            </div>
            <span class="risk-val" style="color:${barColor}">${s.score.toFixed(0)}%</span>
          </div>
        </td>
        <td><span class="status-badge ${statusClass}">${statusText}</span></td>
      </tr>
    `;
  }).join('');
}

// ─── Alerts ───────────────────────────────────────────────────────────────

function checkForAlerts(students) {
  const highRisk = students.filter(s => s.score >= 70);
  const newlyFlagged = highRisk.filter(s => !lastAlertedStudents.has(s.id));

  if (newlyFlagged.length > 0) {
    newlyFlagged.forEach(s => lastAlertedStudents.add(s.id));
    const names = newlyFlagged.map(s => `${s.name} (${s.score.toFixed(0)}%)`).join(', ');
    showAlertPopup(`Flagged: ${names} — suspected collaborative cheating detected via GNN analysis.`);
  }
}

function showAlertPopup(msg) {
  const popup = document.getElementById('alert-popup');
  document.getElementById('alert-popup-msg').textContent = msg;
  popup.classList.remove('hidden');
  if (alertTimeout) clearTimeout(alertTimeout);
  alertTimeout = setTimeout(closeAlert, 8000);
}

function closeAlert() {
  document.getElementById('alert-popup').classList.add('hidden');
}

// ─── Demo & Clear ─────────────────────────────────────────────────────────

function seedDemo() {
  lastAlertedStudents.clear();
  fetch('/api/demo/seed', { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      console.log('[demo] seeded:', data);
    })
    .catch(e => console.error('[demo] error:', e));
}

function clearAll() {
  if (!confirm('Clear all student data?')) return;
  lastAlertedStudents.clear();
  fetch('/api/admin/clear', { method: 'DELETE' })
    .then(() => {
      updateStudentList([]);
      updateStatsRow([]);
      updateReportTable([]);
      if (cy) cy.elements().remove();
    })
    .catch(e => console.error('[clear] error:', e));
}
