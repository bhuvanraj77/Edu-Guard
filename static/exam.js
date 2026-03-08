'use strict';

const QUESTIONS = [
  {
    text: "What is the value of x if 3x + 7 = 22?",
    options: ["3", "5", "7", "4"], answer: 1
  },
  {
    text: "Which planet is closest to the Sun?",
    options: ["Venus", "Earth", "Mercury", "Mars"], answer: 2
  },
  {
    text: "What is √144?",
    options: ["14", "12", "11", "16"], answer: 1
  },
  {
    text: "Which element has the chemical symbol 'O'?",
    options: ["Osmium", "Oxygen", "Gold", "Oxide"], answer: 1
  },
  {
    text: "What is the area of a circle with radius 5? (Use π ≈ 3.14)",
    options: ["31.4", "78.5", "25", "15.7"], answer: 1
  },
  {
    text: "What is the speed of light (approx)?",
    options: ["300,000 km/s", "150,000 km/s", "3,000 km/s", "30,000 km/s"], answer: 0
  },
  {
    text: "What is 2⁸?",
    options: ["128", "64", "256", "512"], answer: 2
  },
  {
    text: "Which gas makes up most of Earth's atmosphere?",
    options: ["Oxygen", "Carbon Dioxide", "Hydrogen", "Nitrogen"], answer: 3
  },
  {
    text: "If a triangle has angles 60°, 80°, what is the third?",
    options: ["40°", "50°", "30°", "60°"], answer: 0
  },
  {
    text: "What is the powerhouse of the cell?",
    options: ["Nucleus", "Ribosome", "Mitochondria", "Golgi apparatus"], answer: 2
  }
];

const CHEAT_ANSWERS = [2, 2, 1, 2, 3, 0, 2, 1, 3, 0];

let studentId = '';
let studentName = '';
let ipGroup = Math.floor(Math.random() * 5) + 1;
let answers = {};
let behaviors = { tab_switches: 0, paste_count: 0, keystroke_rate: 0, mouse_events: 0 };
let timerSeconds = 30 * 60;
let timerInterval = null;
let ws = null;
let wsInterval = null;
let keystrokeCount = 0;
let keystrokeTimer = null;

function startExam() {
  const idInput = document.getElementById('student-id-input').value.trim();
  const nameInput = document.getElementById('student-name-input').value.trim();
  if (!idInput) { showAlert('Please enter your Student ID'); return; }
  if (!nameInput) { showAlert('Please enter your name'); return; }

  studentId = idInput;
  studentName = nameInput;

  fetch('/api/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: studentId, name: studentName, ip_group: ipGroup })
  }).catch(() => {});

  document.getElementById('login-screen').classList.remove('active');
  document.getElementById('exam-screen').classList.add('active');
  document.getElementById('student-label').textContent = `${studentName} (${studentId})`;

  renderQuestions();
  startTimer();
  connectWebSocket();
  setupBehaviorListeners();
}

function renderQuestions() {
  const container = document.getElementById('question-container');
  container.innerHTML = QUESTIONS.map((q, i) => `
    <div class="question-card" id="q${i}">
      <div class="question-num">Question ${i + 1} of ${QUESTIONS.length}</div>
      <div class="question-text">${q.text}</div>
      <div class="options">
        ${q.options.map((opt, j) => `
          <label class="option-label" id="opt-${i}-${j}" onclick="selectOption(${i}, ${j})">
            <input type="radio" name="q${i}" value="${j}">
            <span class="option-radio"></span>
            <span>${opt}</span>
          </label>
        `).join('')}
      </div>
    </div>
  `).join('');
}

function selectOption(qIdx, optIdx) {
  const prev = answers[qIdx];
  answers[qIdx] = optIdx;
  if (prev !== optIdx) behaviors.answer_changes = (behaviors.answer_changes || 0) + 1;

  document.querySelectorAll(`#q${qIdx} .option-label`).forEach((el, i) => {
    el.classList.toggle('selected', i === optIdx);
  });
  document.getElementById(`q${qIdx}`).classList.add('answered');
  updateProgress();
}

function updateProgress() {
  const answered = Object.keys(answers).length;
  const pct = (answered / QUESTIONS.length) * 100;
  document.getElementById('progress-bar').style.width = pct + '%';
}

function startTimer() {
  timerInterval = setInterval(() => {
    timerSeconds--;
    updateTimerDisplay();
    if (timerSeconds <= 0) {
      clearInterval(timerInterval);
      submitExam();
    }
  }, 1000);
}

function updateTimerDisplay() {
  const m = Math.floor(timerSeconds / 60).toString().padStart(2, '0');
  const s = (timerSeconds % 60).toString().padStart(2, '0');
  const el = document.getElementById('timer-display');
  el.textContent = `${m}:${s}`;
  el.className = 'timer';
  if (timerSeconds <= 300) el.classList.add('warning');
  if (timerSeconds <= 60) { el.classList.remove('warning'); el.classList.add('danger'); }
}

function connectWebSocket() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws/student/${studentId}`);
  ws.onopen = () => { sendBehaviorUpdate(); };
  ws.onclose = () => {
    setTimeout(connectWebSocket, 3000);
  };
  wsInterval = setInterval(sendBehaviorUpdate, 5000);
}

function sendBehaviorUpdate() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  behaviors.keystroke_rate = Math.round((keystrokeCount / 5) * 60);
  keystrokeCount = 0;
  ws.send(JSON.stringify({
    id: studentId,
    name: studentName,
    ip_group: ipGroup,
    behaviors: { ...behaviors },
    answers: { ...answers }
  }));
}

function setupBehaviorListeners() {
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      behaviors.tab_switches = (behaviors.tab_switches || 0) + 1;
    }
  });

  document.addEventListener('paste', () => {
    behaviors.paste_count = (behaviors.paste_count || 0) + 1;
  });

  document.addEventListener('keydown', () => {
    keystrokeCount++;
  });

  document.addEventListener('mousemove', () => {
    behaviors.mouse_events = (behaviors.mouse_events || 0) + 1;
  });
}

function submitExam() {
  clearInterval(timerInterval);
  clearInterval(wsInterval);

  const submitTime = Date.now() / 1000;
  fetch('/api/submit_answers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: studentId, answers, submit_time: submitTime })
  }).catch(() => {});

  if (ws) ws.close();

  document.getElementById('exam-screen').classList.remove('active');
  document.getElementById('submitted-screen').classList.add('active');
  const answered = Object.keys(answers).length;
  document.getElementById('submitted-info').textContent =
    `Answered ${answered} of ${QUESTIONS.length} questions.`;
}

// ─── Cheat Simulation ──────────────────────────────────────────────────────

function simCopyAnswer() {
  CHEAT_ANSWERS.forEach((ans, i) => selectOption(i, ans));
  behaviors.paste_count = (behaviors.paste_count || 0) + 5;
  sendBehaviorUpdate();
  setCheatStatus('Copied cheating answers. Answer similarity will be very high.');
}

function simSyncTime() {
  fetch('/api/admin/status')
    .then(r => r.json())
    .then(data => {
      if (data.students && data.students.length > 0) {
        const submitted = data.students.filter(s => s.answers_count > 0);
        if (submitted.length > 0) {
          setCheatStatus('Submit time synced with another student. Timing attack active!');
        }
      }
    })
    .catch(() => {});
  timerSeconds = Math.floor(Math.random() * 30) + 5;
  updateTimerDisplay();
  setCheatStatus('Submit time will sync with peers — triggering in ~30s.');
}

function simGroupCheat() {
  ipGroup = 7;
  if (ws) {
    sendBehaviorUpdate();
  }
  behaviors.tab_switches = (behaviors.tab_switches || 0) + 8;
  setCheatStatus(`Joined cheat group (IP Group 7). Suspicious cluster forming!`);
  sendBehaviorUpdate();
}

function setCheatStatus(msg) {
  const el = document.getElementById('cheat-status');
  el.textContent = '⚡ ' + msg;
  setTimeout(() => { el.textContent = ''; }, 5000);
}

function showAlert(msg) {
  const el = document.getElementById('alert-banner');
  el.textContent = msg;
  el.classList.remove('hidden');
  setTimeout(() => el.classList.add('hidden'), 3000);
}
