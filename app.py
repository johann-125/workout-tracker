import os
import json
from datetime import datetime
from flask import (Flask, render_template, request, redirect, url_for,
                   jsonify, flash, session as flask_session)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                         login_required, current_user)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-me')

_db_url = os.environ.get('DATABASE_URL', 'sqlite:///workout.db')
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
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
    name = db.Column(db.String(100), nullable=False)
    muscle_group = db.Column(db.String(50))
    category = db.Column(db.String(50), default='strength')

    def to_dict(self):
        return {'id': self.id, 'name': self.name,
                'muscle_group': self.muscle_group, 'category': self.category}


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
    return render_template('summary.html', workout=w)


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


# ─── JSON API ─────────────────────────────────────────────────────────────────

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
    results = query.order_by(Exercise.name).limit(30).all()
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
    db.session.commit()
    return jsonify({'ok': True, 'redirect': url_for('summary', session_id=session_id)})


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
    rows = (db.session.query(WorkoutSession.started_at, ExerciseSet)
            .join(SessionExercise, WorkoutSession.id == SessionExercise.session_id)
            .join(ExerciseSet, SessionExercise.id == ExerciseSet.session_exercise_id)
            .filter(WorkoutSession.user_id == current_user.id,
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
        if key not in by_session or e1rm > by_session[key]['e1rm']:
            by_session[key] = {'date': date.strftime('%b %d'), 'e1rm': e1rm,
                               'weight': s.weight, 'reps': s.reps}
        by_session[key]['volume'] = by_session.get(key, {}).get('volume', 0) + vol

    sessions = list(by_session.values())
    return jsonify({
        'labels': [s['date'] for s in sessions],
        'e1rm': [s['e1rm'] for s in sessions],
        'volume': [s.get('volume', 0) for s in sessions],
    })


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
            model='claude-opus-4-7',
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

EXERCISES = [
    ('Bench Press', 'Chest', 'strength'), ('Incline Bench Press', 'Chest', 'strength'),
    ('Decline Bench Press', 'Chest', 'strength'), ('Dumbbell Flyes', 'Chest', 'strength'),
    ('Cable Crossover', 'Chest', 'strength'), ('Push-ups', 'Chest', 'bodyweight'),
    ('Dips', 'Chest', 'bodyweight'), ('Incline Dumbbell Press', 'Chest', 'strength'),

    ('Deadlift', 'Back', 'strength'), ('Barbell Row', 'Back', 'strength'),
    ('Pull-ups', 'Back', 'bodyweight'), ('Chin-ups', 'Back', 'bodyweight'),
    ('Lat Pulldown', 'Back', 'strength'), ('Seated Cable Row', 'Back', 'strength'),
    ('T-Bar Row', 'Back', 'strength'), ('Single-Arm Dumbbell Row', 'Back', 'strength'),
    ('Face Pulls', 'Back', 'strength'),

    ('Overhead Press', 'Shoulders', 'strength'), ('Dumbbell Shoulder Press', 'Shoulders', 'strength'),
    ('Lateral Raises', 'Shoulders', 'strength'), ('Front Raises', 'Shoulders', 'strength'),
    ('Arnold Press', 'Shoulders', 'strength'), ('Rear Delt Flyes', 'Shoulders', 'strength'),

    ('Barbell Curl', 'Biceps', 'strength'), ('Dumbbell Curl', 'Biceps', 'strength'),
    ('Hammer Curl', 'Biceps', 'strength'), ('Preacher Curl', 'Biceps', 'strength'),
    ('Cable Curl', 'Biceps', 'strength'), ('Concentration Curl', 'Biceps', 'strength'),

    ('Skull Crushers', 'Triceps', 'strength'), ('Tricep Pushdown', 'Triceps', 'strength'),
    ('Overhead Tricep Extension', 'Triceps', 'strength'),
    ('Close-Grip Bench Press', 'Triceps', 'strength'), ('Diamond Push-ups', 'Triceps', 'bodyweight'),

    ('Squat', 'Legs', 'strength'), ('Front Squat', 'Legs', 'strength'),
    ('Leg Press', 'Legs', 'strength'), ('Romanian Deadlift', 'Legs', 'strength'),
    ('Leg Curl', 'Legs', 'strength'), ('Leg Extension', 'Legs', 'strength'),
    ('Calf Raises', 'Legs', 'strength'), ('Lunges', 'Legs', 'strength'),
    ('Bulgarian Split Squat', 'Legs', 'strength'), ('Hip Thrust', 'Legs', 'strength'),
    ('Sumo Deadlift', 'Legs', 'strength'),

    ('Plank', 'Core', 'bodyweight'), ('Crunches', 'Core', 'bodyweight'),
    ('Russian Twists', 'Core', 'bodyweight'), ('Leg Raises', 'Core', 'bodyweight'),
    ('Cable Crunch', 'Core', 'strength'), ('Ab Wheel Rollout', 'Core', 'bodyweight'),
    ('Hanging Leg Raises', 'Core', 'bodyweight'),
]


def seed():
    if Exercise.query.count() == 0:
        for name, muscle, cat in EXERCISES:
            db.session.add(Exercise(name=name, muscle_group=muscle, category=cat))
        db.session.commit()


try:
    with app.app_context():
        db.create_all()
        seed()
except Exception as e:
    print(f'DB init skipped: {e}')

if __name__ == '__main__':
    app.run(debug=True, port=5000)
