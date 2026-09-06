const deptFilter = document.getElementById('deptFilter');
const recalcBtn = document.getElementById('recalcBtn');
let knownDepartments = [];

async function loadDoctors() {
  const res = await fetch('/api/doctors');
  const doctors = await res.json();
  const el = document.getElementById('doctorList');
  el.innerHTML = doctors.map(d => `
    <div class="doctor-row">
      <span>${d.name}<br><span class="dept">${d.department}</span></span>
      <button class="toggle-btn ${d.available ? 'on' : 'off'}" onclick="toggleDoctor(${d.id})">
        ${d.available ? 'ON DUTY' : 'OFF DUTY'}
      </button>
    </div>
  `).join('');
}

async function toggleDoctor(id) {
  await fetch(`/api/doctors/${id}/toggle`, { method: 'POST' });
  refreshAll();
}

async function loadAdvisory() {
  const dept = deptFilter.value || knownDepartments[0];
  if (!dept) {
    document.getElementById('advisoryText').textContent = 'No departments configured yet.';
    return;
  }
  const res = await fetch('/api/advisory?department=' + encodeURIComponent(dept));
  const data = await res.json();
  const box = document.getElementById('advisory');
  box.className = 'advisory ' + (data.level || 'good');
  document.getElementById('advisoryText').textContent = `[${dept}] ` + data.text;
}

async function loadQueue() {
  const dept = deptFilter.value;
  const url = '/api/queue' + (dept ? '?department=' + encodeURIComponent(dept) : '');
  const res = await fetch(url);
  const data = await res.json();

  knownDepartments = data.departments || [];
  const currentOptions = Array.from(deptFilter.options).map(o => o.value).filter(Boolean);
  if (JSON.stringify(currentOptions) !== JSON.stringify(knownDepartments)) {
    const selected = deptFilter.value;
    deptFilter.innerHTML = '<option value="">All departments</option>' +
      knownDepartments.map(d => `<option ${d === selected ? 'selected' : ''}>${d}</option>`).join('');
  }

  const body = document.getElementById('queueBody');
  const table = document.getElementById('queueTable');
  const empty = document.getElementById('emptyState');

  if (data.queue.length === 0) {
    table.style.display = 'none';
    empty.style.display = 'block';
  } else {
    table.style.display = 'table';
    empty.style.display = 'none';
    body.innerHTML = data.queue.map(r => `
      <tr>
        <td class="token">${r.token}</td>
        <td>${r.name}</td>
        <td>${r.department}</td>
        <td class="time">${r.expected_time_display}${r.minutes_until !== null ? ` <span style="color:var(--board-dim); font-size:12px;">(${r.minutes_until > 0 ? '~' + Math.round(r.minutes_until) + ' min' : 'now'})</span>` : ''}</td>
        <td><span class="status-pill ${r.status}">${r.status.replace('_',' ')}</span></td>
        <td><button class="secondary" onclick="markDone(${r.id})">Mark done</button></td>
      </tr>
    `).join('');
  }

  document.getElementById('stamp').textContent = 'updated ' + new Date().toLocaleTimeString();
}

async function loadNotifications() {
  const res = await fetch('/api/notifications');
  const rows = await res.json();
  document.getElementById('notifBody').innerHTML = rows.map(r => `
    <div class="notif-entry"><span class="tkn">${r.token}</span>${r.message}</div>
  `).join('') || '<div class="notif-entry">No notifications yet.</div>';
}

async function loadLog() {
  const res = await fetch('/api/log');
  const rows = await res.json();
  document.getElementById('logBody').innerHTML = rows.map(r => `
    <div class="log-entry"><span class="step">${r.step}</span>[${r.department}] ${r.detail}</div>
  `).join('');
}

async function markDone(queueId) {
  await fetch('/api/mark-done', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({queue_id: queueId})
  });
  refreshAll();
}

async function refreshAll() {
  await loadQueue();
  await Promise.all([loadDoctors(), loadAdvisory(), loadNotifications(), loadLog()]);
}

// ---------------- AI Settings (user-entered API key) ----------------
const apiKeyInput = document.getElementById('apiKeyInput');
const saveKeyBtn = document.getElementById('saveKeyBtn');
const clearKeyBtn = document.getElementById('clearKeyBtn');
const apiKeyStatus = document.getElementById('apiKeyStatus');

function renderKeyStatus(data) {
  if (data.configured) {
    const sourceLabel = data.source === 'dashboard' ? 'entered here' : 'from server environment';
    apiKeyStatus.innerHTML = `<span class="key-ok">● Active</span> (${sourceLabel})<br><span class="key-masked">${data.masked}</span>`;
    clearKeyBtn.style.display = data.source === 'dashboard' ? 'inline-block' : 'none';
  } else {
    apiKeyStatus.innerHTML = `<span class="key-off">● No key set</span> — using rule-based advisory`;
    clearKeyBtn.style.display = 'none';
  }
}

async function loadKeyStatus() {
  const res = await fetch('/api/settings');
  const data = await res.json();
  renderKeyStatus(data);
}

saveKeyBtn.addEventListener('click', async () => {
  const key = apiKeyInput.value.trim();
  if (!key) return;
  saveKeyBtn.disabled = true;
  saveKeyBtn.textContent = 'Saving…';
  const res = await fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ api_key: key })
  });
  const data = await res.json();
  renderKeyStatus(data);
  apiKeyInput.value = '';
  saveKeyBtn.disabled = false;
  saveKeyBtn.textContent = 'Save';
  await loadAdvisory();
});

clearKeyBtn.addEventListener('click', async () => {
  const res = await fetch('/api/settings', { method: 'DELETE' });
  const data = await res.json();
  renderKeyStatus(data);
  await loadAdvisory();
});

loadKeyStatus();

recalcBtn.addEventListener('click', async () => {
  recalcBtn.disabled = true;
  recalcBtn.textContent = 'Recalculating…';
  await fetch('/api/recalculate', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({})
  });
  await refreshAll();
  recalcBtn.disabled = false;
  recalcBtn.textContent = 'Recalculate now';
});

deptFilter.addEventListener('change', refreshAll);

refreshAll();
setInterval(refreshAll, 6000);
