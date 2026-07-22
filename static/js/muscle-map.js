// ── Muscle Map ────────────────────────────────────────────────────────────────
// Grey body-outline diagrams with the target muscle highlighted in red, built
// from static/img/muscle-{front,back}.svg (each region tagged data-muscle="...").

const MUSCLE_MAP_FRONT_VIEW = new Set(
  ['Chest', 'Biceps', 'Core', 'Quadriceps', 'Adductors', 'Neck', 'Shoulders', 'Forearms']);
let _muscleSvgCache = {};
const _muscleSvgReady = Promise.all([
  fetch('/static/img/muscle-front.svg').then(r => r.text()),
  fetch('/static/img/muscle-back.svg').then(r => r.text()),
]).then(([front, back]) => { _muscleSvgCache = {front, back}; }).catch(() => {});

function muscleMapHTML(muscleGroup, secondaryMuscles) {
  const view = MUSCLE_MAP_FRONT_VIEW.has(muscleGroup) ? 'front' : 'back';
  const svgText = _muscleSvgCache[view];
  if (!svgText) return '';
  const wrapper = document.createElement('div');
  wrapper.innerHTML = svgText;
  const svg = wrapper.querySelector('svg');
  (secondaryMuscles || []).forEach(m => {
    svg.querySelectorAll(`[data-muscle="${cssEsc(m)}"]`).forEach(el => el.setAttribute('fill', '#f0a3a6'));
  });
  svg.querySelectorAll(`[data-muscle="${cssEsc(muscleGroup)}"]`).forEach(el => el.setAttribute('fill', '#e0393f'));
  return svg.outerHTML;
}

function cssEsc(s) { return window.CSS?.escape ? CSS.escape(s) : String(s).replace(/"/g, '\\"'); }

function esc(str) {
  const d = document.createElement('div');
  d.appendChild(document.createTextNode(String(str)));
  return d.innerHTML;
}

// Hydrates server-rendered placeholders once the SVGs are loaded, e.g.:
// <div class="ex-card-thumb" data-muscle="Chest" data-secondary="Triceps, Shoulders"></div>
function hydrateMuscleThumbs(root) {
  root = root || document;
  _muscleSvgReady.then(() => {
    root.querySelectorAll('[data-muscle]:empty').forEach(el => {
      const secondary = (el.dataset.secondary || '').split(',').map(s => s.trim()).filter(Boolean);
      el.innerHTML = muscleMapHTML(el.dataset.muscle, secondary);
    });
  });
}

document.addEventListener('DOMContentLoaded', () => hydrateMuscleThumbs());
