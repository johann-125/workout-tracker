import os
import json
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
from flask import (Flask, render_template, request, redirect, url_for,
                   jsonify, flash, session as flask_session)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                         login_required, current_user)
from werkzeug.security import generate_password_hash, check_password_hash
import progress_analytics

load_dotenv()  # picks up DATABASE_URL etc. from a local .env file if present (never committed)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-me')

_db_url = os.environ.get('DATABASE_URL', 'sqlite:///workout.db')
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
elif os.path.isdir('/data'):
    _db_url = 'sqlite:////data/workout.db'
app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = ''

# ─── Models ───────────────────────────────────────────────────────────────────

class User(db.Model, UserMixin):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class Exercise(db.Model):
    __tablename__ = 'exercises'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    muscle_group = db.Column(db.String(50))
    category = db.Column(db.String(50), default='strength')
    equipment = db.Column(db.String(50))
    level = db.Column(db.String(20))
    mechanic = db.Column(db.String(20))
    force = db.Column(db.String(20))
    secondary_muscles = db.Column(db.Text)
    instructions = db.Column(db.Text)

    def to_dict(self):
        return {'id': self.id, 'name': self.name,
                'muscle_group': self.muscle_group, 'category': self.category,
                'equipment': self.equipment, 'level': self.level,
                'mechanic': self.mechanic, 'force': self.force,
                'secondary_muscles': self.secondary_muscles.split(', ') if self.secondary_muscles else [],
                'instructions': self.instructions.split('\n') if self.instructions else []}


class WorkoutPlan(db.Model):
    __tablename__ = 'workout_plans'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    exercises = db.relationship('PlanExercise', backref='plan', lazy=True,
                                order_by='PlanExercise.order',
                                cascade='all, delete-orphan')


class PlanExercise(db.Model):
    __tablename__ = 'plan_exercises'
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey('workout_plans.id'), nullable=False)
    exercise_id = db.Column(db.Integer, db.ForeignKey('exercises.id'), nullable=False)
    default_sets = db.Column(db.Integer, default=3)
    default_reps = db.Column(db.Integer, default=8)
    order = db.Column(db.Integer, default=0)
    exercise = db.relationship('Exercise')

    def to_dict(self):
        return {'id': self.id, 'exercise': self.exercise.to_dict(),
                'default_sets': self.default_sets, 'default_reps': self.default_reps}


class WorkoutSession(db.Model):
    __tablename__ = 'workout_sessions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    plan_id = db.Column(db.Integer, db.ForeignKey('workout_plans.id'), nullable=True)
    name = db.Column(db.String(100))
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    finished_at = db.Column(db.DateTime)
    completed = db.Column(db.Boolean, default=False)
    exercises = db.relationship('SessionExercise', backref='session', lazy=True,
                                order_by='SessionExercise.order',
                                cascade='all, delete-orphan')

    @property
    def duration_minutes(self):
        if self.finished_at and self.started_at:
            return int((self.finished_at - self.started_at).total_seconds() / 60)
        return None

    @property
    def total_sets(self):
        return sum(len([s for s in se.sets if s.completed]) for se in self.exercises)

    @property
    def total_volume(self):
        vol = 0
        for se in self.exercises:
            for s in se.sets:
                if s.completed and s.weight and s.reps:
                    vol += s.weight * s.reps
        return round(vol, 1)


class SessionExercise(db.Model):
    __tablename__ = 'session_exercises'
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('workout_sessions.id'), nullable=False)
    exercise_id = db.Column(db.Integer, db.ForeignKey('exercises.id'), nullable=False)
    order = db.Column(db.Integer, default=0)
    exercise = db.relationship('Exercise')
    sets = db.relationship('ExerciseSet', backref='session_exercise', lazy=True,
                            order_by='ExerciseSet.set_number',
                            cascade='all, delete-orphan')


class ExerciseSet(db.Model):
    __tablename__ = 'exercise_sets'
    id = db.Column(db.Integer, primary_key=True)
    session_exercise_id = db.Column(db.Integer, db.ForeignKey('session_exercises.id'), nullable=False)
    set_number = db.Column(db.Integer, nullable=False)
    weight = db.Column(db.Float)
    reps = db.Column(db.Integer)
    completed = db.Column(db.Boolean, default=False)

    def to_dict(self):
        return {'id': self.id, 'set_number': self.set_number,
                'weight': self.weight, 'reps': self.reps, 'completed': self.completed}

    @property
    def estimated_1rm(self):
        if self.weight and self.reps and self.reps > 0:
            return round(self.weight * (1 + self.reps / 30), 1)
        return None


class PersonalRecord(db.Model):
    __tablename__ = 'personal_records'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    exercise_id = db.Column(db.Integer, db.ForeignKey('exercises.id'), nullable=False)
    metric = db.Column(db.String(10), nullable=False)  # 'e1rm' or 'volume'
    value = db.Column(db.Float, nullable=False)
    session_id = db.Column(db.Integer, db.ForeignKey('workout_sessions.id'))
    achieved_at = db.Column(db.DateTime, default=datetime.utcnow)
    exercise = db.relationship('Exercise')


class Food(db.Model):
    __tablename__ = 'foods'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    category = db.Column(db.String(20), default='general')  # indian / general / custom
    serving_size = db.Column(db.String(50))
    serving_grams = db.Column(db.Float)  # weight of one serving_size unit, in grams -- enables gram-based logging
    calories = db.Column(db.Float, nullable=False, default=0)
    protein = db.Column(db.Float, default=0)
    carbs = db.Column(db.Float, default=0)
    fat = db.Column(db.Float, default=0)
    source = db.Column(db.String(20), default='curated')  # curated / custom / openfoodfacts
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))  # set only for user-created custom foods

    def to_dict(self):
        return {'id': self.id, 'name': self.name, 'category': self.category,
                'serving_size': self.serving_size, 'serving_grams': self.serving_grams,
                'calories': self.calories, 'protein': self.protein, 'carbs': self.carbs,
                'fat': self.fat, 'source': self.source}


class FoodLog(db.Model):
    __tablename__ = 'food_logs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    food_id = db.Column(db.Integer, db.ForeignKey('foods.id'))
    name = db.Column(db.String(150), nullable=False)
    servings = db.Column(db.Float, default=1.0)  # multiplier of the food's base serving -- used for macro math
    unit = db.Column(db.String(10), default='serving')  # 'serving' or 'grams' -- how the user entered the amount
    amount = db.Column(db.Float, default=1.0)  # raw quantity in `unit`, for display (e.g. "150" grams)
    meal_type = db.Column(db.String(20), default='snack')
    calories = db.Column(db.Float, nullable=False, default=0)
    protein = db.Column(db.Float, default=0)
    carbs = db.Column(db.Float, default=0)
    fat = db.Column(db.Float, default=0)
    logged_at = db.Column(db.DateTime, default=datetime.utcnow)
    food = db.relationship('Food')

    def to_dict(self):
        return {'id': self.id, 'food_id': self.food_id, 'name': self.name,
                'servings': self.servings, 'unit': self.unit, 'amount': self.amount,
                'meal_type': self.meal_type, 'calories': self.calories,
                'protein': self.protein, 'carbs': self.carbs, 'fat': self.fat}


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ─── Auth Routes ──────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user, remember=True)
            return redirect(url_for('dashboard'))
        flash('Invalid email or password.', 'error')
    return render_template('login.html')


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'error')
        elif User.query.filter_by(email=email).first():
            flash('Email already registered.', 'error')
        elif User.query.filter_by(username=username).first():
            flash('Username already taken.', 'error')
        else:
            user = User(username=username, email=email)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            return redirect(url_for('dashboard'))
    return render_template('signup.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# ─── Main Routes ──────────────────────────────────────────────────────────────

@app.route('/')
@login_required
def dashboard():
    recent = (WorkoutSession.query
              .filter_by(user_id=current_user.id, completed=True)
              .order_by(WorkoutSession.started_at.desc()).limit(3).all())
    active = (WorkoutSession.query
              .filter_by(user_id=current_user.id, completed=False)
              .order_by(WorkoutSession.started_at.desc()).first())
    total_workouts = WorkoutSession.query.filter_by(
        user_id=current_user.id, completed=True).count()
    plans = WorkoutPlan.query.filter_by(user_id=current_user.id).all()
    return render_template('dashboard.html', recent=recent, active=active,
                           total_workouts=total_workouts, plans=plans)


# ── Plans ────────────────────────────────────────────────────────────────────

@app.route('/plans')
@login_required
def plans():
    user_plans = (WorkoutPlan.query.filter_by(user_id=current_user.id)
                  .order_by(WorkoutPlan.created_at.desc()).all())
    return render_template('plans.html', plans=user_plans)


@app.route('/plans/new', methods=['GET', 'POST'])
@login_required
def new_plan():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Plan name is required.', 'error')
            return render_template('plan_form.html', plan=None)
        plan = WorkoutPlan(user_id=current_user.id, name=name,
                           description=request.form.get('description', '').strip())
        db.session.add(plan)
        db.session.commit()
        return redirect(url_for('plan_detail', plan_id=plan.id))
    return render_template('plan_form.html', plan=None)


@app.route('/plans/<int:plan_id>')
@login_required
def plan_detail(plan_id):
    plan = WorkoutPlan.query.filter_by(id=plan_id, user_id=current_user.id).first_or_404()
    groups = [r[0] for r in db.session.query(Exercise.muscle_group).distinct().all() if r[0]]
    return render_template('plan_detail.html', plan=plan, groups=sorted(groups))


@app.route('/plans/<int:plan_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_plan(plan_id):
    plan = WorkoutPlan.query.filter_by(id=plan_id, user_id=current_user.id).first_or_404()
    if request.method == 'POST':
        plan.name = request.form.get('name', '').strip() or plan.name
        plan.description = request.form.get('description', '').strip()
        db.session.commit()
        return redirect(url_for('plan_detail', plan_id=plan.id))
    return render_template('plan_form.html', plan=plan)


@app.route('/plans/<int:plan_id>/delete', methods=['POST'])
@login_required
def delete_plan(plan_id):
    plan = WorkoutPlan.query.filter_by(id=plan_id, user_id=current_user.id).first_or_404()
    db.session.delete(plan)
    db.session.commit()
    return redirect(url_for('plans'))


# ── Workout Session ───────────────────────────────────────────────────────────

@app.route('/workout/start', methods=['POST'])
@login_required
def start_workout():
    plan_id = request.form.get('plan_id', type=int)
    plan = None
    if plan_id:
        plan = WorkoutPlan.query.filter_by(id=plan_id, user_id=current_user.id).first()

    name = request.form.get('name', '').strip()
    if not name:
        name = plan.name if plan else f'Workout — {datetime.now().strftime("%b %d")}'

    session = WorkoutSession(user_id=current_user.id, plan_id=plan_id, name=name)
    db.session.add(session)
    db.session.flush()

    if plan:
        for pe in plan.exercises:
            se = SessionExercise(session_id=session.id, exercise_id=pe.exercise_id,
                                 order=pe.order)
            db.session.add(se)
            db.session.flush()
            prev = _get_prev_performance(pe.exercise_id, session.id, current_user.id)
            prev_sets = prev['sets'] if prev else []
            for i in range(1, pe.default_sets + 1):
                ps = prev_sets[i - 1] if i - 1 < len(prev_sets) else {}
                db.session.add(ExerciseSet(session_exercise_id=se.id, set_number=i,
                                           weight=ps.get('weight'),
                                           reps=ps.get('reps', pe.default_reps)))

    db.session.commit()
    return redirect(url_for('workout', session_id=session.id))


@app.route('/workout/<int:session_id>')
@login_required
def workout(session_id):
    w = WorkoutSession.query.filter_by(id=session_id, user_id=current_user.id).first_or_404()
    if w.completed:
        return redirect(url_for('summary', session_id=session_id))
    prev_perfs = {se.id: _get_prev_performance(se.exercise_id, session_id, current_user.id)
                  for se in w.exercises}
    return render_template('workout.html', workout=w, prev_perfs=prev_perfs)


@app.route('/workout/<int:session_id>/summary')
@login_required
def summary(session_id):
    w = WorkoutSession.query.filter_by(id=session_id, user_id=current_user.id).first_or_404()
    prs = PersonalRecord.query.filter_by(session_id=session_id).all()
    prs_by_exercise = {}
    for pr in prs:
        prs_by_exercise.setdefault(pr.exercise_id, []).append(pr.metric)
    return render_template('summary.html', workout=w, prs_by_exercise=prs_by_exercise)


@app.route('/history')
@login_required
def history():
    workouts = (WorkoutSession.query
                .filter_by(user_id=current_user.id, completed=True)
                .order_by(WorkoutSession.started_at.desc()).all())
    return render_template('history.html', workouts=workouts)


@app.route('/progress')
@login_required
def progress():
    exercises_done = (db.session.query(Exercise)
                      .join(SessionExercise)
                      .join(WorkoutSession)
                      .filter(WorkoutSession.user_id == current_user.id,
                              WorkoutSession.completed == True)
                      .distinct().order_by(Exercise.name).all())
    return render_template('progress.html', exercises=exercises_done)


# ── Food Log ──────────────────────────────────────────────────────────────────

@app.route('/food-log')
@login_required
def food_log():
    date_str = request.args.get('date', '').strip()
    try:
        day = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else datetime.now().date()
    except ValueError:
        day = datetime.now().date()

    day_start = datetime.combine(day, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    entries = (FoodLog.query.filter_by(user_id=current_user.id)
               .filter(FoodLog.logged_at >= day_start, FoodLog.logged_at < day_end)
               .order_by(FoodLog.logged_at).all())

    meals = {'breakfast': [], 'lunch': [], 'dinner': [], 'snack': []}
    for e in entries:
        meals.setdefault(e.meal_type, []).append(e)

    totals = {
        'calories': round(sum(e.calories for e in entries), 1),
        'protein': round(sum(e.protein for e in entries), 1),
        'carbs': round(sum(e.carbs for e in entries), 1),
        'fat': round(sum(e.fat for e in entries), 1),
    }

    return render_template('food_log.html', meals=meals, totals=totals, day=day,
                           prev_day=(day - timedelta(days=1)).isoformat(),
                           next_day=(day + timedelta(days=1)).isoformat(),
                           is_today=(day == datetime.now().date()))


# ─── JSON API ─────────────────────────────────────────────────────────────────

@app.route('/api/foods/search')
@login_required
def search_foods():
    q = request.args.get('q', '').strip()
    category = request.args.get('category', '').strip()
    if not q:
        return jsonify([])

    query = Food.query.filter(db.or_(Food.user_id == None, Food.user_id == current_user.id))
    query = query.filter(Food.name.ilike(f'%{q}%'))
    if category:
        query = query.filter_by(category=category)
    local = query.order_by(Food.name).limit(25).all()
    results = [f.to_dict() for f in local]

    if len(results) < 8 and len(q) >= 3 and category != 'custom':
        seen = {r['name'].lower() for r in results}
        for r in search_openfoodfacts(q, limit=10):
            if r['name'].lower() not in seen:
                results.append(r)
                seen.add(r['name'].lower())

    return jsonify(results)


@app.route('/api/food-log/add', methods=['POST'])
@login_required
def api_food_log_add():
    data = request.json or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Name is required'}), 400

    try:
        servings = float(data.get('servings') or 1)
    except (TypeError, ValueError):
        servings = 1.0
    if servings <= 0:
        return jsonify({'error': 'Amount must be greater than zero'}), 400

    food_id = data.get('food_id')
    food = Food.query.get(food_id) if food_id else None

    if food is None:
        source = data.get('source') or 'custom'
        owner_id = current_user.id if source == 'custom' else None
        food = Food.query.filter_by(name=name, source=source, user_id=owner_id).first()
        if food is None:
            food = Food(name=name,
                        category=data.get('category') or ('custom' if source == 'custom' else 'general'),
                        serving_size=data.get('serving_size') or '1 serving',
                        serving_grams=data.get('serving_grams'),
                        calories=float(data.get('calories') or 0),
                        protein=float(data.get('protein') or 0),
                        carbs=float(data.get('carbs') or 0),
                        fat=float(data.get('fat') or 0),
                        source=source, user_id=owner_id)
            db.session.add(food)
            db.session.flush()

    unit = data.get('unit') or 'serving'
    amount = data.get('amount', servings)

    entry = FoodLog(user_id=current_user.id, food_id=food.id, name=food.name,
                    servings=servings, unit=unit, amount=amount,
                    meal_type=data.get('meal_type') or 'snack',
                    calories=round(food.calories * servings, 1),
                    protein=round(food.protein * servings, 1),
                    carbs=round(food.carbs * servings, 1),
                    fat=round(food.fat * servings, 1))
    db.session.add(entry)
    db.session.commit()
    return jsonify(entry.to_dict())


@app.route('/api/food-log/<int:log_id>', methods=['PUT'])
@login_required
def api_food_log_update(log_id):
    entry = FoodLog.query.filter_by(id=log_id, user_id=current_user.id).first_or_404()
    data = request.json or {}
    if 'servings' in data:
        try:
            servings = float(data['servings'])
        except (TypeError, ValueError):
            servings = entry.servings
        if entry.food and servings > 0:
            entry.servings = servings
            entry.calories = round(entry.food.calories * servings, 1)
            entry.protein = round(entry.food.protein * servings, 1)
            entry.carbs = round(entry.food.carbs * servings, 1)
            entry.fat = round(entry.food.fat * servings, 1)
    if 'meal_type' in data:
        entry.meal_type = data['meal_type']
    db.session.commit()
    return jsonify(entry.to_dict())


@app.route('/api/food-log/<int:log_id>', methods=['DELETE'])
@login_required
def api_food_log_delete(log_id):
    entry = FoodLog.query.filter_by(id=log_id, user_id=current_user.id).first_or_404()
    db.session.delete(entry)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/exercises/search')
@login_required
def search_exercises():
    q = request.args.get('q', '').strip()
    muscle = request.args.get('muscle', '').strip()
    query = Exercise.query
    if q:
        query = query.filter(Exercise.name.ilike(f'%{q}%'))
    if muscle:
        query = query.filter_by(muscle_group=muscle)
    results = query.order_by(Exercise.name).limit(200).all()
    return jsonify([e.to_dict() for e in results])


@app.route('/api/muscle-groups')
@login_required
def muscle_groups():
    groups = [r[0] for r in db.session.query(Exercise.muscle_group).distinct().all() if r[0]]
    return jsonify(sorted(groups))


@app.route('/api/workout/<int:session_id>/add-exercise', methods=['POST'])
@login_required
def api_add_exercise(session_id):
    w = WorkoutSession.query.filter_by(id=session_id, user_id=current_user.id).first_or_404()
    exercise_id = request.json.get('exercise_id')
    ex = Exercise.query.get_or_404(exercise_id)

    se = SessionExercise(session_id=session_id, exercise_id=exercise_id, order=len(w.exercises))
    db.session.add(se)
    db.session.flush()

    prev = _get_prev_performance(exercise_id, session_id, current_user.id)
    prev_sets = prev['sets'] if prev else []

    for i in range(1, 4):
        ps = prev_sets[i - 1] if i - 1 < len(prev_sets) else {}
        db.session.add(ExerciseSet(session_exercise_id=se.id, set_number=i,
                                   weight=ps.get('weight'), reps=ps.get('reps')))
    db.session.commit()

    return jsonify({'session_exercise_id': se.id, 'exercise': ex.to_dict(),
                    'sets': [s.to_dict() for s in se.sets], 'previous': prev})


@app.route('/api/session-exercise/<int:se_id>/add-set', methods=['POST'])
@login_required
def api_add_set(se_id):
    se = SessionExercise.query.get_or_404(se_id)
    last = se.sets[-1] if se.sets else None
    new_set = ExerciseSet(session_exercise_id=se_id, set_number=len(se.sets) + 1,
                          weight=last.weight if last else None,
                          reps=last.reps if last else None)
    db.session.add(new_set)
    db.session.commit()
    return jsonify(new_set.to_dict())


@app.route('/api/set/<int:set_id>', methods=['PUT'])
@login_required
def api_update_set(set_id):
    s = ExerciseSet.query.get_or_404(set_id)
    data = request.json
    if 'weight' in data:
        s.weight = data['weight']
    if 'reps' in data:
        s.reps = data['reps']
    if 'completed' in data:
        s.completed = data['completed']
    db.session.commit()
    return jsonify(s.to_dict())


@app.route('/api/set/<int:set_id>', methods=['DELETE'])
@login_required
def api_delete_set(set_id):
    s = ExerciseSet.query.get_or_404(set_id)
    db.session.delete(s)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/session-exercise/<int:se_id>', methods=['DELETE'])
@login_required
def api_delete_session_exercise(se_id):
    se = SessionExercise.query.get_or_404(se_id)
    db.session.delete(se)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/workout/<int:session_id>/finish', methods=['POST'])
@login_required
def api_finish_workout(session_id):
    w = WorkoutSession.query.filter_by(id=session_id, user_id=current_user.id).first_or_404()
    w.completed = True
    w.finished_at = datetime.utcnow()

    new_prs = _detect_and_record_prs(w)
    db.session.commit()
    return jsonify({
        'ok': True, 'redirect': url_for('summary', session_id=session_id),
        'new_prs': [{'exercise': pr.exercise.name, 'metric': pr.metric, 'value': pr.value} for pr in new_prs],
    })


def _detect_and_record_prs(session):
    """For each exercise trained in `session`, compare this session's best
    e1RM/volume against every PRIOR completed session for that user+exercise.
    Only counts as a PR if there's prior history to beat (a first-ever lift
    isn't a "record"). Persists a PersonalRecord row per improved metric."""
    new_prs = []
    for se in session.exercises:
        done = [s for s in se.sets if s.completed and s.weight and s.reps]
        if not done:
            continue
        session_e1rm = max((s.estimated_1rm or 0) for s in done)
        session_volume = sum(s.weight * s.reps for s in done)

        prior_sets = (db.session.query(ExerciseSet)
                      .join(SessionExercise, ExerciseSet.session_exercise_id == SessionExercise.id)
                      .join(WorkoutSession, SessionExercise.session_id == WorkoutSession.id)
                      .filter(WorkoutSession.user_id == session.user_id,
                              WorkoutSession.completed == True,
                              WorkoutSession.id != session.id,
                              SessionExercise.exercise_id == se.exercise_id,
                              ExerciseSet.completed == True,
                              ExerciseSet.weight.isnot(None), ExerciseSet.reps.isnot(None))
                      .all())
        if not prior_sets:
            continue  # no history yet -- not a "record"

        prior_best_e1rm = max((s.estimated_1rm or 0) for s in prior_sets)
        if session_e1rm > prior_best_e1rm:
            pr = PersonalRecord(user_id=session.user_id, exercise_id=se.exercise_id,
                                metric='e1rm', value=session_e1rm, session_id=session.id)
            db.session.add(pr)
            new_prs.append(pr)

        prior_volumes_by_session = {}
        for s in prior_sets:
            prior_volumes_by_session[s.session_exercise_id] = (
                prior_volumes_by_session.get(s.session_exercise_id, 0) + s.weight * s.reps)
        prior_best_volume = max(prior_volumes_by_session.values()) if prior_volumes_by_session else 0
        if session_volume > prior_best_volume:
            pr = PersonalRecord(user_id=session.user_id, exercise_id=se.exercise_id,
                                metric='volume', value=session_volume, session_id=session.id)
            db.session.add(pr)
            new_prs.append(pr)
    return new_prs


@app.route('/api/plan/<int:plan_id>/add-exercise', methods=['POST'])
@login_required
def api_plan_add_exercise(plan_id):
    plan = WorkoutPlan.query.filter_by(id=plan_id, user_id=current_user.id).first_or_404()
    data = request.json
    exercise_id = data.get('exercise_id')
    ex = Exercise.query.get_or_404(exercise_id)

    existing = PlanExercise.query.filter_by(plan_id=plan_id, exercise_id=exercise_id).first()
    if existing:
        return jsonify({'error': 'Exercise already in plan'}), 409

    pe = PlanExercise(plan_id=plan_id, exercise_id=exercise_id,
                      default_sets=data.get('sets', 3), default_reps=data.get('reps', 8),
                      order=len(plan.exercises))
    db.session.add(pe)
    db.session.commit()
    return jsonify(pe.to_dict())


@app.route('/api/plan-exercise/<int:pe_id>', methods=['DELETE'])
@login_required
def api_plan_delete_exercise(pe_id):
    pe = PlanExercise.query.get_or_404(pe_id)
    plan = WorkoutPlan.query.filter_by(id=pe.plan_id, user_id=current_user.id).first_or_404()
    db.session.delete(pe)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/exercise/<int:exercise_id>/progress')
@login_required
def api_exercise_progress(exercise_id):
    sessions = _exercise_session_series(exercise_id, current_user.id)
    e1rm_values = [s['e1rm'] for s in sessions]
    volume_values = [s.get('volume', 0) for s in sessions]
    return jsonify({
        'labels': [s['date'] for s in sessions],
        'e1rm': e1rm_values,
        'volume': volume_values,
        **progress_analytics.analyze_series(e1rm_values, volume_values),
    })


@app.route('/api/progress/overview')
@login_required
def api_progress_overview():
    exercises_done = (db.session.query(Exercise)
                      .join(SessionExercise)
                      .join(WorkoutSession)
                      .filter(WorkoutSession.user_id == current_user.id,
                              WorkoutSession.completed == True)
                      .distinct().all())

    overview = []
    for ex in exercises_done:
        sessions = _exercise_session_series(ex.id, current_user.id)
        if not sessions:
            continue
        e1rm_values = [s['e1rm'] for s in sessions]
        analysis = progress_analytics.analyze_series(e1rm_values, [s.get('volume', 0) for s in sessions])
        days_since = (datetime.utcnow() - sessions[-1]['started_at']).days
        needs_attention = (analysis['e1rm_trend']['direction'] == 'falling'
                          or analysis['stalled'] or days_since > 14)
        overview.append({
            'exercise_id': ex.id, 'name': ex.name, 'muscle_group': ex.muscle_group,
            'e1rm_trend': analysis['e1rm_trend'], 'stalled': analysis['stalled'],
            'days_since_last': days_since, 'needs_attention': needs_attention,
        })

    # Worst-first: falling > stalled > stale > everything else
    def sort_key(o):
        return (0 if o['e1rm_trend']['direction'] == 'falling' else
                1 if o['stalled'] else
                2 if o['days_since_last'] > 14 else 3, -o['days_since_last'])
    overview.sort(key=sort_key)
    return jsonify(overview)


# ─── AI Recommendations ───────────────────────────────────────────────────────

@app.route('/api/ai/recommend', methods=['POST'])
@login_required
def ai_recommend():
    exercise_id = request.json.get('exercise_id')
    ex = Exercise.query.get_or_404(exercise_id)

    recent = (SessionExercise.query
              .join(WorkoutSession)
              .filter(WorkoutSession.user_id == current_user.id,
                      SessionExercise.exercise_id == exercise_id,
                      WorkoutSession.completed == True)
              .order_by(WorkoutSession.started_at.desc())
              .limit(5).all())

    if not recent:
        return jsonify({'recommendation':
                        'No history yet for this exercise. Log a few sessions and come back for personalised recommendations!'})

    history_lines = []
    for se in reversed(recent):
        date_str = se.session.started_at.strftime('%b %d')
        done = [s for s in se.sets if s.completed and s.weight and s.reps]
        if done:
            sets_str = ', '.join(f'{s.weight}kg×{s.reps}' for s in done)
            history_lines.append(f'- {date_str}: {sets_str}')

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return jsonify({'recommendation':
                        'Add your ANTHROPIC_API_KEY environment variable to enable AI recommendations.'})

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model='claude-haiku-4-5',
            max_tokens=300,
            system=[{
                'type': 'text',
                'text': 'You are a concise personal trainer focused on progressive overload. Give specific, actionable advice in 2-4 sentences.',
                'cache_control': {'type': 'ephemeral'}
            }],
            messages=[{
                'role': 'user',
                'content': (
                    f'Exercise: {ex.name} ({ex.muscle_group})\n'
                    f'Recent sessions (oldest→newest):\n'
                    + '\n'.join(history_lines) +
                    '\n\nRecommend exact weight, sets, and reps for the next session, with a one-sentence reason.'
                )
            }]
        )
        return jsonify({'recommendation': msg.content[0].text})
    except Exception as e:
        return jsonify({'recommendation': f'AI error: {str(e)}'}), 500


# ─── Helpers ──────────────────────────────────────────────────────────────────

def search_openfoodfacts(query, limit=10):
    """Live fallback lookup against the free Open Food Facts API for foods
    not in our curated/custom database (e.g. branded/packaged products)."""
    try:
        resp = requests.get('https://world.openfoodfacts.org/cgi/search.pl', params={
            'search_terms': query, 'search_simple': 1, 'action': 'process',
            'json': 1, 'page_size': limit,
        }, timeout=4, headers={'User-Agent': 'Liftr-WorkoutTracker/1.0'})
        resp.raise_for_status()
        products = resp.json().get('products', [])
    except (requests.RequestException, ValueError):
        return []

    results = []
    for p in products:
        name = (p.get('product_name') or p.get('generic_name') or '').strip()
        nutriments = p.get('nutriments', {})
        calories = nutriments.get('energy-kcal_100g')
        if not name or calories is None:
            continue
        brand = (p.get('brands') or '').split(',')[0].strip()
        results.append({
            'name': f'{name} ({brand})' if brand else name,
            'category': 'general', 'serving_size': '100 g', 'serving_grams': 100,
            'calories': round(calories, 1),
            'protein': round(nutriments.get('proteins_100g') or 0, 1),
            'carbs': round(nutriments.get('carbohydrates_100g') or 0, 1),
            'fat': round(nutriments.get('fat_100g') or 0, 1),
            'source': 'openfoodfacts',
        })
    return results


def _exercise_session_series(exercise_id, user_id):
    """One row per completed session for this exercise, oldest->newest:
    best e1RM, total volume, and the session date (used for both the
    per-exercise progress chart and the cross-exercise overview)."""
    rows = (db.session.query(WorkoutSession.started_at, ExerciseSet)
            .join(SessionExercise, WorkoutSession.id == SessionExercise.session_id)
            .join(ExerciseSet, SessionExercise.id == ExerciseSet.session_exercise_id)
            .filter(WorkoutSession.user_id == user_id,
                    WorkoutSession.completed == True,
                    SessionExercise.exercise_id == exercise_id,
                    ExerciseSet.completed == True,
                    ExerciseSet.weight.isnot(None),
                    ExerciseSet.reps.isnot(None))
            .order_by(WorkoutSession.started_at.asc())
            .all())

    by_session = {}
    for date, s in rows:
        key = date.strftime('%Y-%m-%d')
        e1rm = s.estimated_1rm or 0
        vol = (s.weight or 0) * (s.reps or 0)
        if key not in by_session:
            by_session[key] = {'date': date.strftime('%b %d'), 'started_at': date, 'e1rm': e1rm, 'volume': 0}
        elif e1rm > by_session[key]['e1rm']:
            by_session[key]['e1rm'] = e1rm
        by_session[key]['volume'] += vol

    return list(by_session.values())


def _get_prev_performance(exercise_id, current_session_id, user_id):
    prev = (SessionExercise.query
            .join(WorkoutSession)
            .filter(WorkoutSession.user_id == user_id,
                    SessionExercise.exercise_id == exercise_id,
                    WorkoutSession.completed == True,
                    WorkoutSession.id != current_session_id)
            .order_by(WorkoutSession.started_at.desc())
            .first())
    if prev:
        done = [s for s in prev.sets if s.completed]
        if done:
            return {'sets': [{'weight': s.weight, 'reps': s.reps} for s in done]}
    return None


# ─── Seed Data ────────────────────────────────────────────────────────────────
# Canonical exercise catalogue. muscle_group uses a finer split (Quadriceps /
# Hamstrings / Glutes / Calves instead of a single "Legs" bucket) than the
# original MVP seed. seed() upserts by name, so existing rows get reclassified
# in place instead of being duplicated.

EXERCISES = [
    # Chest
    ('Bench Press', 'Chest', 'strength'), ('Incline Bench Press', 'Chest', 'strength'),
    ('Decline Bench Press', 'Chest', 'strength'), ('Dumbbell Flyes', 'Chest', 'strength'),
    ('Cable Crossover', 'Chest', 'strength'), ('Push-ups', 'Chest', 'bodyweight'),
    ('Dips', 'Chest', 'bodyweight'), ('Incline Dumbbell Press', 'Chest', 'strength'),
    ('Dumbbell Bench Press', 'Chest', 'strength'), ('Decline Dumbbell Bench Press', 'Chest', 'strength'),
    ('Incline Dumbbell Flyes', 'Chest', 'strength'), ('Low-to-High Cable Fly', 'Chest', 'strength'),
    ('High-to-Low Cable Fly', 'Chest', 'strength'), ('Machine Chest Press', 'Chest', 'strength'),
    ('Pec Deck Machine', 'Chest', 'strength'), ('Wide-Grip Push-ups', 'Chest', 'bodyweight'),
    ('Decline Push-ups', 'Chest', 'bodyweight'), ('Incline Push-ups', 'Chest', 'bodyweight'),
    ('Smith Machine Bench Press', 'Chest', 'strength'), ('Landmine Press', 'Chest', 'strength'),
    ('Svend Press', 'Chest', 'strength'), ('Floor Press', 'Chest', 'strength'),
    ('Guillotine Press', 'Chest', 'strength'), ('Resistance Band Chest Press', 'Chest', 'strength'),
    ('Plyo Push-up', 'Chest', 'plyometric'), ('Archer Push-up', 'Chest', 'bodyweight'),
    ('Hex Press', 'Chest', 'strength'),

    # Back
    ('Deadlift', 'Back', 'strength'), ('Barbell Row', 'Back', 'strength'),
    ('Pull-ups', 'Back', 'bodyweight'), ('Chin-ups', 'Back', 'bodyweight'),
    ('Lat Pulldown', 'Back', 'strength'), ('Seated Cable Row', 'Back', 'strength'),
    ('T-Bar Row', 'Back', 'strength'), ('Single-Arm Dumbbell Row', 'Back', 'strength'),
    ('Pendlay Row', 'Back', 'strength'), ('Chest-Supported Row', 'Back', 'strength'),
    ('Lat Pulldown Close Grip', 'Back', 'strength'), ('Lat Pulldown Reverse Grip', 'Back', 'strength'),
    ('Neutral-Grip Pull-ups', 'Back', 'bodyweight'), ('Weighted Pull-ups', 'Back', 'bodyweight'),
    ('Assisted Pull-up Machine', 'Back', 'strength'), ('Straight-Arm Pulldown', 'Back', 'strength'),
    ('Rack Pulls', 'Back', 'strength'), ('Meadows Row', 'Back', 'strength'),
    ('Renegade Row', 'Back', 'strength'), ('Inverted Row', 'Back', 'bodyweight'),
    ('Kroc Row', 'Back', 'strength'), ('Machine Row', 'Back', 'strength'),
    ('Cable Pullover', 'Back', 'strength'), ('Dumbbell Pullover', 'Back', 'strength'),
    ('Superman', 'Back', 'bodyweight'), ('Back Extension', 'Back', 'bodyweight'),
    ('Reverse Hyperextension', 'Back', 'strength'), ('Deficit Deadlift', 'Back', 'strength'),
    ('Trap Bar Deadlift', 'Back', 'strength'),

    # Shoulders
    ('Overhead Press', 'Shoulders', 'strength'), ('Dumbbell Shoulder Press', 'Shoulders', 'strength'),
    ('Lateral Raises', 'Shoulders', 'strength'), ('Front Raises', 'Shoulders', 'strength'),
    ('Arnold Press', 'Shoulders', 'strength'), ('Rear Delt Flyes', 'Shoulders', 'strength'),
    ('Face Pulls', 'Shoulders', 'strength'),
    ('Seated Barbell Shoulder Press', 'Shoulders', 'strength'), ('Push Press', 'Shoulders', 'strength'),
    ('Behind-the-Neck Press', 'Shoulders', 'strength'), ('Cable Lateral Raise', 'Shoulders', 'strength'),
    ('Machine Lateral Raise', 'Shoulders', 'strength'), ('Plate Front Raise', 'Shoulders', 'strength'),
    ('Rear Delt Fly Cable', 'Shoulders', 'strength'), ('Reverse Pec Deck', 'Shoulders', 'strength'),
    ('Upright Row', 'Shoulders', 'strength'), ('Landmine Lateral Raise', 'Shoulders', 'strength'),
    ('Cuban Press', 'Shoulders', 'strength'), ('Bradford Press', 'Shoulders', 'strength'),
    ('Handstand Push-up', 'Shoulders', 'bodyweight'), ('Pike Push-up', 'Shoulders', 'bodyweight'),
    ('Bus Driver', 'Shoulders', 'strength'), ('Y-Raise', 'Shoulders', 'strength'),
    ('Egyptian Lateral Raise', 'Shoulders', 'strength'),

    # Biceps
    ('Barbell Curl', 'Biceps', 'strength'), ('Dumbbell Curl', 'Biceps', 'strength'),
    ('Hammer Curl', 'Biceps', 'strength'), ('Preacher Curl', 'Biceps', 'strength'),
    ('Cable Curl', 'Biceps', 'strength'), ('Concentration Curl', 'Biceps', 'strength'),
    ('EZ-Bar Curl', 'Biceps', 'strength'), ('Cable Rope Hammer Curl', 'Biceps', 'strength'),
    ('Incline Dumbbell Curl', 'Biceps', 'strength'), ('Spider Curl', 'Biceps', 'strength'),
    ('Zottman Curl', 'Biceps', 'strength'), ('Drag Curl', 'Biceps', 'strength'),
    ('21s Curl', 'Biceps', 'strength'), ('Reverse Curl', 'Biceps', 'strength'),
    ('Machine Bicep Curl', 'Biceps', 'strength'), ('Cross-Body Hammer Curl', 'Biceps', 'strength'),
    ('Bayesian Cable Curl', 'Biceps', 'strength'),

    # Triceps
    ('Skull Crushers', 'Triceps', 'strength'), ('Tricep Pushdown', 'Triceps', 'strength'),
    ('Overhead Tricep Extension', 'Triceps', 'strength'),
    ('Close-Grip Bench Press', 'Triceps', 'strength'), ('Diamond Push-ups', 'Triceps', 'bodyweight'),
    ('Tricep Rope Pushdown', 'Triceps', 'strength'), ('Tricep V-Bar Pushdown', 'Triceps', 'strength'),
    ('Overhead Tricep Extension Cable', 'Triceps', 'strength'), ('Tricep Dips', 'Triceps', 'bodyweight'),
    ('Bench Dips', 'Triceps', 'bodyweight'), ('Tricep Kickback', 'Triceps', 'strength'),
    ('JM Press', 'Triceps', 'strength'), ('Tate Press', 'Triceps', 'strength'),
    ('Machine Tricep Extension', 'Triceps', 'strength'),

    # Forearms
    ('Wrist Curl', 'Forearms', 'strength'), ('Reverse Wrist Curl', 'Forearms', 'strength'),
    ("Farmer's Carry", 'Forearms', 'strength'), ('Plate Pinch', 'Forearms', 'strength'),
    ('Dead Hang', 'Forearms', 'bodyweight'), ('Wrist Roller', 'Forearms', 'strength'),

    # Quadriceps
    ('Squat', 'Quadriceps', 'strength'), ('Front Squat', 'Quadriceps', 'strength'),
    ('Leg Press', 'Quadriceps', 'strength'), ('Leg Extension', 'Quadriceps', 'strength'),
    ('Lunges', 'Quadriceps', 'bodyweight'), ('Bulgarian Split Squat', 'Quadriceps', 'strength'),
    ('Goblet Squat', 'Quadriceps', 'strength'), ('Hack Squat', 'Quadriceps', 'strength'),
    ('Walking Lunges', 'Quadriceps', 'bodyweight'), ('Reverse Lunges', 'Quadriceps', 'bodyweight'),
    ('Step-Ups', 'Quadriceps', 'bodyweight'), ('Sissy Squat', 'Quadriceps', 'bodyweight'),
    ('Zercher Squat', 'Quadriceps', 'strength'), ('Overhead Squat', 'Quadriceps', 'strength'),
    ('Smith Machine Squat', 'Quadriceps', 'strength'), ('Box Squat', 'Quadriceps', 'strength'),
    ('Pistol Squat', 'Quadriceps', 'bodyweight'), ('Wall Sit', 'Quadriceps', 'bodyweight'),
    ('Jump Squat', 'Quadriceps', 'plyometric'),

    # Hamstrings
    ('Romanian Deadlift', 'Hamstrings', 'strength'), ('Leg Curl', 'Hamstrings', 'strength'),
    ('Stiff-Leg Deadlift', 'Hamstrings', 'strength'), ('Seated Leg Curl', 'Hamstrings', 'strength'),
    ('Nordic Hamstring Curl', 'Hamstrings', 'bodyweight'), ('Good Mornings', 'Hamstrings', 'strength'),
    ('Glute Ham Raise', 'Hamstrings', 'bodyweight'), ('Single-Leg RDL', 'Hamstrings', 'strength'),
    ('Cable Pull-Through', 'Hamstrings', 'strength'),

    # Glutes
    ('Hip Thrust', 'Glutes', 'strength'), ('Sumo Deadlift', 'Glutes', 'strength'),
    ('Barbell Glute Bridge', 'Glutes', 'strength'), ('Cable Kickback', 'Glutes', 'strength'),
    ('Frog Pumps', 'Glutes', 'bodyweight'), ('Curtsy Lunge', 'Glutes', 'bodyweight'),
    ('Clamshells', 'Glutes', 'bodyweight'), ('Fire Hydrant', 'Glutes', 'bodyweight'),
    ('Donkey Kicks', 'Glutes', 'bodyweight'), ('Single-Leg Hip Thrust', 'Glutes', 'bodyweight'),
    ('Banded Lateral Walk', 'Glutes', 'bodyweight'),

    # Calves
    ('Calf Raises', 'Calves', 'strength'), ('Seated Calf Raise', 'Calves', 'strength'),
    ('Leg Press Calf Raise', 'Calves', 'strength'), ('Donkey Calf Raise', 'Calves', 'strength'),
    ('Single-Leg Calf Raise', 'Calves', 'bodyweight'), ('Smith Machine Calf Raise', 'Calves', 'strength'),

    # Core
    ('Plank', 'Core', 'bodyweight'), ('Crunches', 'Core', 'bodyweight'),
    ('Russian Twists', 'Core', 'bodyweight'), ('Leg Raises', 'Core', 'bodyweight'),
    ('Cable Crunch', 'Core', 'strength'), ('Ab Wheel Rollout', 'Core', 'bodyweight'),
    ('Hanging Leg Raises', 'Core', 'bodyweight'),
    ('Side Plank', 'Core', 'bodyweight'), ('Bicycle Crunches', 'Core', 'bodyweight'),
    ('Reverse Crunch', 'Core', 'bodyweight'), ('Hanging Knee Raises', 'Core', 'bodyweight'),
    ('V-Ups', 'Core', 'bodyweight'), ('Mountain Climbers', 'Core', 'bodyweight'),
    ('Flutter Kicks', 'Core', 'bodyweight'), ('Toe Touches', 'Core', 'bodyweight'),
    ('Dragon Flag', 'Core', 'bodyweight'), ('Dead Bug', 'Core', 'bodyweight'),
    ('Cable Woodchopper', 'Core', 'strength'), ('Pallof Press', 'Core', 'strength'),
    ('Sit-ups', 'Core', 'bodyweight'), ('Weighted Sit-ups', 'Core', 'strength'),
    ('Decline Sit-ups', 'Core', 'bodyweight'), ('Machine Ab Crunch', 'Core', 'strength'),
    ('L-Sit', 'Core', 'bodyweight'), ('Hollow Body Hold', 'Core', 'bodyweight'),

    # Traps
    ('Barbell Shrug', 'Traps', 'strength'), ('Dumbbell Shrug', 'Traps', 'strength'),
    ('Cable Shrug', 'Traps', 'strength'), ('Behind-the-Back Shrug', 'Traps', 'strength'),
    ('Snatch Grip Shrug', 'Traps', 'strength'), ("Farmer's Walk", 'Traps', 'strength'),

    # Neck
    ('Neck Curl', 'Neck', 'strength'), ('Neck Extension', 'Neck', 'strength'),
    ('Lateral Neck Flexion', 'Neck', 'strength'), ('Harness Neck Extension', 'Neck', 'strength'),

    # Full Body / Olympic / Conditioning
    ('Clean and Jerk', 'Full Body', 'olympic'), ('Power Clean', 'Full Body', 'olympic'),
    ('Hang Clean', 'Full Body', 'olympic'), ('Snatch', 'Full Body', 'olympic'),
    ('Power Snatch', 'Full Body', 'olympic'), ('Clean and Press', 'Full Body', 'olympic'),
    ('Thruster', 'Full Body', 'olympic'), ('Turkish Get-Up', 'Full Body', 'olympic'),
    ('Kettlebell Swing', 'Full Body', 'olympic'), ('Man Maker', 'Full Body', 'olympic'),
    ('Burpees', 'Full Body', 'bodyweight'), ('Bear Crawl', 'Full Body', 'bodyweight'),
    ('Sled Push', 'Full Body', 'strength'), ('Sled Pull', 'Full Body', 'strength'),
    ('Battle Ropes', 'Full Body', 'cardio'), ('Tire Flip', 'Full Body', 'strength'),
    ('Box Jump', 'Full Body', 'plyometric'), ('Broad Jump', 'Full Body', 'plyometric'),
    ('Depth Jump', 'Full Body', 'plyometric'), ('Clap Push-up', 'Chest', 'plyometric'),
    ('Skater Jump', 'Full Body', 'plyometric'), ('Tuck Jump', 'Full Body', 'plyometric'),

    # Cardio
    ('Treadmill Running', 'Cardio', 'cardio'), ('Stationary Bike', 'Cardio', 'cardio'),
    ('Rowing Machine', 'Cardio', 'cardio'), ('Elliptical', 'Cardio', 'cardio'),
    ('Jump Rope', 'Cardio', 'cardio'), ('Stair Climber', 'Cardio', 'cardio'),
    ('Sprint Intervals', 'Cardio', 'cardio'), ('Incline Walking', 'Cardio', 'cardio'),
    ('Swimming', 'Cardio', 'cardio'), ('Assault Bike', 'Cardio', 'cardio'),

    # Mobility / Stretching
    ('Cat-Cow Stretch', 'Mobility', 'stretching'), ("Child's Pose", 'Mobility', 'stretching'),
    ('Hamstring Stretch', 'Mobility', 'stretching'), ('Quad Stretch', 'Mobility', 'stretching'),
    ('Hip Flexor Stretch', 'Mobility', 'stretching'), ('Band Shoulder Dislocate', 'Mobility', 'stretching'),
    ("World's Greatest Stretch", 'Mobility', 'stretching'), ('Pigeon Pose', 'Mobility', 'stretching'),
    ('Cobra Stretch', 'Mobility', 'stretching'), ('Downward Dog', 'Mobility', 'stretching'),
    ('90/90 Hip Stretch', 'Mobility', 'stretching'), ('Thoracic Spine Rotation', 'Mobility', 'stretching'),
    ('Foam Rolling', 'Mobility', 'stretching'),
]


def seed():
    """Upsert the canonical catalogue by name: adds new exercises and
    reclassifies existing rows (e.g. old 'Legs' entries) without duplicating
    or touching exercises already logged in past sessions/plans."""
    existing = {e.name: e for e in Exercise.query.all()}
    for name, muscle, cat in EXERCISES:
        ex = existing.get(name)
        if ex is None:
            db.session.add(Exercise(name=name, muscle_group=muscle, category=cat))
        elif ex.muscle_group != muscle or ex.category != cat:
            ex.muscle_group = muscle
            ex.category = cat
    db.session.commit()


# ─── free-exercise-db import (yuhonas/free-exercise-db, Unlicense/public domain) ──
# Adds ~870 more exercises with equipment/difficulty/instructions metadata on
# top of the curated EXERCISES catalogue above. Upserts by name so it never
# duplicates or overwrites an exercise already logged in a session/plan.

FREE_DB_MUSCLE_MAP = {
    'abdominals': 'Core', 'hamstrings': 'Hamstrings', 'adductors': 'Adductors',
    'quadriceps': 'Quadriceps', 'biceps': 'Biceps', 'shoulders': 'Shoulders',
    'chest': 'Chest', 'middle back': 'Back', 'calves': 'Calves', 'glutes': 'Glutes',
    'lower back': 'Back', 'lats': 'Back', 'triceps': 'Triceps', 'traps': 'Traps',
    'forearms': 'Forearms', 'neck': 'Neck', 'abductors': 'Abductors',
}

FREE_DB_CATEGORY_MAP = {
    'stretching': 'stretching', 'plyometrics': 'plyometric', 'strongman': 'strongman',
    'powerlifting': 'powerlifting', 'cardio': 'cardio', 'olympic weightlifting': 'olympic',
}


def _free_db_muscle(raw):
    return FREE_DB_MUSCLE_MAP.get(raw, raw.title()) if raw else 'Full Body'


def _free_db_category(raw, equipment):
    if raw == 'strength':
        return 'bodyweight' if equipment == 'body only' else 'strength'
    return FREE_DB_CATEGORY_MAP.get(raw, 'strength')


def import_free_exercise_db():
    path = os.path.join(os.path.dirname(__file__), 'data', 'free_exercise_db.json')
    if not os.path.exists(path):
        return
    with open(path, encoding='utf-8') as f:
        entries = json.load(f)

    existing = {e.name: e for e in Exercise.query.all()}
    for entry in entries:
        name = entry['name']
        primary = entry.get('primaryMuscles') or []
        muscle_group = _free_db_muscle(primary[0]) if primary else 'Full Body'
        category = _free_db_category(entry.get('category'), entry.get('equipment'))
        equipment = entry.get('equipment')
        equipment = equipment.title() if equipment else None
        secondary_names = primary[1:] + (entry.get('secondaryMuscles') or [])
        secondary = ', '.join(dict.fromkeys(_free_db_muscle(m) for m in secondary_names))
        instructions = '\n'.join(entry.get('instructions') or [])

        ex = existing.get(name)
        if ex is None:
            ex = Exercise(name=name, muscle_group=muscle_group, category=category)
            db.session.add(ex)
            existing[name] = ex
        # Only fill blanks -- never clobber the hand-curated catalogue's data.
        if not ex.equipment:
            ex.equipment = equipment
        if not ex.level:
            ex.level = entry.get('level')
        if not ex.mechanic:
            ex.mechanic = entry.get('mechanic')
        if not ex.force:
            ex.force = entry.get('force')
        if not ex.secondary_muscles:
            ex.secondary_muscles = secondary or None
        if not ex.instructions:
            ex.instructions = instructions or None
    db.session.commit()


# ─── Food DB import (curated Indian + general foods) ─────────────────────────

def import_food_db():
    """Upsert the curated food catalogue by (name, category): adds new foods,
    updates nutrition values for existing curated rows, never touches custom
    or cached openfoodfacts foods."""
    path = os.path.join(os.path.dirname(__file__), 'data', 'food_db.json')
    if not os.path.exists(path):
        return
    with open(path, encoding='utf-8') as f:
        entries = json.load(f)

    existing = {f.name: f for f in Food.query.filter_by(source='curated').all()}
    for entry in entries:
        food = existing.get(entry['name'])
        if food is None:
            food = Food(name=entry['name'], source='curated')
            db.session.add(food)
            existing[entry['name']] = food
        food.category = entry.get('category', 'general')
        food.serving_size = entry.get('serving_size')
        food.serving_grams = entry.get('serving_grams')
        food.calories = entry.get('calories', 0)
        food.protein = entry.get('protein', 0)
        food.carbs = entry.get('carbs', 0)
        food.fat = entry.get('fat', 0)
    db.session.commit()


def migrate_schema():
    """Add columns introduced after the initial deploy (SQLite/Postgres both
    support plain ADD COLUMN); no-op if the table was just created fresh."""
    from sqlalchemy import inspect, text
    inspector = inspect(db.engine)
    new_cols_by_table = {
        'exercises': {
            'equipment': 'VARCHAR(50)', 'level': 'VARCHAR(20)',
            'mechanic': 'VARCHAR(20)', 'force': 'VARCHAR(20)',
            'secondary_muscles': 'TEXT', 'instructions': 'TEXT',
        },
        'foods': {'serving_grams': 'FLOAT'},
        'food_logs': {'unit': 'VARCHAR(10)', 'amount': 'FLOAT'},
    }
    for table, new_cols in new_cols_by_table.items():
        existing_cols = {c['name'] for c in inspector.get_columns(table)}
        for col, coltype in new_cols.items():
            if col not in existing_cols:
                db.session.execute(text(f'ALTER TABLE {table} ADD COLUMN {col} {coltype}'))
    db.session.commit()


try:
    with app.app_context():
        db.create_all()
        migrate_schema()
        seed()
        import_free_exercise_db()
        import_food_db()
except Exception as e:
    print(f'DB init skipped: {e}')

if __name__ == '__main__':
    app.run(debug=True, port=5000)
