// ── Timer ────────────────────────────────────────────────────────────────────

(function initTimer() {
  const el = document.getElementById('timer');
  if (!el) return;
  const start = new Date(window.STARTED_AT);
  setInterval(() => {
    const elapsed = Math.floor((Date.now() - start) / 1000);
    const h = Math.floor(elapsed / 3600);
    const m = Math.floor((elapsed % 3600) / 60);
    const s = elapsed % 60;
    el.textContent = h > 0
      ? `${h}:${pad(m)}:${pad(s)}`
      : `${pad(m)}:${pad(s)}`;
  }, 1000);
})();

function pad(n) { return String(n).padStart(2, '0'); }

// ── Exercise Modal ────────────────────────────────────────────────────────────
// (muscleMapHTML/hydrateMuscleThumbs/esc come from static/js/muscle-map.js, loaded in base.html)

let muscleFilter = '';
let searchTimer;

function openExModal() {
  document.getElementById('exModal').style.display = 'flex';
  loadMuscleChips();
  _muscleSvgReady.then(searchExercises);
  setTimeout(() => document.getElementById('exSearch')?.focus(), 50);
}

function closeExModal() {
  document.getElementById('exModal').style.display = 'none';
  document.getElementById('exSearch').value = '';
  muscleFilter = '';
  document.querySelectorAll('#muscleChips .chip').forEach(c => c.classList.remove('active'));
  document.querySelectorAll('#muscleChips .chip')[0]?.classList.add('active');
}

function loadMuscleChips() {
  const chips = document.getElementById('muscleChips');
  if (chips.dataset.loaded) return;
  chips.dataset.loaded = '1';
  fetch('/api/muscle-groups')
    .then(r => r.json())
    .then(groups => {
      groups.forEach(g => {
        const btn = document.createElement('button');
        btn.className = 'chip';
        btn.textContent = g;
        btn.onclick = () => filterMuscle(g, btn);
        chips.appendChild(btn);
      });
    });
}

function filterMuscle(muscle, el) {
  muscleFilter = muscle;
  document.querySelectorAll('#muscleChips .chip').forEach(c => c.classList.remove('active'));
  el.classList.add('active');
  searchExercises();
}

function searchExercises() {
  const q = document.getElementById('exSearch')?.value || '';
  fetch(`/api/exercises/search?q=${encodeURIComponent(q)}&muscle=${encodeURIComponent(muscleFilter)}`)
    .then(r => r.json())
    .then(renderExResults);
}

function renderExResults(exercises) {
  const el = document.getElementById('exResults');
  if (!exercises.length) {
    el.innerHTML = '<p style="color:var(--text-muted);font-size:13px;padding:12px 0">No results</p>';
    return;
  }
  el.innerHTML = exercises.map(e => `
    <div class="ex-result-item" data-id="${e.id}" data-name="${esc(e.name)}">
      <div class="ex-result-thumb">${muscleMapHTML(e.muscle_group, e.secondary_muscles)}</div>
      <div class="ex-result-info">
        <div class="ex-result-name">${esc(e.name)}</div>
        <div class="ex-result-meta">${esc(e.muscle_group)} · ${esc(e.category)}</div>
      </div>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
      </svg>
    </div>`).join('');
}

document.getElementById('exResults')?.addEventListener('click', e => {
  const item = e.target.closest('.ex-result-item');
  if (item) addExercise(parseInt(item.dataset.id, 10), item.dataset.name);
});

document.getElementById('exSearch')?.addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(searchExercises, 280);
});

// ── Add Exercise ──────────────────────────────────────────────────────────────

function addExercise(exerciseId, name) {
  fetch(`/api/workout/${window.WORKOUT_ID}/add-exercise`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({exercise_id: exerciseId})
  })
  .then(r => r.json())
  .then(data => {
    closeExModal();
    renderExerciseCard(data);
    document.getElementById('emptyWorkout')?.remove();
    showToast(`${name} added`);
    document.getElementById(`se-${data.session_exercise_id}`)
      ?.scrollIntoView({behavior: 'smooth', block: 'start'});
  });
}

function renderExerciseCard(data) {
  const container = document.getElementById('exercisesContainer');
  const prev = data.previous?.sets || [];
  const setsHTML = data.sets.map((s, i) => buildSetRow(s, prev[i] ?? null)).join('');

  const div = document.createElement('div');
  div.className = 'exercise-card';
  div.id = `se-${data.session_exercise_id}`;
  div.innerHTML = `
    <div class="ex-card-header">
      <div class="ex-card-thumb">${muscleMapHTML(data.exercise.muscle_group, data.exercise.secondary_muscles)}</div>
      <div class="ex-card-info">
        <div class="ex-card-name">${esc(data.exercise.name)}</div>
        <span class="muscle-chip">${esc(data.exercise.muscle_group)}</span>
      </div>
      <button class="btn-icon-danger" onclick="removeExercise(${data.session_exercise_id})" title="Remove">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
        </svg>
      </button>
    </div>
    <div class="sets-table">
      <div class="sets-header">
        <span>Set</span><span>Previous</span><span>Weight (kg)</span><span>Reps</span><span></span>
      </div>
      <div class="sets-body" id="sets-${data.session_exercise_id}">${setsHTML}</div>
    </div>
    <div class="card-footer">
      <button class="btn-add-set" onclick="addSet(${data.session_exercise_id})">+ Add Set</button>
    </div>`;
  container.appendChild(div);
}

function buildSetRow(s, prev) {
  const prevText = prev && prev.weight && prev.reps
    ? `${prev.weight} × ${prev.reps}`
    : '—';
  return `
    <div class="set-row ${s.completed ? 'completed' : ''}" id="set-${s.id}">
      <span class="set-num">${s.set_number}</span>
      <span class="set-prev">${esc(prevText)}</span>
      <input type="number" class="set-input" value="${s.weight ?? ''}" placeholder="0"
             step="0.5" min="0" onchange="updateSet(${s.id}, 'weight', this.value)">
      <input type="number" class="set-input" value="${s.reps ?? ''}" placeholder="0"
             step="1" min="0" onchange="updateSet(${s.id}, 'reps', this.value)">
      <button class="complete-btn ${s.completed ? 'done' : ''}" onclick="toggleComplete(${s.id}, this)">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
          <polyline points="20 6 9 17 4 12"/>
        </svg>
      </button>
    </div>`;
}

// ── Set Actions ───────────────────────────────────────────────────────────────

function addSet(seId) {
  fetch(`/api/session-exercise/${seId}/add-set`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}
  })
  .then(r => r.json())
  .then(s => {
    const body = document.getElementById(`sets-${seId}`);
    body.insertAdjacentHTML('beforeend', buildSetRow(s, null));
  });
}

function updateSet(setId, field, value) {
  const body = {};
  body[field] = value === '' ? null : parseFloat(value);
  fetch(`/api/set/${setId}`, {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
}

function toggleComplete(setId, btn) {
  const row = document.getElementById(`set-${setId}`);
  const inputs = row.querySelectorAll('.set-input');
  const weight = inputs[0].value;
  const reps = inputs[1].value;
  const nowDone = !btn.classList.contains('done');

  btn.classList.toggle('done', nowDone);
  row.classList.toggle('completed', nowDone);

  fetch(`/api/set/${setId}`, {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      completed: nowDone,
      weight: weight ? parseFloat(weight) : null,
      reps: reps ? parseInt(reps) : null
    })
  });

  if (nowDone) showToast('Set done ✓');
}

function removeExercise(seId) {
  if (!confirm('Remove this exercise?')) return;
  fetch(`/api/session-exercise/${seId}`, {method: 'DELETE'}).then(() => {
    document.getElementById(`se-${seId}`)?.remove();
    showToast('Exercise removed');
  });
}

// ── Finish Workout ────────────────────────────────────────────────────────────

function finishWorkout() {
  if (!confirm('Finish and save this workout?')) return;
  fetch(`/api/workout/${window.WORKOUT_ID}/finish`, {method: 'POST'})
    .then(r => r.json())
    .then(data => {
      if (data.new_prs?.length) {
        showToast(`🏆 ${data.new_prs.length} new PR${data.new_prs.length > 1 ? 's' : ''}`);
        setTimeout(() => { if (data.redirect) window.location.href = data.redirect; }, 1600);
      } else if (data.redirect) {
        window.location.href = data.redirect;
      }
    });
}

// ── Utils ─────────────────────────────────────────────────────────────────────

function showToast(msg) {
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('show'), 2000);
}
