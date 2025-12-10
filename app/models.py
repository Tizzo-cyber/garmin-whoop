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