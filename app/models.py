from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from cryptography.fernet import Fernet
import base64
import hashlib

db = SQLAlchemy()

def get_fernet(key: str) -> Fernet:
    """Crea un oggetto Fernet da una chiave stringa"""
    key_bytes = hashlib.sha256(key.encode()).digest()
    key_b64 = base64.urlsafe_b64encode(key_bytes)
    return Fernet(key_b64)


class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)  # Password app
    
    # Profile
    name = db.Column(db.String(100))
    birth_year = db.Column(db.Integer)
    sport_goals = db.Column(db.Text)
    
    # Credenziali Garmin (criptate)
    garmin_email = db.Column(db.String(255))
    garmin_password_encrypted = db.Column(db.Text)
    
    # Stato sync
    last_sync = db.Column(db.DateTime)
    sync_enabled = db.Column(db.Boolean, default=True)
    
    # Obiettivi nutrizione
    calorie_goal = db.Column(db.Integer, default=2000)
    protein_goal = db.Column(db.Integer, default=120)  # grammi
    carbs_goal = db.Column(db.Integer, default=250)    # grammi
    fat_goal = db.Column(db.Integer, default=70)       # grammi
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relazioni
    daily_metrics = db.relationship('DailyMetric', backref='user', lazy='dynamic')
    activities = db.relationship('Activity', backref='user', lazy='dynamic')
    
    def set_garmin_password(self, password: str, encryption_key: str):
        """Cripta e salva la password Garmin"""
        f = get_fernet(encryption_key)
        self.garmin_password_encrypted = f.encrypt(password.encode()).decode()
    
    def get_garmin_password(self, encryption_key: str) -> str:
        """Decripta e ritorna la password Garmin"""
        if not self.garmin_password_encrypted:
            return None
        f = get_fernet(encryption_key)
        return f.decrypt(self.garmin_password_encrypted.encode()).decode()
    
    def get_real_age(self):
        if self.birth_year:
            return datetime.now().year - self.birth_year
        return 42


class DailyMetric(db.Model):
    __tablename__ = 'daily_metrics'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    
    # Heart
    resting_hr = db.Column(db.Integer)
    min_hr = db.Column(db.Integer)
    max_hr = db.Column(db.Integer)
    avg_hr = db.Column(db.Integer)
    
    # HRV (se disponibile)
    hrv_weekly_avg = db.Column(db.Float)
    hrv_last_night = db.Column(db.Float)
    
    # Body Battery
    body_battery_high = db.Column(db.Integer)
    body_battery_low = db.Column(db.Integer)
    body_battery_charged = db.Column(db.Integer)
    body_battery_drained = db.Column(db.Integer)
    
    # Sleep
    sleep_seconds = db.Column(db.Integer)
    deep_sleep_seconds = db.Column(db.Integer)
    light_sleep_seconds = db.Column(db.Integer)
    rem_sleep_seconds = db.Column(db.Integer)
    awake_seconds = db.Column(db.Integer)
    sleep_score = db.Column(db.Integer)
    sleep_start = db.Column(db.DateTime)
    sleep_end = db.Column(db.DateTime)
    
    # Stress
    stress_avg = db.Column(db.Integer)
    stress_max = db.Column(db.Integer)
    rest_stress_duration = db.Column(db.Integer)
    low_stress_duration = db.Column(db.Integer)
    medium_stress_duration = db.Column(db.Integer)
    high_stress_duration = db.Column(db.Integer)
    
    # Activity
    steps = db.Column(db.Integer)
    total_calories = db.Column(db.Integer)
    active_calories = db.Column(db.Integer)
    distance_meters = db.Column(db.Integer)
    floors_ascended = db.Column(db.Integer)
    moderate_intensity_minutes = db.Column(db.Integer)
    vigorous_intensity_minutes = db.Column(db.Integer)
    active_seconds = db.Column(db.Integer)
    sedentary_seconds = db.Column(db.Integer)
    
    # Respiration
    avg_respiration = db.Column(db.Float)
    min_respiration = db.Column(db.Float)
    max_respiration = db.Column(db.Float)
    
    # SpO2
    avg_spo2 = db.Column(db.Float)
    min_spo2 = db.Column(db.Float)
    
    # VO2 Max
    vo2_max = db.Column(db.Float)
    
    # Fitness Age (da Garmin)
    fitness_age = db.Column(db.Integer)
    
    # Race Predictions (secondi)
    race_time_5k = db.Column(db.Integer)
    race_time_10k = db.Column(db.Integer)
    race_time_half = db.Column(db.Integer)
    race_time_marathon = db.Column(db.Integer)
    
    # Metriche calcolate
    recovery_score = db.Column(db.Integer)  # 0-100
    strain_score = db.Column(db.Float)      # 0-21 stile WHOOP
    sleep_performance = db.Column(db.Integer)  # 0-100
    biological_age = db.Column(db.Float)
    
    # Impatto singoli fattori sull'età biologica
    bio_age_rhr_impact = db.Column(db.Float)
    bio_age_vo2_impact = db.Column(db.Float)
    bio_age_sleep_impact = db.Column(db.Float)
    bio_age_steps_impact = db.Column(db.Float)
    bio_age_stress_impact = db.Column(db.Float)
    bio_age_hrz_impact = db.Column(db.Float)
    
    # Raw data per debug
    raw_json = db.Column(db.Text)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        db.UniqueConstraint('user_id', 'date', name='unique_user_date'),
    )


class Activity(db.Model):
    __tablename__ = 'activities'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    garmin_activity_id = db.Column(db.BigInteger, unique=True)
    
    # Info base
    activity_name = db.Column(db.String(255))
    activity_type = db.Column(db.String(100))
    start_time = db.Column(db.DateTime)
    end_time = db.Column(db.DateTime)
    
    # Metriche
    duration_seconds = db.Column(db.Float)
    distance_meters = db.Column(db.Float)
    calories = db.Column(db.Integer)
    avg_hr = db.Column(db.Integer)
    max_hr = db.Column(db.Integer)
    
    # Training Effect
    aerobic_effect = db.Column(db.Float)
    anaerobic_effect = db.Column(db.Float)
    
    # HR Zones (secondi in ogni zona)
    hr_zone_1 = db.Column(db.Float)
    hr_zone_2 = db.Column(db.Float)
    hr_zone_3 = db.Column(db.Float)
    hr_zone_4 = db.Column(db.Float)
    hr_zone_5 = db.Column(db.Float)
    
    # Intensity minutes
    moderate_intensity_minutes = db.Column(db.Integer)
    vigorous_intensity_minutes = db.Column(db.Integer)
    
    # Strain calcolato
    strain_score = db.Column(db.Float)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class SyncLog(db.Model):
    """Log delle sincronizzazioni"""
    __tablename__ = 'sync_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    finished_at = db.Column(db.DateTime)
    status = db.Column(db.String(50))  # success, error, partial
    error_message = db.Column(db.Text)
    metrics_synced = db.Column(db.Integer, default=0)
    activities_synced = db.Column(db.Integer, default=0)


class ChatMessage(db.Model):
    """Messaggi chat con i coach AI"""
    __tablename__ = 'chat_messages'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # user, assistant
    content = db.Column(db.Text, nullable=False)
    coach = db.Column(db.String(20))  # sensei, sakura
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class UserMemory(db.Model):
    """Memoria persistente dei coach"""
    __tablename__ = 'user_memories'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    category = db.Column(db.String(50))
    content = db.Column(db.Text, nullable=False)
    coach = db.Column(db.String(20))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)








# ==================== AGGIUNGI A models.py ====================
# Copia queste classi nel file app/models.py

class FatigueLog(db.Model):
    """Log fatica percepita giornaliera (1-10)"""
    __tablename__ = 'fatigue_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    value = db.Column(db.Integer, nullable=False)  # 1-10
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Unique constraint: un solo valore per utente/giorno
    __table_args__ = (db.UniqueConstraint('user_id', 'date', name='unique_user_date_fatigue'),)
    
    user = db.relationship('User', backref=db.backref('fatigue_logs', lazy='dynamic'))


class WeeklyCheck(db.Model):
    """Check-in settimanale per Sensei (fisico) e Sakura (mentale)"""
    __tablename__ = 'weekly_checks'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    coach = db.Column(db.String(20), nullable=False)  # 'sensei' o 'sakura'
    answers = db.Column(db.Text, nullable=False)  # JSON string
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref=db.backref('weekly_checks', lazy='dynamic'))
    
    def get_answers_dict(self):
        """Ritorna answers come dict"""
        import json
        return json.loads(self.answers) if self.answers else {}
    
    def set_answers_dict(self, answers_dict):
        """Salva answers da dict"""
        import json
        self.answers = json.dumps(answers_dict)


class FoodEntry(db.Model):
    """Tracciamento pasti e nutrizione"""
    __tablename__ = 'food_entries'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    
    # Tipo pasto
    meal_type = db.Column(db.String(20), nullable=False)  # breakfast, lunch, dinner, snack
    
    # Info cibo
    food_name = db.Column(db.String(255), nullable=False)
    brand = db.Column(db.String(255))
    barcode = db.Column(db.String(50))
    
    # Quantità
    serving_size = db.Column(db.Float, default=100)  # grammi o porzione
    serving_unit = db.Column(db.String(20), default='g')  # g, ml, porzione
    
    # Valori nutrizionali
    calories = db.Column(db.Integer, nullable=False)
    protein = db.Column(db.Float)  # grammi
    carbs = db.Column(db.Float)    # grammi
    fat = db.Column(db.Float)      # grammi
    fiber = db.Column(db.Float)    # grammi
    sugar = db.Column(db.Float)    # grammi
    
    # Origine dati
    source = db.Column(db.String(50))  # openfoodfacts, manual, ai_estimate
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref=db.backref('food_entries', lazy='dynamic'))


# ==================== LOU - SCULPTING COACH ====================

class GymProfile(db.Model):
    """Profilo palestra utente per Lou"""
    __tablename__ = 'gym_profiles'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True)
    
    # Livello esperienza
    experience = db.Column(db.String(20), default='beginner')  # beginner, intermediate, advanced
    
    # Disponibilità
    days_per_week = db.Column(db.Integer, default=3)  # 3-6
    session_minutes = db.Column(db.Integer, default=60)  # 45, 60, 90
    
    # Muscoli da escludere (JSON array: ["abs", "shoulders"])
    excluded_muscles = db.Column(db.Text, default='[]')
    
    # Muscoli prioritari (JSON array: ["glutes", "legs"])
    priority_muscles = db.Column(db.Text, default='["glutes", "legs"]')
    
    # Equipaggiamento disponibile (JSON array)
    equipment = db.Column(db.Text, default='["barbell", "dumbbells", "cables", "machines"]')
    
    # Modificatore intensità globale (0.6 = relax, 1.0 = normale, 1.2 = beast)
    intensity_modifier = db.Column(db.Float, default=1.0)
    
    # Obiettivo principale
    primary_goal = db.Column(db.String(50), default='toning')  # toning, strength, hypertrophy
    
    # === CICLO MESTRUALE (Dr. Stacy Sims) ===
    # Tracciamento per ottimizzare allenamento
    track_cycle = db.Column(db.Boolean, default=False)  # Vuole tracciare?
    cycle_length = db.Column(db.Integer, default=28)  # Lunghezza ciclo (21-35 giorni)
    last_period_start = db.Column(db.Date, nullable=True)  # Ultimo giorno 1
    
    # === PERIODIZZAZIONE ===
    periodization_type = db.Column(db.String(20), default='simple')  # simple, dup, undulating
    
    # === ESERCIZI PREFERITI ===
    favorite_exercises = db.Column(db.Text, default='[]')  # JSON array di exercise IDs
    
    def get_favorite_exercises(self):
        import json
        try:
            return json.loads(self.favorite_exercises or '[]')
        except:
            return []
    
    def set_favorite_exercises(self, exercises):
        import json
        self.favorite_exercises = json.dumps(exercises or [])
    
    # Setup completato?
    setup_complete = db.Column(db.Boolean, default=False)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = db.relationship('User', backref=db.backref('gym_profile', uselist=False))
    
    def get_cycle_phase(self):
        """
        Calcola la fase del ciclo mestruale (Dr. Stacy Sims method)
        
        FASE FOLLICOLARE (Giorno 1-14): Estrogeni in salita
        - Più forza, più tolleranza al volume, migliore recupero
        - PUSH HARD! Ideale per PR e sessioni intense
        
        FASE OVULATORIA (Giorno 12-16): Picco estrogeni
        - Massima forza potenziale MA attenzione ai legamenti
        - Buona per forza, cautela su movimenti esplosivi
        
        FASE LUTEALE (Giorno 15-28): Progesterone alto
        - Temperatura corporea +0.5°C, più fatica
        - Ridurre volume/intensità, focus tecnica
        - Ultimi giorni (PMS): ancora più cautela
        
        Returns: dict con phase, day, recommendations
        """
        from datetime import date, timedelta
        
        if not self.track_cycle or not self.last_period_start:
            return None
        
        days_since = (date.today() - self.last_period_start).days
        cycle_day = (days_since % self.cycle_length) + 1
        
        if cycle_day <= 5:
            # Mestruazione
            return {
                'phase': 'mestruation',
                'phase_name': '🔴 Mestruazione',
                'day': cycle_day,
                'intensity_modifier': 0.8,
                'recommendation': 'Ascolta il corpo. Se ti senti bene vai, altrimenti sessione leggera o riposo.',
                'focus': 'Mobilità, cardio leggero, o riposo attivo',
                'avoid': 'Nulla di forzato se hai crampi'
            }
        elif cycle_day <= 14:
            # Fase follicolare
            return {
                'phase': 'follicular',
                'phase_name': '🟢 Fase Follicolare',
                'day': cycle_day,
                'intensity_modifier': 1.1,
                'recommendation': 'SPACCA! Estrogeni alti = più forza e recupero. Ideale per PR!',
                'focus': 'Sessioni intense, carichi pesanti, volume alto',
                'avoid': 'Niente - è il momento di spingere!'
            }
        elif cycle_day <= 17:
            # Ovulazione
            return {
                'phase': 'ovulation',
                'phase_name': '🟡 Ovulazione',
                'day': cycle_day,
                'intensity_modifier': 1.0,
                'recommendation': 'Forza al top ma legamenti più lassi. Attenzione alla tecnica!',
                'focus': 'Forza con controllo, evita movimenti esplosivi/balistici',
                'avoid': 'Jump squat, box jump, movimenti esplosivi'
            }
        elif cycle_day <= self.cycle_length - 5:
            # Fase luteale
            return {
                'phase': 'luteal',
                'phase_name': '🟠 Fase Luteale',
                'day': cycle_day,
                'intensity_modifier': 0.9,
                'recommendation': 'Progesterone alto = più fatica. Riduci un po\' e focus qualità.',
                'focus': 'Volume moderato, tecnica perfetta, steady state cardio',
                'avoid': 'Sessioni troppo lunghe o intense'
            }
        else:
            # PMS / Pre-mestruale
            return {
                'phase': 'pms',
                'phase_name': '🟣 Pre-mestruale',
                'day': cycle_day,
                'intensity_modifier': 0.75,
                'recommendation': 'Sii gentile con te stessa. Movimento leggero, no pressione.',
                'focus': 'Yoga, stretching, camminate, sessioni brevi',
                'avoid': 'Aspettative alte, sessioni lunghe'
            }
    
    def get_excluded_muscles(self):
        import json
        return json.loads(self.excluded_muscles) if self.excluded_muscles else []
    
    def set_excluded_muscles(self, muscles):
        import json
        self.excluded_muscles = json.dumps(muscles)
    
    def get_priority_muscles(self):
        import json
        return json.loads(self.priority_muscles) if self.priority_muscles else []
    
    def set_priority_muscles(self, muscles):
        import json
        self.priority_muscles = json.dumps(muscles)
    
    def get_equipment(self):
        import json
        return json.loads(self.equipment) if self.equipment else []


class WorkoutProgram(db.Model):
    """Programma di allenamento generato da Lou"""
    __tablename__ = 'workout_programs'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    
    name = db.Column(db.String(100), nullable=False)  # "Programma Glutei Focus"
    description = db.Column(db.Text)
    
    # Tipo split
    split_type = db.Column(db.String(50))  # PPL, Upper-Lower, Bro, FullBody
    
    # Durata e progressione
    weeks_total = db.Column(db.Integer, default=6)
    current_week = db.Column(db.Integer, default=1)
    
    # Stato
    is_active = db.Column(db.Boolean, default=True)
    created_by_ai = db.Column(db.Boolean, default=True)
    
    # Timestamps
    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref=db.backref('workout_programs', lazy='dynamic'))
    days = db.relationship('WorkoutDay', backref='program', lazy='dynamic', cascade='all, delete-orphan')


class WorkoutDay(db.Model):
    """Giorno di allenamento nel programma"""
    __tablename__ = 'workout_days'
    
    id = db.Column(db.Integer, primary_key=True)
    program_id = db.Column(db.Integer, db.ForeignKey('workout_programs.id'), nullable=False)
    
    # Giorno della settimana (1=Lunedì, 7=Domenica)
    day_of_week = db.Column(db.Integer, nullable=False)
    
    # Nome e descrizione
    name = db.Column(db.String(100), nullable=False)  # "Leg Day 🦵"
    
    # Muscoli target (JSON array)
    muscle_groups = db.Column(db.Text)  # ["glutes", "quads", "hamstrings"]
    
    # Durata stimata in minuti
    estimated_minutes = db.Column(db.Integer, default=60)
    
    # Ordine nel programma
    order = db.Column(db.Integer, default=0)
    
    exercises = db.relationship('ProgramExercise', backref='workout_day', lazy='dynamic', cascade='all, delete-orphan')
    
    def get_muscle_groups(self):
        import json
        return json.loads(self.muscle_groups) if self.muscle_groups else []


class ProgramExercise(db.Model):
    """Esercizio nel programma"""
    __tablename__ = 'program_exercises'
    
    id = db.Column(db.Integer, primary_key=True)
    workout_day_id = db.Column(db.Integer, db.ForeignKey('workout_days.id'), nullable=False)
    
    # Ordine nell'allenamento
    order = db.Column(db.Integer, nullable=False)
    
    # Info esercizio
    name = db.Column(db.String(100), nullable=False)  # "Hip Thrust"
    muscle_group = db.Column(db.String(50))  # glutes, quads, back, shoulders, arms, abs
    equipment = db.Column(db.String(50))  # barbell, dumbbells, cables, bodyweight, machine
    
    # Parametri
    sets = db.Column(db.Integer, default=4)
    reps_min = db.Column(db.Integer, default=8)
    reps_max = db.Column(db.Integer, default=12)
    rest_seconds = db.Column(db.Integer, default=90)
    
    # RPE target (Rate of Perceived Exertion)
    rpe_target = db.Column(db.Integer, default=7)  # 1-10
    
    # Note tecniche
    notes = db.Column(db.Text)
    video_url = db.Column(db.String(500))
    
    # Peso suggerito (calcolato da storico)
    suggested_weight = db.Column(db.Float)


class ExerciseLog(db.Model):
    """Log di un esercizio completato"""
    __tablename__ = 'exercise_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    exercise_id = db.Column(db.Integer, db.ForeignKey('program_exercises.id'))
    
    # Data e ora
    date = db.Column(db.Date, nullable=False)
    
    # Nome esercizio (anche per esercizi fuori programma)
    exercise_name = db.Column(db.String(100), nullable=False)
    muscle_group = db.Column(db.String(50))
    
    # Risultati
    sets_completed = db.Column(db.Integer)
    reps_per_set = db.Column(db.Text)  # JSON array: [12, 12, 10, 8]
    weight_kg = db.Column(db.Float)
    
    # Feedback
    rpe = db.Column(db.Integer)  # 1-10 Rate of Perceived Exertion
    feedback = db.Column(db.String(20))  # too_easy, perfect, too_hard
    
    # PR (Personal Record)?
    is_pr = db.Column(db.Boolean, default=False)
    
    # Note
    notes = db.Column(db.Text)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref=db.backref('exercise_logs', lazy='dynamic'))
    
    def get_reps_array(self):
        import json
        return json.loads(self.reps_per_set) if self.reps_per_set else []
    
    def set_reps_array(self, reps):
        import json
        self.reps_per_set = json.dumps(reps)


class WorkoutSession(db.Model):
    """Sessione di allenamento completata"""
    __tablename__ = 'workout_sessions'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    workout_day_id = db.Column(db.Integer, db.ForeignKey('workout_days.id'))
    
    date = db.Column(db.Date, nullable=False)
    
    # Durata effettiva
    duration_minutes = db.Column(db.Integer)
    
    # Volume totale (kg × reps)
    total_volume = db.Column(db.Float)
    
    # Feedback generale
    overall_rpe = db.Column(db.Integer)  # 1-10
    feeling = db.Column(db.String(20))  # great, good, okay, tired, exhausted
    
    # Note
    notes = db.Column(db.Text)
    lou_comment = db.Column(db.Text)  # Commento generato da Lou
    
    # PRs in questa sessione (JSON array)
    prs_achieved = db.Column(db.Text)  # ["Hip Thrust 65kg", "Squat 50kg"]
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref=db.backref('workout_sessions', lazy='dynamic'))


class GymWeeklyReport(db.Model):
    """Report settimanale generato da Lou"""
    __tablename__ = 'gym_weekly_reports'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    program_id = db.Column(db.Integer, db.ForeignKey('workout_programs.id'))
    
    # Settimana
    week_number = db.Column(db.Integer, nullable=False)
    week_start = db.Column(db.Date, nullable=False)
    week_end = db.Column(db.Date, nullable=False)
    
    # Statistiche
    sessions_planned = db.Column(db.Integer)
    sessions_completed = db.Column(db.Integer)
    total_volume_kg = db.Column(db.Float)
    total_duration_min = db.Column(db.Integer)
    
    # PRs della settimana
    prs_achieved = db.Column(db.Text)  # JSON array
    
    # Medie
    avg_rpe = db.Column(db.Float)
    
    # Progressi per muscolo (JSON)
    muscle_progress = db.Column(db.Text)  # {"glutes": +5%, "legs": +3%}
    
    # Messaggio di Lou
    lou_message = db.Column(db.Text)
    
    # Scelta utente per prossima settimana
    user_choice = db.Column(db.String(20))  # push, maintain, deload
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref=db.backref('gym_weekly_reports', lazy='dynamic'))