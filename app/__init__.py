"""
Garmin WHOOP - Flask App
"""

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
from functools import wraps
import jwt
import os

from config import Config
from app.models import db, User, DailyMetric, Activity, SyncLog
from app.garmin_sync import GarminSyncService


def create_app():
    app = Flask(__name__, static_folder='../static', static_url_path='')
    app.config.from_object(Config)
    
    # Init extensions
    db.init_app(app)
    CORS(app)
    
    # Create tables
    with app.app_context():
        db.create_all()
    
    # ========== AUTH HELPERS ==========
    
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
    
    # ========== FRONTEND ==========
    
    @app.route('/', methods=['GET'])
    def index():
        """Serve frontend"""
        return send_from_directory(app.static_folder, 'index.html')
    
    # ========== AUTH ROUTES ==========
    
    @app.route('/api/register', methods=['POST'])
    def register():
        """Registra un nuovo utente"""
        data = request.get_json()
        
        if not data.get('email') or not data.get('password'):
            return jsonify({'error': 'Email e password richiesti'}), 400
        
        if User.query.filter_by(email=data['email']).first():
            return jsonify({'error': 'Email già registrata'}), 400
        
        user = User(
            email=data['email'],
            password_hash=generate_password_hash(data['password'])
        )
        
        # Se fornite, salva anche credenziali Garmin
        if data.get('garmin_email') and data.get('garmin_password'):
            user.garmin_email = data['garmin_email']
            user.set_garmin_password(data['garmin_password'], app.config['ENCRYPTION_KEY'])
        
        db.session.add(user)
        db.session.commit()
        
        return jsonify({'message': 'Utente creato', 'user_id': user.id}), 201
    
    @app.route('/api/login', methods=['POST'])
    def login():
        """Login e ottieni token JWT"""
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
                'last_sync': user.last_sync.isoformat() if user.last_sync else None
            }
        })
    
    # ========== GARMIN CONFIG ==========
    
    @app.route('/api/garmin/connect', methods=['POST'])
    @token_required
    def connect_garmin(current_user):
        """Collega account Garmin"""
        data = request.get_json()
        
        if not data.get('garmin_email') or not data.get('garmin_password'):
            return jsonify({'error': 'Credenziali Garmin richieste'}), 400
        
        # Verifica credenziali
        try:
            from garminconnect import Garmin
            client = Garmin(data['garmin_email'], data['garmin_password'])
            client.login()
        except Exception as e:
            return jsonify({'error': f'Login Garmin fallito: {str(e)}'}), 400
        
        # Salva credenziali
        current_user.garmin_email = data['garmin_email']
        current_user.set_garmin_password(data['garmin_password'], app.config['ENCRYPTION_KEY'])
        db.session.commit()
        
        return jsonify({'message': 'Account Garmin collegato'})
    
    @app.route('/api/garmin/disconnect', methods=['POST'])
    @token_required
    def disconnect_garmin(current_user):
        """Scollega account Garmin"""
        current_user.garmin_email = None
        current_user.garmin_password_encrypted = None
        db.session.commit()
        return jsonify({'message': 'Account Garmin scollegato'})
    
    # ========== SYNC ==========
    
    @app.route('/api/sync', methods=['POST'])
    @token_required
    def sync_now(current_user):
        """Sincronizza dati Garmin ora"""
        if not current_user.garmin_email:
            return jsonify({'error': 'Account Garmin non collegato'}), 400
        
        days_back = request.get_json().get('days_back', 7) if request.get_json() else 7
        
        service = GarminSyncService(app.config['ENCRYPTION_KEY'])
        result = service.sync_user(current_user, days_back=days_back)
        
        return jsonify(result)
    
    # ========== METRICS ==========
    
    @app.route('/api/metrics/today', methods=['GET'])
    @token_required
    def get_today_metrics(current_user):
        """Ottieni metriche di oggi"""
        metric = DailyMetric.query.filter_by(
            user_id=current_user.id,
            date=date.today()
        ).first()
        
        if not metric:
            return jsonify({'message': 'Nessun dato per oggi. Esegui sync.'}), 404
        
        return jsonify(_metric_to_dict(metric))
    
    @app.route('/api/metrics/range', methods=['GET'])
    @token_required
    def get_metrics_range(current_user):
        """Ottieni metriche per un range di date"""
        start = request.args.get('start', (date.today() - timedelta(days=7)).isoformat())
        end = request.args.get('end', date.today().isoformat())
        
        metrics = DailyMetric.query.filter(
            DailyMetric.user_id == current_user.id,
            DailyMetric.date >= start,
            DailyMetric.date <= end
        ).order_by(DailyMetric.date.desc()).all()
        
        return jsonify([_metric_to_dict(m) for m in metrics])
    
    @app.route('/api/metrics/summary', methods=['GET'])
    @token_required
    def get_summary(current_user):
        """Ottieni summary settimanale"""
        week_ago = date.today() - timedelta(days=7)
        
        metrics = DailyMetric.query.filter(
            DailyMetric.user_id == current_user.id,
            DailyMetric.date >= week_ago
        ).order_by(DailyMetric.date.desc()).all()
        
        if not metrics:
            return jsonify({'message': 'Nessun dato disponibile'}), 404
        
        # Calcola medie
        recovery_scores = [m.recovery_score for m in metrics if m.recovery_score]
        strain_scores = [m.strain_score for m in metrics if m.strain_score]
        sleep_scores = [m.sleep_performance for m in metrics if m.sleep_performance]
        sleep_hours = [m.sleep_seconds / 3600 for m in metrics if m.sleep_seconds]
        
        return jsonify({
            'period': {
                'start': week_ago.isoformat(),
                'end': date.today().isoformat(),
                'days_with_data': len(metrics)
            },
            'averages': {
                'recovery': round(sum(recovery_scores) / len(recovery_scores), 1) if recovery_scores else None,
                'strain': round(sum(strain_scores) / len(strain_scores), 1) if strain_scores else None,
                'sleep_performance': round(sum(sleep_scores) / len(sleep_scores), 1) if sleep_scores else None,
                'sleep_hours': round(sum(sleep_hours) / len(sleep_hours), 1) if sleep_hours else None
            },
            'today': _metric_to_dict(metrics[0]) if metrics else None
        })
    
    # ========== ACTIVITIES ==========
    
    @app.route('/api/activities', methods=['GET'])
    @token_required
    def get_activities(current_user):
        """Ottieni lista attività"""
        limit = request.args.get('limit', 20, type=int)
        
        activities = Activity.query.filter_by(
            user_id=current_user.id
        ).order_by(Activity.start_time.desc()).limit(limit).all()
        
        return jsonify([{
            'id': a.id,
            'garmin_id': a.garmin_activity_id,
            'name': a.activity_name,
            'type': a.activity_type,
            'start_time': a.start_time.isoformat() if a.start_time else None,
            'duration_minutes': round(a.duration_seconds / 60, 1) if a.duration_seconds else None,
            'distance_km': round(a.distance_meters / 1000, 2) if a.distance_meters else None,
            'calories': a.calories,
            'avg_hr': a.avg_hr,
            'max_hr': a.max_hr,
            'strain': a.strain_score,
            'aerobic_effect': a.aerobic_effect,
            'anaerobic_effect': a.anaerobic_effect
        } for a in activities])
    
    # ========== HEALTH CHECK ==========
    
    @app.route('/api/health', methods=['GET'])
    def health_check():
        """Health check per Railway"""
        return jsonify({
            'status': 'ok',
            'timestamp': datetime.utcnow().isoformat()
        })
    
    @app.route('/api', methods=['GET'])
    def api_info():
        """API info"""
        return jsonify({
            'app': 'Garmin WHOOP',
            'version': '1.0.0',
            'endpoints': [
                'POST /api/register',
                'POST /api/login',
                'POST /api/garmin/connect',
                'POST /api/sync',
                'GET /api/metrics/today',
                'GET /api/metrics/range',
                'GET /api/metrics/summary',
                'GET /api/activities'
            ]
        })
    
    return app


def _metric_to_dict(m: DailyMetric) -> dict:
    """Converte DailyMetric in dict"""
    return {
        'date': m.date.isoformat(),
        'scores': {
            'recovery': m.recovery_score,
            'strain': m.strain_score,
            'sleep_performance': m.sleep_performance
        },
        'heart': {
            'resting_hr': m.resting_hr,
            'min_hr': m.min_hr,
            'max_hr': m.max_hr,
            'hrv_last_night': m.hrv_last_night
        },
        'body_battery': {
            'high': m.body_battery_high,
            'low': m.body_battery_low,
            'charged': m.body_battery_charged,
            'drained': m.body_battery_drained
        },
        'sleep': {
            'total_hours': round(m.sleep_seconds / 3600, 1) if m.sleep_seconds else None,
            'deep_hours': round(m.deep_sleep_seconds / 3600, 1) if m.deep_sleep_seconds else None,
            'light_hours': round(m.light_sleep_seconds / 3600, 1) if m.light_sleep_seconds else None,
            'rem_hours': round(m.rem_sleep_seconds / 3600, 1) if m.rem_sleep_seconds else None,
            'awake_hours': round(m.awake_seconds / 3600, 1) if m.awake_seconds else None,
            'score': m.sleep_score,
            'start': m.sleep_start.strftime('%H:%M') if m.sleep_start else None,
            'end': m.sleep_end.strftime('%H:%M') if m.sleep_end else None
        },
        'stress': {
            'average': m.stress_avg,
            'max': m.stress_max
        },
        'activity': {
            'steps': m.steps,
            'calories': m.total_calories,
            'active_calories': m.active_calories,
            'distance_km': round(m.distance_meters / 1000, 2) if m.distance_meters else None,
            'moderate_minutes': m.moderate_intensity_minutes,
            'vigorous_minutes': m.vigorous_intensity_minutes
        },
        'respiration': {
            'average': m.avg_respiration,
            'min': m.min_respiration,
            'max': m.max_respiration
        },
        'spo2': {
            'average': m.avg_spo2,
            'min': m.min_spo2
        }
    }


# Entry point
app = create_app()

if __name__ == '__main__':
    app.run(debug=True, port=5000)