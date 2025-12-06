"""
Garmin WHOOP - Flask App with WHOOP-style Biological Age
"""

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
from functools import wraps
from sqlalchemy import text
import jwt
import os

from config import Config
from app.models import db, User, DailyMetric, Activity, SyncLog
from app.garmin_sync import GarminSyncService


def create_app():
    app = Flask(__name__, static_folder='../static', static_url_path='')
    app.config.from_object(Config)
    
    db.init_app(app)
    CORS(app)
    
    with app.app_context():
        db.create_all()
        
        # Migrations
        migrations = [
            'ALTER TABLE users ADD COLUMN IF NOT EXISTS birth_year INTEGER',
            'ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS biological_age FLOAT',
            'ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS vo2_max FLOAT',
            'ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS bio_age_rhr_impact FLOAT',
            'ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS bio_age_vo2_impact FLOAT',
            'ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS bio_age_sleep_impact FLOAT',
            'ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS bio_age_steps_impact FLOAT',
            'ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS bio_age_stress_impact FLOAT',
            'ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS bio_age_hrz_impact FLOAT',
            'ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS hr_zone_1_seconds INTEGER',
            'ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS hr_zone_2_seconds INTEGER',
            'ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS hr_zone_3_seconds INTEGER',
            'ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS hr_zone_4_seconds INTEGER',
            'ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS hr_zone_5_seconds INTEGER',
        ]
        for sql in migrations:
            try:
                db.session.execute(text(sql))
                db.session.commit()
            except Exception as e:
                db.session.rollback()
    
    def token_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            token = request.headers.get('Authorization', '').replace('Bearer ', '')
            if not token:
                return jsonify({'error': 'Token mancante'}), 401
            try:
                data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
                current_user = User.query.get(data['user_id'])
                if not current_user:
                    return jsonify({'error': 'Utente non trovato'}), 401
            except jwt.ExpiredSignatureError:
                return jsonify({'error': 'Token scaduto'}), 401
            except:
                return jsonify({'error': 'Token non valido'}), 401
            return f(current_user, *args, **kwargs)
        return decorated
    
    @app.route('/', methods=['GET'])
    def index():
        return send_from_directory(app.static_folder, 'index.html')
    
    @app.route('/api/register', methods=['POST'])
    def register():
        data = request.get_json()
        if not data.get('email') or not data.get('password'):
            return jsonify({'error': 'Email e password richiesti'}), 400
        if User.query.filter_by(email=data['email']).first():
            return jsonify({'error': 'Email già registrata'}), 400
        user = User(
            email=data['email'],
            password_hash=generate_password_hash(data['password']),
            birth_year=data.get('birth_year')
        )
        db.session.add(user)
        db.session.commit()
        return jsonify({'message': 'Utente creato', 'user_id': user.id}), 201
    
    @app.route('/api/login', methods=['POST'])
    def login():
        data = request.get_json()
        user = User.query.filter_by(email=data.get('email')).first()
        if not user or not check_password_hash(user.password_hash, data.get('password', '')):
            return jsonify({'error': 'Credenziali non valide'}), 401
        token = jwt.encode({
            'user_id': user.id,
            'exp': datetime.utcnow() + timedelta(days=30)
        }, app.config['SECRET_KEY'], algorithm='HS256')
        return jsonify({
            'token': token,
            'user': {
                'id': user.id,
                'email': user.email,
                'garmin_connected': bool(user.garmin_email),
                'birth_year': user.birth_year
            }
        })
    
    @app.route('/api/profile', methods=['PUT'])
    @token_required
    def update_profile(current_user):
        data = request.get_json()
        if data.get('birth_year'):
            current_user.birth_year = data['birth_year']
        db.session.commit()
        return jsonify({'message': 'Profilo aggiornato'})
    
    @app.route('/api/garmin/connect', methods=['POST'])
    @token_required
    def connect_garmin(current_user):
        data = request.get_json()
        if not data.get('garmin_email') or not data.get('garmin_password'):
            return jsonify({'error': 'Credenziali Garmin richieste'}), 400
        try:
            from garminconnect import Garmin
            client = Garmin(data['garmin_email'], data['garmin_password'])
            client.login()
        except Exception as e:
            return jsonify({'error': f'Login Garmin fallito: {str(e)}'}), 400
        current_user.garmin_email = data['garmin_email']
        current_user.set_garmin_password(data['garmin_password'], app.config['ENCRYPTION_KEY'])
        db.session.commit()
        return jsonify({'message': 'Account Garmin collegato'})
    
    @app.route('/api/garmin/disconnect', methods=['POST'])
    @token_required
    def disconnect_garmin(current_user):
        current_user.garmin_email = None
        current_user.garmin_password_encrypted = None
        db.session.commit()
        return jsonify({'message': 'Account Garmin scollegato'})
    
    @app.route('/api/sync', methods=['POST'])
    @token_required
    def sync_now(current_user):
        if not current_user.garmin_email:
            return jsonify({'error': 'Account Garmin non collegato'}), 400
        days_back = request.get_json().get('days_back', 7) if request.get_json() else 7
        service = GarminSyncService(app.config['ENCRYPTION_KEY'])
        result = service.sync_user(current_user, days_back=days_back)
        return jsonify(result)
    
    @app.route('/api/metrics/today', methods=['GET'])
    @token_required
    def get_today_metrics(current_user):
        metric = DailyMetric.query.filter_by(user_id=current_user.id, date=date.today()).first()
        if not metric:
            return jsonify({'message': 'Nessun dato per oggi'}), 404
        return jsonify(_metric_to_dict(metric, current_user))
    
    @app.route('/api/metrics/summary', methods=['GET'])
    @token_required
    def get_summary(current_user):
        days = request.args.get('days', 30, type=int)
        start_date = date.today() - timedelta(days=days)
        metrics = DailyMetric.query.filter(
            DailyMetric.user_id == current_user.id,
            DailyMetric.date >= start_date
        ).order_by(DailyMetric.date.desc()).all()
        
        if not metrics:
            return jsonify({'message': 'Nessun dato disponibile'}), 404
        
        def safe_avg(lst):
            vals = [x for x in lst if x is not None]
            return round(sum(vals) / len(vals), 1) if vals else None
        
        # Calculate averages for bio age impacts
        return jsonify({
            'period': {
                'start': start_date.isoformat(),
                'end': date.today().isoformat(),
                'days_with_data': len(metrics)
            },
            'averages': {
                'recovery': safe_avg([m.recovery_score for m in metrics]),
                'strain': safe_avg([m.strain_score for m in metrics]),
                'sleep_performance': safe_avg([m.sleep_performance for m in metrics]),
                'sleep_hours': safe_avg([m.sleep_seconds / 3600 if m.sleep_seconds else None for m in metrics]),
                'biological_age': safe_avg([m.biological_age for m in metrics]),
                'resting_hr': safe_avg([m.resting_hr for m in metrics]),
                'vo2_max': safe_avg([m.vo2_max for m in metrics]),
                'steps': safe_avg([m.steps for m in metrics]),
                # Bio age impacts averages
                'bio_impacts': {
                    'rhr': safe_avg([m.bio_age_rhr_impact for m in metrics]),
                    'vo2': safe_avg([m.bio_age_vo2_impact for m in metrics]),
                    'sleep': safe_avg([m.bio_age_sleep_impact for m in metrics]),
                    'steps': safe_avg([m.bio_age_steps_impact for m in metrics]),
                    'stress': safe_avg([m.bio_age_stress_impact for m in metrics]),
                    'hrz': safe_avg([m.bio_age_hrz_impact for m in metrics]),
                }
            },
            'real_age': current_user.get_real_age(),
            'today': _metric_to_dict(metrics[0], current_user) if metrics else None
        })
    
    @app.route('/api/metrics/trend', methods=['GET'])
    @token_required
    def get_trend(current_user):
        days = request.args.get('days', 90, type=int)
        start_date = date.today() - timedelta(days=days)
        metrics = DailyMetric.query.filter(
            DailyMetric.user_id == current_user.id,
            DailyMetric.date >= start_date
        ).order_by(DailyMetric.date.asc()).all()
        
        trend_data = [{
            'date': m.date.isoformat(),
            'recovery': m.recovery_score,
            'strain': m.strain_score,
            'sleep_hours': round(m.sleep_seconds / 3600, 1) if m.sleep_seconds else None,
            'biological_age': m.biological_age,
            'resting_hr': m.resting_hr,
            'vo2_max': m.vo2_max,
            'steps': m.steps,
        } for m in metrics]
        
        # Calculate pace of aging (compare last 30 days to previous 30 days)
        pace_of_aging = None
        if len(metrics) >= 30:
            recent = [m.biological_age for m in metrics[-30:] if m.biological_age]
            if len(recent) >= 15:
                recent_avg = sum(recent) / len(recent)
                older = [m.biological_age for m in metrics[:-30] if m.biological_age]
                if older and len(older) >= 15:
                    older_avg = sum(older) / len(older)
                    # Pace: negative = getting younger
                    days_diff = 30
                    age_diff = recent_avg - older_avg
                    # Convert to yearly pace: if in 30 days you got 0.1 years younger, pace = -1.2x
                    yearly_pace = (age_diff / days_diff) * 365
                    pace_of_aging = round(yearly_pace, 1)
        
        return jsonify({
            'data': trend_data,
            'real_age': current_user.get_real_age(),
            'pace_of_aging': pace_of_aging
        })
    
    @app.route('/api/metrics/advice', methods=['GET'])
    @token_required
    def get_advice(current_user):
        week_ago = date.today() - timedelta(days=7)
        metrics = DailyMetric.query.filter(
            DailyMetric.user_id == current_user.id,
            DailyMetric.date >= week_ago
        ).order_by(DailyMetric.date.desc()).all()
        
        if not metrics:
            return jsonify({'advice': []})
        
        advice = []
        today = metrics[0]
        
        if today.recovery_score:
            if today.recovery_score >= 80:
                advice.append({
                    'type': 'recovery', 'priority': 'success',
                    'coach': "🔥 SEI UNA MACCHINA! Recovery eccellente!",
                    'zen': "L'energia scorre potente in te.",
                    'action': "Allenamento intenso consigliato"
                })
            elif today.recovery_score >= 50:
                advice.append({
                    'type': 'recovery', 'priority': 'warning',
                    'coach': "💪 Recovery discreto. Ascolta il corpo.",
                    'zen': "Adatta l'intensità al momento.",
                    'action': "Allenamento moderato"
                })
            else:
                advice.append({
                    'type': 'recovery', 'priority': 'danger',
                    'coach': "⚠️ Il tuo corpo chiede riposo.",
                    'zen': "Anche la spada deve riposare.",
                    'action': "Riposo attivo oggi"
                })
        
        # Bio age advice
        if today.biological_age:
            real_age = current_user.get_real_age()
            diff = real_age - today.biological_age
            if diff >= 3:
                advice.append({
                    'type': 'bio_age', 'priority': 'success',
                    'coach': f"🎯 FENOMENO! {diff:.1f} anni più giovane!",
                    'zen': "Il tempo scorre, ma tu fluisci controcorrente.",
                    'action': "Continua così!"
                })
            elif diff < -2:
                advice.append({
                    'type': 'bio_age', 'priority': 'warning',
                    'coach': f"⚠️ Età biologica {today.biological_age:.0f} vs {real_age}",
                    'zen': "Mai troppo tardi per rinascere.",
                    'action': "Focus su movimento e sonno"
                })
        
        return jsonify({'advice': advice})
    
    @app.route('/api/activities', methods=['GET'])
    @token_required
    def get_activities(current_user):
        limit = request.args.get('limit', 20, type=int)
        activities = Activity.query.filter_by(user_id=current_user.id).order_by(Activity.start_time.desc()).limit(limit).all()
        return jsonify([{
            'id': a.id,
            'name': a.activity_name,
            'type': a.activity_type,
            'start_time': a.start_time.isoformat() if a.start_time else None,
            'duration_minutes': round(a.duration_seconds / 60, 1) if a.duration_seconds else None,
            'calories': a.calories,
            'avg_hr': a.avg_hr,
            'strain': a.strain_score
        } for a in activities])
    
    @app.route('/api/health', methods=['GET'])
    def health_check():
        return jsonify({'status': 'ok'})
    
    @app.route('/api', methods=['GET'])
    def api_info():
        return jsonify({'app': 'SENSEI', 'version': '2.0.0'})
    
    return app


def _metric_to_dict(m, user):
    return {
        'date': m.date.isoformat(),
        'scores': {
            'recovery': m.recovery_score,
            'strain': m.strain_score,
            'sleep_performance': m.sleep_performance,
            'biological_age': m.biological_age
        },
        'bio_impacts': {
            'rhr': m.bio_age_rhr_impact,
            'vo2': m.bio_age_vo2_impact,
            'sleep': m.bio_age_sleep_impact,
            'steps': m.bio_age_steps_impact,
            'stress': m.bio_age_stress_impact,
            'hrz': m.bio_age_hrz_impact
        },
        'real_age': user.get_real_age(),
        'heart': {
            'resting_hr': m.resting_hr,
            'vo2_max': m.vo2_max,
            'hrv_last_night': m.hrv_last_night
        },
        'body_battery': {
            'high': m.body_battery_high,
            'low': m.body_battery_low
        },
        'sleep': {
            'total_hours': round(m.sleep_seconds / 3600, 1) if m.sleep_seconds else None,
            'deep_hours': round(m.deep_sleep_seconds / 3600, 1) if m.deep_sleep_seconds else None,
            'rem_hours': round(m.rem_sleep_seconds / 3600, 1) if m.rem_sleep_seconds else None,
        },
        'stress': {'average': m.stress_avg},
        'activity': {
            'steps': m.steps,
            'active_calories': m.active_calories,
            'vigorous_minutes': m.vigorous_intensity_minutes
        }
    }


app = create_app()

if __name__ == '__main__':
    app.run(debug=True, port=5000)