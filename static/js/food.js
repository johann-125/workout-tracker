// ── Food Log ─────────────────────────────────────────────────────────────────

let FOOD_DATE = '';
let currentMeal = 'snack';
let currentCategory = '';
let currentFood = null;
let currentUnit = 'serving';
let foodSearchTimeout;

function initFoodLog(dateStr) {
  FOOD_DATE = dateStr;
  document.getElementById('foodSearch').addEventListener('input', () => {
    clearTimeout(foodSearchTimeout);
    foodSearchTimeout = setTimeout(runFoodSearch, 280);
  });
  document.getElementById('foodResults').addEventListener('click', e => {
    const item = e.target.closest('.food-result-item');
    if (item) openConfirmModal(JSON.parse(item.dataset.food));
  });
}

function openSearchModal(meal) {
  currentMeal = meal;
  document.getElementById('searchMealLabel').textContent = meal;
  document.getElementById('foodSearch').value = '';
  document.getElementById('foodResults').innerHTML = '';
  document.getElementById('customForm').style.display = 'none';
  document.getElementById('searchModal').style.display = 'flex';
  document.getElementById('foodSearch').focus();
}

function closeSearchModal() {
  document.getElementById('searchModal').style.display = 'none';
}

function filterCategory(cat, el) {
  currentCategory = cat;
  document.querySelectorAll('#foodChips .chip').forEach(c => c.classList.remove('active'));
  el.classList.add('active');
  runFoodSearch();
}

function runFoodSearch() {
  const q = document.getElementById('foodSearch').value.trim();
  const el = document.getElementById('foodResults');
  if (!q) { el.innerHTML = ''; return; }
  fetch(`/api/foods/search?q=${encodeURIComponent(q)}&category=${encodeURIComponent(currentCategory)}`)
    .then(r => r.json()).then(renderFoodResults);
}

function renderFoodResults(foods) {
  const el = document.getElementById('foodResults');
  if (!foods.length) { el.innerHTML = '<p class="empty-sub" style="padding:12px 0">No results</p>'; return; }
  el.innerHTML = foods.map(f => {
    const badgeClass = f.source === 'openfoodfacts' ? 'openfoodfacts'
      : f.source === 'custom' ? 'custom' : (f.category || 'general');
    const badgeText = f.source === 'openfoodfacts' ? 'web' : f.source === 'custom' ? 'mine' : f.category;
    return `
    <div class="food-result-item" data-food='${esc(JSON.stringify(f)).replace(/'/g, "&#39;")}'>
      <div class="food-result-info">
        <div class="food-result-name">${esc(f.name)}</div>
        <div class="food-result-meta">${esc(f.serving_size || '')} · ${Math.round(f.calories)} kcal</div>
      </div>
      <span class="food-source-badge ${esc(badgeClass)}">${esc(badgeText)}</span>
    </div>`;
  }).join('');
}

function toggleCustomForm() {
  const form = document.getElementById('customForm');
  form.style.display = form.style.display === 'none' ? 'block' : 'none';
}

function selectCustomFood() {
  const name = document.getElementById('customName').value.trim();
  if (!name) { showFoodToast('Name is required'); return; }
  const gramsInput = document.getElementById('customServingGrams').value;
  openConfirmModal({
    name,
    serving_size: document.getElementById('customServing').value.trim() || '1 serving',
    serving_grams: gramsInput ? parseFloat(gramsInput) : null,
    calories: parseFloat(document.getElementById('customCalories').value) || 0,
    protein: parseFloat(document.getElementById('customProtein').value) || 0,
    carbs: parseFloat(document.getElementById('customCarbs').value) || 0,
    fat: parseFloat(document.getElementById('customFat').value) || 0,
    source: 'custom', category: 'custom',
  });
}

function openConfirmModal(food) {
  currentFood = food;
  document.getElementById('confirmFoodName').textContent = food.name;
  document.getElementById('confirmServingInfo').textContent =
    `1 serving = ${food.serving_size || '1 serving'} · ${Math.round(food.calories)} kcal · P ${food.protein}g · C ${food.carbs}g · F ${food.fat}g`;
  document.getElementById('confirmServings').value = 1;
  document.getElementById('confirmGrams').value = food.serving_grams || '';
  document.getElementById('confirmMeal').value = currentMeal;

  const gramsBtn = document.getElementById('unitBtnGrams');
  gramsBtn.disabled = !food.serving_grams;
  gramsBtn.title = food.serving_grams ? '' : 'No gram weight known for this food';
  setUnit('serving');
  document.getElementById('confirmModal').style.display = 'flex';
}

function closeConfirmModal() {
  document.getElementById('confirmModal').style.display = 'none';
}

function setUnit(unit) {
  currentUnit = unit;
  document.getElementById('unitBtnServing').classList.toggle('active', unit === 'serving');
  document.getElementById('unitBtnGrams').classList.toggle('active', unit === 'grams');
  document.getElementById('servingsField').style.display = unit === 'serving' ? 'flex' : 'none';
  document.getElementById('gramsFieldWrap').style.display = unit === 'grams' ? 'flex' : 'none';
  updatePreview();
}

function currentServingsMultiplier() {
  if (!currentFood) return 0;
  if (currentUnit === 'grams') {
    const grams = parseFloat(document.getElementById('confirmGrams').value) || 0;
    return currentFood.serving_grams ? grams / currentFood.serving_grams : 0;
  }
  return parseFloat(document.getElementById('confirmServings').value) || 0;
}

function updatePreview() {
  const el = document.getElementById('confirmPreview');
  if (!currentFood) { el.innerHTML = ''; return; }
  const factor = currentServingsMultiplier();
  const round1 = n => Math.round(n * 10) / 10;
  el.innerHTML = `
    <div class="macro-card cal"><div class="macro-value">${Math.round(currentFood.calories * factor)}</div><div class="macro-label">Calories</div></div>
    <div class="macro-card protein"><div class="macro-value">${round1(currentFood.protein * factor)}g</div><div class="macro-label">Protein</div></div>
    <div class="macro-card carbs"><div class="macro-value">${round1(currentFood.carbs * factor)}g</div><div class="macro-label">Carbs</div></div>
    <div class="macro-card fat"><div class="macro-value">${round1(currentFood.fat * factor)}g</div><div class="macro-label">Fat</div></div>`;
}

function confirmLog() {
  const servings = currentServingsMultiplier();
  if (servings <= 0) { showFoodToast('Enter a valid amount'); return; }
  const amount = currentUnit === 'grams'
    ? parseFloat(document.getElementById('confirmGrams').value) || 0
    : parseFloat(document.getElementById('confirmServings').value) || 1;
  const meal = document.getElementById('confirmMeal').value;
  fetch('/api/food-log/add', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      name: currentFood.name, food_id: currentFood.id, source: currentFood.source,
      category: currentFood.category, serving_size: currentFood.serving_size,
      serving_grams: currentFood.serving_grams,
      calories: currentFood.calories, protein: currentFood.protein,
      carbs: currentFood.carbs, fat: currentFood.fat,
      servings, unit: currentUnit, amount, meal_type: meal,
    })
  }).then(r => r.json()).then(() => {
    window.location.href = `/food-log?date=${FOOD_DATE}`;
  });
}

function deleteEntry(id) {
  fetch(`/api/food-log/${id}`, {method: 'DELETE'}).then(() => {
    window.location.href = `/food-log?date=${FOOD_DATE}`;
  });
}

function showFoodToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2000);
}
