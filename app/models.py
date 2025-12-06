from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from cryptography.fernet import Fernet
import base64
import hashlib

db = SQLAlchemy()

def get_fernet(key: str) -> Fernet:
    key_bytes = hashlib.sha256(key.encode()).digest()
    key_b64 = base64.urlsafe_b64encode(key_bytes)
    return Fernet(key_b64)


class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    
    garmin_email = db.Column(db.String(255))
    garmin_password_encrypted = db.Column(db.Text)
    
    birth_year = db.Column(db.Integer)
    name = db.Column(db.String(100))
    sport_goals = db.Column(db.Text)
    injuries = db.Column(db.Text)
    
    last_sync = db.Column(db.DateTime)
    sync_enabled = db.Column(db.Boolean, default=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    daily_metrics = db.relationship('DailyMetric', backref='user', lazy='dynamic')
    activities = db.relationship('Activity', backref='user', lazy='dynamic')
    chat_messages = db.relationship('ChatMessage', backref='user', lazy='dynamic')
    memories = db.relationship('UserMemory', backref='user', lazy='dynamic')
    
    def set_garmin_password(self, password: str, encryption_key: str):
        f = get_fernet(encryption_key)
        self.garmin_password_encrypted = f.encrypt(password.encode()).decode()
    
    def get_garmin_password(self, encryption_key: str) -> str:
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
    
    resting_hr = db.Column(db.Integer)
    min_hr = db.Column(db.Integer)
    max_hr = db.Column(db.Integer)
    avg_hr = db.Column(db.Integer)
    
    hrv_weekly_avg = db.Column(db.Float)
    hrv_last_night = db.Column(db.Float)
    vo2_max = db.Column(db.Float)
    
    body_battery_high = db.Column(db.Integer)
    body_battery_low = db.Column(db.Integer)
    body_battery_charged = db.Column(db.Integer)
    body_battery_drained = db.Column(db.Integer)
    
    sleep_seconds = db.Column(db.Integer)
    deep_sleep_seconds = db.Column(db.Integer)
    light_sleep_seconds = db.Column(db.Integer)
    rem_sleep_seconds = db.Column(db.Integer)
    awake_seconds = db.Column(db.Integer)
    sleep_score = db.Column(db.Integer)
    sleep_start = db.Column(db.DateTime)
    sleep_end = db.Column(db.DateTime)
    
    stress_avg = db.Column(db.Integer)
    stress_max = db.Column(db.Integer)
    rest_stress_duration = db.Column(db.Integer)
    low_stress_duration = db.Column(db.Integer)
    medium_stress_duration = db.Column(db.Integer)
    high_stress_duration = db.Column(db.Integer)
    
    steps = db.Column(db.Integer)
    total_calories = db.Column(db.Integer)
    active_calories = db.Column(db.Integer)
    distance_meters = db.Column(db.Integer)
    floors_ascended = db.Column(db.Integer)
    moderate_intensity_minutes = db.Column(db.Integer)
    vigorous_intensity_minutes = db.Column(db.Integer)
    active_seconds = db.Column(db.Integer)
    sedentary_seconds = db.Column(db.Integer)
    
    hr_zone_1_seconds = db.Column(db.Integer)
    hr_zone_2_seconds = db.Column(db.Integer)
    hr_zone_3_seconds = db.Column(db.Integer)
    hr_zone_4_seconds = db.Column(db.Integer)
    hr_zone_5_seconds = db.Column(db.Integer)
    
    avg_respiration = db.Column(db.Float)
    min_respiration = db.Column(db.Float)
    max_respiration = db.Column(db.Float)
    
    avg_spo2 = db.Column(db.Float)
    min_spo2 = db.Column(db.Float)
    
    recovery_score = db.Column(db.Integer)
    strain_score = db.Column(db.Float)
    sleep_performance = db.Column(db.Integer)
    biological_age = db.Column(db.Float)
    
    bio_age_rhr_impact = db.Column(db.Float)
    bio_age_vo2_impact = db.Column(db.Float)
    bio_age_sleep_impact = db.Column(db.Float)
    bio_age_steps_impact = db.Column(db.Float)
    bio_age_stress_impact = db.Column(db.Float)
    bio_age_hrz_impact = db.Column(db.Float)
    
    raw_json = db.Column(db.Text)
    
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
    
    activity_name = db.Column(db.String(255))
    activity_type = db.Column(db.String(100))
    start_time = db.Column(db.DateTime)
    end_time = db.Column(db.DateTime)
    
    duration_seconds = db.Column(db.Float)
    distance_meters = db.Column(db.Float)
    calories = db.Column(db.Integer)
    avg_hr = db.Column(db.Integer)
    max_hr = db.Column(db.Integer)
    
    aerobic_effect = db.Column(db.Float)
    anaerobic_effect = db.Column(db.Float)
    
    hr_zone_1 = db.Column(db.Float)
    hr_zone_2 = db.Column(db.Float)
    hr_zone_3 = db.Column(db.Float)
    hr_zone_4 = db.Column(db.Float)
    hr_zone_5 = db.Column(db.Float)
    
    moderate_intensity_minutes = db.Column(db.Integer)
    vigorous_intensity_minutes = db.Column(db.Integer)
    
    strain_score = db.Column(db.Float)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class SyncLog(db.Model):
    __tablename__ = 'sync_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    finished_at = db.Column(db.DateTime)
    status = db.Column(db.String(50))
    error_message = db.Column(db.Text)
    metrics_synced = db.Column(db.Integer, default=0)
    activities_synced = db.Column(db.Integer, default=0)


class ChatMessage(db.Model):
    """Cronologia conversazioni"""
    __tablename__ = 'chat_messages'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    context_summary = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class UserMemory(db.Model):
    """Memoria persistente - fatti importanti sull'utente"""
    __tablename__ = 'user_memories'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    
    category = db.Column(db.String(50))  # injury, goal, preference, health, lifestyle
    content = db.Column(db.Text, nullable=False)
    
    # Per tracking
    is_active = db.Column(db.Boolean, default=True)  # False se risolto (es. infortunio guarito)
    source_message_id = db.Column(db.Integer)  # Da quale messaggio viene
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)