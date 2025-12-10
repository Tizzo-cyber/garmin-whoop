"""
Garmin WHOOP - Flask App with AI Coaches
Version: 2.8.1 - Pace with negative values for improvement - 2024-12-07
"""

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
from functools import wraps
from sqlalchemy import or_
import jwt
import os

import json

from config import Config
from app.models import db, User, DailyMetric, Activity, SyncLog, ChatMessage, UserMemory, FatigueLog, WeeklyCheck
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
        
        # Auto-migration: aggiungi nuove colonne se non esistono
        try:
            from sqlalchemy import text
            migrations = [
                "ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS fitness_age INTEGER",
                "ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS race_time_5k INTEGER",
                "ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS race_time_10k INTEGER",
                "ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS race_time_half INTEGER",
                "ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS race_time_marathon INTEGER",
            ]
            for sql in migrations:
                try:
                    db.session.execute(text(sql))
                except Exception:
                    pass  # Colonna già esiste o altro errore non critico
            db.session.commit()
            print("[OK] Auto-migration completata")
        except Exception as e:
            print(f"[WARN] Auto-migration warning: {e}")
    
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
            password_hash=generate_password_hash(data['password']),
            name=data.get('name'),
            birth_year=data.get('birth_year')
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
                'name': user.name,
                'birth_year': user.birth_year,
                'garmin_connected': bool(user.garmin_email),
                'last_sync': user.last_sync.isoformat() if user.last_sync else None
            }
        })
    
    @app.route('/api/profile', methods=['PUT'])
    @token_required
    def update_profile(current_user):
        """Aggiorna profilo utente"""
        data = request.get_json()
        if data.get('name'):
            current_user.name = data['name']
        if data.get('birth_year'):
            current_user.birth_year = data['birth_year']
        if data.get('sport_goals'):
            current_user.sport_goals = data['sport_goals']
        db.session.commit()
        return jsonify({'message': 'Profilo aggiornato'})
    
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
        
        data = request.get_json() or {}
        days_back = data.get('days_back', 7)
        offset_days = data.get('offset_days', 0)  # Per sync a blocchi
        
        service = GarminSyncService(app.config['ENCRYPTION_KEY'])
        result = service.sync_user(current_user, days_back=days_back, offset_days=offset_days)
        
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
        """Ottieni summary con tutti i dati"""
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
                'stress_avg': safe_avg([m.stress_avg for m in metrics]),
                'hrv': safe_avg([m.hrv_last_night for m in metrics]),
                'body_battery_high': safe_avg([m.body_battery_high for m in metrics]),
                'body_battery_low': safe_avg([m.body_battery_low for m in metrics]),
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
            'today': _metric_to_dict(metrics[0]) if metrics else None
        })
    
    @app.route('/api/metrics/trend', methods=['GET'])
    @token_required
    def get_trend(current_user):
        """Ottieni trend età biologica e altre metriche"""
        days = request.args.get('days', 90, type=int)
        start_date = date.today() - timedelta(days=days)
        
        metrics = DailyMetric.query.filter(
            DailyMetric.user_id == current_user.id,
            DailyMetric.date >= start_date
        ).order_by(DailyMetric.date.asc()).all()
        
        trend_data = [{
            'date': m.date.isoformat(),
            'biological_age': m.biological_age,
            'recovery': m.recovery_score,
            'strain': m.strain_score,
            'sleep_hours': round(m.sleep_seconds / 3600, 1) if m.sleep_seconds else None,
            'stress': m.stress_avg,
            'hrv': m.hrv_last_night,
            'rhr': m.resting_hr,
            'body_battery_high': m.body_battery_high,
            'body_battery_low': m.body_battery_low,
            'steps': m.steps,
            'vo2_max': m.vo2_max,
        } for m in metrics]
        
        # Calculate pace of aging
        pace = None
        if len(metrics) >= 30:
            recent = [m.biological_age for m in metrics[-15:] if m.biological_age]
            older = [m.biological_age for m in metrics[:15] if m.biological_age]
            if recent and older:
                pace = round((sum(recent)/len(recent) - sum(older)/len(older)) * 12, 1)
        
        return jsonify({
            'data': trend_data,
            'real_age': current_user.get_real_age(),
            'pace_of_aging': pace
        })
    
    # ========== INTRADAY DATA ==========
    
    @app.route('/api/intraday/body-battery', methods=['GET'])
    @token_required
    def get_body_battery_intraday(current_user):
        """Ottieni dati intraday Body Battery"""
        from garminconnect import Garmin
        
        day = request.args.get('date', date.today().isoformat())
        
        if not current_user.garmin_email or not current_user.garmin_password_encrypted:
            return jsonify({'error': 'Garmin non connesso'}), 400
        
        try:
            # Decrypt e connetti
            from cryptography.fernet import Fernet
            fernet = Fernet(app.config['ENCRYPTION_KEY'])
            password = fernet.decrypt(current_user.garmin_password_encrypted.encode()).decode()
            
            client = Garmin(current_user.garmin_email, password)
            client.login()
            
            # Ottieni dati body battery
            bb_data = client.get_body_battery(day)
            
            if not bb_data:
                return jsonify({'data': [], 'date': day})
            
            # Estrai i datapoint
            datapoints = []
            if isinstance(bb_data, list):
                for item in bb_data:
                    if 'bodyBatteryValuesArray' in item:
                        for val in item['bodyBatteryValuesArray']:
                            if val and len(val) >= 2:
                                ts = val[0]  # timestamp in ms
                                value = val[1]  # body battery value
                                if value is not None:
                                    datapoints.append({
                                        'timestamp': ts,
                                        'time': datetime.fromtimestamp(ts/1000).strftime('%H:%M'),
                                        'value': value
                                    })
            elif isinstance(bb_data, dict):
                if 'bodyBatteryValuesArray' in bb_data:
                    for val in bb_data['bodyBatteryValuesArray']:
                        if val and len(val) >= 2:
                            ts = val[0]
                            value = val[1]
                            if value is not None:
                                datapoints.append({
                                    'timestamp': ts,
                                    'time': datetime.fromtimestamp(ts/1000).strftime('%H:%M'),
                                    'value': value
                                })
            
            # Ordina per timestamp
            datapoints.sort(key=lambda x: x['timestamp'])
            
            return jsonify({
                'data': datapoints,
                'date': day,
                'min': min([d['value'] for d in datapoints]) if datapoints else None,
                'max': max([d['value'] for d in datapoints]) if datapoints else None
            })
            
        except Exception as e:
            return jsonify({'error': str(e), 'data': []}), 500
    
    @app.route('/api/intraday/stress', methods=['GET'])
    @token_required  
    def get_stress_intraday(current_user):
        """Ottieni dati intraday Stress"""
        from garminconnect import Garmin
        
        day = request.args.get('date', date.today().isoformat())
        
        if not current_user.garmin_email or not current_user.garmin_password_encrypted:
            return jsonify({'error': 'Garmin non connesso'}), 400
        
        try:
            from cryptography.fernet import Fernet
            fernet = Fernet(app.config['ENCRYPTION_KEY'])
            password = fernet.decrypt(current_user.garmin_password_encrypted.encode()).decode()
            
            client = Garmin(current_user.garmin_email, password)
            client.login()
            
            stress_data = client.get_stress_data(day)
            
            datapoints = []
            if stress_data and 'stressValuesArray' in stress_data:
                for val in stress_data['stressValuesArray']:
                    if val and len(val) >= 2:
                        ts = val[0]
                        value = val[1]
                        if value is not None and value >= 0:  # -1 = no data
                            datapoints.append({
                                'timestamp': ts,
                                'time': datetime.fromtimestamp(ts/1000).strftime('%H:%M'),
                                'value': value
                            })
            
            datapoints.sort(key=lambda x: x['timestamp'])
            
            return jsonify({
                'data': datapoints,
                'date': day,
                'avg': round(sum(d['value'] for d in datapoints) / len(datapoints)) if datapoints else None
            })
            
        except Exception as e:
            return jsonify({'error': str(e), 'data': []}), 500
    
    # ========== ACTIVITIES ==========
    
    @app.route('/api/activities', methods=['GET'])
    @token_required
    def get_activities(current_user):
        """Ottieni lista attività"""
        days = request.args.get('days', 7, type=int)
        limit = request.args.get('limit', 50, type=int)
        since = datetime.utcnow() - timedelta(days=days)
        
        activities = Activity.query.filter(
            Activity.user_id == current_user.id,
            Activity.start_time >= since
        ).order_by(Activity.start_time.desc()).limit(limit).all()
        
        return jsonify([{
            'id': a.id,
            'garmin_id': a.garmin_activity_id,
            'activity_name': a.activity_name,
            'activity_type': a.activity_type,
            'start_time': a.start_time.isoformat() if a.start_time else None,
            'duration_seconds': a.duration_seconds,
            'distance_meters': a.distance_meters,
            'calories': a.calories,
            'avg_hr': a.avg_hr,
            'max_hr': a.max_hr,
            'strain_score': a.strain_score,
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
    
    @app.route('/api/metrics/period', methods=['GET'])
    @token_required
    def get_period_metrics(current_user):
        """Ottieni metriche per periodo: day, week, month, year + 3 precedenti"""
        period_type = request.args.get('type', 'week')
        offset = request.args.get('offset', 0, type=int)
        
        today = date.today()
        
        def get_period_range(ptype, off):
            """Calcola range date per un periodo"""
            if ptype == 'day':
                target_date = today + timedelta(days=off)
                return target_date, target_date, target_date.strftime('%d %b')
            elif ptype == 'week':
                start_of_week = today - timedelta(days=today.weekday())
                start_date = start_of_week + timedelta(weeks=off)
                end_date = start_date + timedelta(days=6)
                return start_date, end_date, f"{start_date.strftime('%d %b')} - {end_date.strftime('%d %b')}"
            elif ptype == 'month':
                target_month = today.month + off
                target_year = today.year
                while target_month < 1:
                    target_month += 12
                    target_year -= 1
                while target_month > 12:
                    target_month -= 12
                    target_year += 1
                start_date = date(target_year, target_month, 1)
                if target_month == 12:
                    end_date = date(target_year + 1, 1, 1) - timedelta(days=1)
                else:
                    end_date = date(target_year, target_month + 1, 1) - timedelta(days=1)
                return start_date, end_date, start_date.strftime('%B %Y')
            else:  # year
                target_year = today.year + off
                return date(target_year, 1, 1), date(target_year, 12, 31), str(target_year)
        
        def calc_metrics(start_date, end_date):
            """Calcola metriche per un range di date"""
            metrics = DailyMetric.query.filter(
                DailyMetric.user_id == current_user.id,
                DailyMetric.date >= start_date,
                DailyMetric.date <= end_date
            ).all()
            
            activities = Activity.query.filter(
                Activity.user_id == current_user.id,
                Activity.start_time >= datetime.combine(start_date, datetime.min.time()),
                Activity.start_time <= datetime.combine(end_date, datetime.max.time())
            ).all()
            
            def avg(lst):
                vals = [x for x in lst if x is not None]
                return round(sum(vals)/len(vals), 1) if vals else None
            
            def total(lst):
                vals = [x for x in lst if x is not None]
                return sum(vals) if vals else 0
            
            return {
                'days_with_data': len(metrics),
                'rhr': avg([m.resting_hr for m in metrics]),
                'hrv': avg([m.hrv_last_night for m in metrics]),
                'sleep_hours': avg([m.sleep_seconds/3600 if m.sleep_seconds else None for m in metrics]),
                'sleep_score': avg([m.sleep_score for m in metrics]),
                'deep_sleep_min': avg([m.deep_sleep_seconds/60 if m.deep_sleep_seconds else None for m in metrics]),
                'rem_sleep_min': avg([m.rem_sleep_seconds/60 if m.rem_sleep_seconds else None for m in metrics]),
                'stress_avg': avg([m.stress_avg for m in metrics]),
                'steps_avg': avg([m.steps for m in metrics]),
                'steps_total': total([m.steps for m in metrics]),
                'calories_total': total([m.total_calories for m in metrics]),
                'active_calories': total([m.active_calories for m in metrics]),
                'distance_km': round(total([m.distance_meters for m in metrics]) / 1000, 1) if metrics else 0,
                'floors': total([m.floors_ascended for m in metrics]),
                'moderate_min': total([m.moderate_intensity_minutes for m in metrics]),
                'vigorous_min': total([m.vigorous_intensity_minutes for m in metrics]),
                'body_battery_high': avg([m.body_battery_high for m in metrics]),
                'body_battery_low': avg([m.body_battery_low for m in metrics]),
                'recovery': avg([m.recovery_score for m in metrics]),
                'strain': avg([m.strain_score for m in metrics]),
                'spo2': avg([m.avg_spo2 for m in metrics]),
                'activity_count': len(activities),
                'activity_duration_min': round(sum([a.duration_seconds/60 for a in activities if a.duration_seconds])),
                'activity_calories': sum([a.calories for a in activities if a.calories]),
            }
        
        # Periodo corrente
        start_date, end_date, label = get_period_range(period_type, offset)
        current_metrics = calc_metrics(start_date, end_date)
        
        # 3 periodi precedenti per confronto
        previous = []
        for i in range(1, 4):
            prev_start, prev_end, prev_label = get_period_range(period_type, offset - i)
            prev_metrics = calc_metrics(prev_start, prev_end)
            previous.append({
                'label': prev_label,
                'start': prev_start.isoformat(),
                'end': prev_end.isoformat(),
                'metrics': prev_metrics
            })
        
        # Dati giornalieri per il periodo corrente
        daily_metrics = DailyMetric.query.filter(
            DailyMetric.user_id == current_user.id,
            DailyMetric.date >= start_date,
            DailyMetric.date <= end_date
        ).order_by(DailyMetric.date.asc()).all()
        
        # Attività del periodo corrente
        activities = Activity.query.filter(
            Activity.user_id == current_user.id,
            Activity.start_time >= datetime.combine(start_date, datetime.min.time()),
            Activity.start_time <= datetime.combine(end_date, datetime.max.time())
        ).order_by(Activity.start_time.desc()).all()
        
        data = {
            'period': {
                'type': period_type,
                'label': label,
                'start': start_date.isoformat(),
                'end': end_date.isoformat(),
                'days_total': (end_date - start_date).days + 1,
                'days_with_data': current_metrics['days_with_data']
            },
            'current_time': datetime.now().strftime('%H:%M'),
            'current_hour': datetime.now().hour,
            'metrics': current_metrics,
            'previous': previous,
            'daily': [{
                'date': m.date.isoformat(),
                'day': m.date.strftime('%a'),
                'steps': m.steps,
                'calories': m.total_calories,
                'sleep_hours': round(m.sleep_seconds/3600, 1) if m.sleep_seconds else None,
                'stress': m.stress_avg,
                'rhr': m.resting_hr,
                'recovery': m.recovery_score,
                'strain': m.strain_score,
                'body_battery_high': m.body_battery_high,
            } for m in daily_metrics],
            'activities': [{
                'name': a.activity_name or a.activity_type,
                'type': a.activity_type,
                'date': a.start_time.strftime('%d/%m %H:%M') if a.start_time else None,
                'duration_min': round(a.duration_seconds / 60) if a.duration_seconds else None,
                'distance_km': round(a.distance_meters / 1000, 2) if a.distance_meters else None,
                'calories': a.calories,
            } for a in activities[:10]],
            'activity_count': len(activities)
        }
        
        return jsonify(data)
    
    @app.route('/api/metrics/healthspan', methods=['GET'])
    @token_required
    def get_healthspan_age(current_user):
        """Calcola Healthspan su 6 mesi di dati - FORMULA NORMALIZZATA"""
        import statistics
        
        today = date.today()
        six_months_ago = today - timedelta(days=180)
        
        metrics = DailyMetric.query.filter(
            DailyMetric.user_id == current_user.id,
            DailyMetric.date >= six_months_ago
        ).order_by(DailyMetric.date.desc()).all()
        
        activities = Activity.query.filter(
            Activity.user_id == current_user.id,
            Activity.start_time >= datetime.combine(six_months_ago, datetime.min.time())
        ).all()
        
        if len(metrics) < 30:
            return jsonify({'error': 'Servono almeno 30 giorni di dati', 'days': len(metrics)}), 400
        
        real_age = current_user.get_real_age()
        
        def avg(lst):
            vals = [x for x in lst if x is not None]
            return sum(vals)/len(vals) if vals else None
        
        def stdev(lst):
            vals = [x for x in lst if x is not None]
            return statistics.stdev(vals) if len(vals) >= 2 else 0
        
        # ═══ CALCOLO METRICHE NORMALIZZATE ═══
        # Ogni metrica dà un impatto da -1 a +1
        # Poi normalizziamo per chi ha meno dati
        
        impacts = {}
        raw_impacts = {}  # Per il calcolo normalizzato
        MAX_YEARS = 8  # Range finale ±8 anni
        
        # 1. DURATA SONNO
        sleep_hours = [m.sleep_seconds/3600 for m in metrics if m.sleep_seconds]
        avg_sleep = avg(sleep_hours)
        if avg_sleep:
            if 7 <= avg_sleep <= 8.5:
                raw_impacts['sleep_duration'] = -0.4  # Premio per sonno ottimale
                impacts['sleep_duration'] = {'value': round(avg_sleep, 1), 'impact': 0, 'unit': 'h', 'status': '🟢'}
            elif avg_sleep >= 6 or avg_sleep <= 9.5:
                raw = min(0.3, abs(avg_sleep - 7.5) * 0.2)
                raw_impacts['sleep_duration'] = raw
                impacts['sleep_duration'] = {'value': round(avg_sleep, 1), 'impact': 0, 'unit': 'h', 'status': '🟡'}
            else:
                raw = min(0.5, abs(avg_sleep - 7.5) * 0.25)
                raw_impacts['sleep_duration'] = raw
                impacts['sleep_duration'] = {'value': round(avg_sleep, 1), 'impact': 0, 'unit': 'h', 'status': '🔴'}
        
        # 2. ZONE HR 1-3 (cardio moderato) - PREMI > penalità
        zone_low = sum([(a.hr_zone_1 or 0) + (a.hr_zone_2 or 0) + (a.hr_zone_3 or 0) for a in activities]) / 60
        weekly_low = zone_low / 26
        if weekly_low > 0 or len(activities) > 0:
            if weekly_low >= 150:
                raw_impacts['zone_low'] = -0.8  # Grande premio
                impacts['zone_low'] = {'value': round(weekly_low), 'impact': 0, 'unit': 'min/sett', 'status': '🟢'}
            elif weekly_low >= 100:
                raw_impacts['zone_low'] = -0.5
                impacts['zone_low'] = {'value': round(weekly_low), 'impact': 0, 'unit': 'min/sett', 'status': '🟢'}
            elif weekly_low >= 50:
                raw_impacts['zone_low'] = -0.2
                impacts['zone_low'] = {'value': round(weekly_low), 'impact': 0, 'unit': 'min/sett', 'status': '🟡'}
            else:
                raw_impacts['zone_low'] = 0.25  # Penalità moderata
                impacts['zone_low'] = {'value': round(weekly_low), 'impact': 0, 'unit': 'min/sett', 'status': '🔴'}
        
        # 3. ZONE HR 4-5 (cardio intenso) - PREMI > penalità
        zone_high = sum([(a.hr_zone_4 or 0) + (a.hr_zone_5 or 0) for a in activities]) / 60
        weekly_high = zone_high / 26
        if weekly_high > 0 or len(activities) > 0:
            if weekly_high >= 75:
                raw_impacts['zone_high'] = -0.8  # Grande premio
                impacts['zone_high'] = {'value': round(weekly_high), 'impact': 0, 'unit': 'min/sett', 'status': '🟢'}
            elif weekly_high >= 50:
                raw_impacts['zone_high'] = -0.5
                impacts['zone_high'] = {'value': round(weekly_high), 'impact': 0, 'unit': 'min/sett', 'status': '🟢'}
            elif weekly_high >= 25:
                raw_impacts['zone_high'] = -0.2
                impacts['zone_high'] = {'value': round(weekly_high), 'impact': 0, 'unit': 'min/sett', 'status': '🟡'}
            else:
                raw_impacts['zone_high'] = 0.25  # Penalità moderata
                impacts['zone_high'] = {'value': round(weekly_high), 'impact': 0, 'unit': 'min/sett', 'status': '🔴'}
        
        # 4. ATTIVITÀ FORZA - PREMI > penalità
        strength_activities = [a for a in activities if a.activity_type and 'strength' in a.activity_type.lower()]
        strength_weekly = len(strength_activities) / 26
        if len(activities) > 0:  # Solo se abbiamo attività
            if strength_weekly >= 2:
                raw_impacts['strength'] = -0.6  # Grande premio
                impacts['strength'] = {'value': round(strength_weekly, 1), 'impact': 0, 'unit': 'x/sett', 'status': '🟢'}
            elif strength_weekly >= 1:
                raw_impacts['strength'] = -0.3
                impacts['strength'] = {'value': round(strength_weekly, 1), 'impact': 0, 'unit': 'x/sett', 'status': '🟡'}
            else:
                raw_impacts['strength'] = 0.2  # Penalità moderata
                impacts['strength'] = {'value': round(strength_weekly, 1), 'impact': 0, 'unit': 'x/sett', 'status': '🔴'}
        
        # 5. PASSI GIORNALIERI - PREMI > penalità
        steps = [m.steps for m in metrics if m.steps]
        avg_steps = avg(steps)
        if avg_steps:
            if avg_steps >= 12000:
                raw_impacts['steps'] = -0.7  # Grande premio
                impacts['steps'] = {'value': round(avg_steps), 'impact': 0, 'unit': '/giorno', 'status': '🟢'}
            elif avg_steps >= 10000:
                raw_impacts['steps'] = -0.5
                impacts['steps'] = {'value': round(avg_steps), 'impact': 0, 'unit': '/giorno', 'status': '🟢'}
            elif avg_steps >= 8000:
                raw_impacts['steps'] = -0.3
                impacts['steps'] = {'value': round(avg_steps), 'impact': 0, 'unit': '/giorno', 'status': '🟢'}
            elif avg_steps >= 6000:
                raw_impacts['steps'] = -0.1
                impacts['steps'] = {'value': round(avg_steps), 'impact': 0, 'unit': '/giorno', 'status': '🟡'}
            elif avg_steps >= 4000:
                raw_impacts['steps'] = 0.15  # Leggera penalità
                impacts['steps'] = {'value': round(avg_steps), 'impact': 0, 'unit': '/giorno', 'status': '🟡'}
            else:
                raw_impacts['steps'] = 0.35  # Sedentarietà penalizzata di più
                impacts['steps'] = {'value': round(avg_steps), 'impact': 0, 'unit': '/giorno', 'status': '🔴'}
        
        # 6. RHR
        rhr_values = [m.resting_hr for m in metrics if m.resting_hr]
        avg_rhr = avg(rhr_values)
        if avg_rhr:
            if avg_rhr < 55:
                raw_impacts['rhr'] = -0.5  # Eccellente
                status = '🟢'
            elif avg_rhr < 60:
                raw_impacts['rhr'] = -0.3  # Ottimo
                status = '🟢'
            elif avg_rhr < 70:
                raw_impacts['rhr'] = 0  # Normale
                status = '🟡'
            elif avg_rhr < 80:
                raw_impacts['rhr'] = 0.25  # Alto
                status = '🔴'
            else:
                raw_impacts['rhr'] = 0.4  # Molto alto
                status = '🔴'
            impacts['rhr'] = {'value': round(avg_rhr), 'impact': 0, 'unit': 'bpm', 'status': status}
        
        # 7. VO2 MAX (se disponibile)
        vo2_values = [m.vo2_max for m in metrics if m.vo2_max]
        avg_vo2 = avg(vo2_values)
        if avg_vo2:
            if avg_vo2 > 55:
                raw_impacts['vo2_max'] = -0.7  # Elite
                status = '🟢'
            elif avg_vo2 > 50:
                raw_impacts['vo2_max'] = -0.5  # Eccellente
                status = '🟢'
            elif avg_vo2 > 45:
                raw_impacts['vo2_max'] = -0.3  # Buono
                status = '🟢'
            elif avg_vo2 > 40:
                raw_impacts['vo2_max'] = 0  # Medio
                status = '🟡'
            elif avg_vo2 > 35:
                raw_impacts['vo2_max'] = 0.2  # Sotto media
                status = '🟡'
            else:
                raw_impacts['vo2_max'] = 0.4  # Basso
                status = '🔴'
            impacts['vo2_max'] = {'value': round(avg_vo2, 1), 'impact': 0, 'unit': 'ml/kg/min', 'status': status}
        
        # ═══ CALCOLO NORMALIZZATO ═══
        if len(raw_impacts) < 2:
            return jsonify({'error': 'Dati insufficienti per il calcolo', 'metrics_found': len(raw_impacts)}), 400
        
        # Media degli impatti normalizzati
        avg_impact = sum(raw_impacts.values()) / len(raw_impacts)
        total_impact = avg_impact * MAX_YEARS
        
        # Aggiorna gli impatti visualizzati (scalati per numero metriche)
        scale = MAX_YEARS / len(raw_impacts)
        for key in impacts:
            if key in raw_impacts:
                impacts[key]['impact'] = round(raw_impacts[key] * scale, 1)
        
        healthspan_age = round(real_age + total_impact, 1)
        
        # Pace of aging (ultimi 30gg vs media)
        recent_metrics = [m for m in metrics if m.date >= today - timedelta(days=30)]
        pace = None
        pace_status = '⚪'
        pace_label = 'Dati insufficienti'
        
        if len(recent_metrics) >= 7:
            recent_raw = {}
            
            recent_sleep = avg([m.sleep_seconds/3600 for m in recent_metrics if m.sleep_seconds])
            if recent_sleep:
                if 7 <= recent_sleep <= 8.5:
                    recent_raw['sleep'] = -0.4
                else:
                    recent_raw['sleep'] = min(0.4, abs(recent_sleep - 7.5) * 0.2)
            
            recent_steps = avg([m.steps for m in recent_metrics if m.steps])
            if recent_steps:
                if recent_steps >= 10000: recent_raw['steps'] = -0.5
                elif recent_steps >= 8000: recent_raw['steps'] = -0.3
                elif recent_steps >= 6000: recent_raw['steps'] = -0.1
                elif recent_steps >= 4000: recent_raw['steps'] = 0.15
                else: recent_raw['steps'] = 0.35
            
            recent_rhr = avg([m.resting_hr for m in recent_metrics if m.resting_hr])
            if recent_rhr:
                if recent_rhr < 55: recent_raw['rhr'] = -0.5
                elif recent_rhr < 60: recent_raw['rhr'] = -0.3
                elif recent_rhr < 70: recent_raw['rhr'] = 0
                elif recent_rhr < 80: recent_raw['rhr'] = 0.25
                else: recent_raw['rhr'] = 0.4
            
            if len(recent_raw) >= 2:
                recent_avg = sum(recent_raw.values()) / len(recent_raw)
                recent_age = real_age + (recent_avg * MAX_YEARS)
                
                # Pace = differenza annualizzata
                age_diff = recent_age - healthspan_age
                pace = round(age_diff * 0.8, 2)  # Scala per visualizzazione
                pace = max(-1.0, min(1.0, pace))  # Range ±1
                
                pace_status = '🟢' if pace < -0.1 else '🟡' if pace <= 0.1 else '🔴'
                pace_label = 'Ringiovanendo' if pace < -0.1 else 'Stabile' if pace <= 0.1 else 'Invecchiamento accelerato'
        
        # Suggerimenti
        suggestions = []
        for key, data in impacts.items():
            if data['status'] == '🔴':
                if key == 'sleep_duration':
                    suggestions.append('😴 Dormi di più: punta a 7-8 ore per notte')
                elif key == 'zone_low':
                    suggestions.append('💚 Aggiungi cardio moderato: camminate, bici, nuoto (150 min/sett)')
                elif key == 'zone_high':
                    suggestions.append('❤️‍🔥 Aggiungi cardio intenso: corsa, HIIT, spinning (75 min/sett)')
                elif key == 'strength':
                    suggestions.append('💪 Aggiungi allenamento forza: 2 sessioni/settimana')
                elif key == 'steps':
                    suggestions.append('👟 Cammina di più: punta a 8000+ passi/giorno')
                elif key == 'rhr':
                    suggestions.append('❤️ Migliora fitness cardiovascolare per abbassare RHR')
        
        return jsonify({
            'healthspan_age': healthspan_age,
            'real_age': real_age,
            'difference': round(healthspan_age - real_age, 1),
            'total_impact': round(total_impact, 1),
            'metrics_used': len(raw_impacts),
            'pace_of_aging': pace,
            'pace_status': pace_status,
            'pace_label': pace_label,
            'days_analyzed': len(metrics),
            'activities_analyzed': len(activities),
            'impacts': impacts,
            'suggestions': suggestions
        })
    
    @app.route('/', methods=['GET'])
    def index():
        """Home page"""
        return send_from_directory(app.static_folder, 'index.html')
    
    # ========== AI CHAT ==========
    
    openai_client = None
    if os.environ.get('OPENAI_API_KEY'):
        from openai import OpenAI
        openai_client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
    
    def _get_sensei_prompt(user, context, memories):
        name = user.name or "atleta"
        age = user.get_real_age()
        memories_text = "\n".join([f"- [{m.category}] {m.content}" for m in memories]) if memories else "Nessuna"
        
        # === TONO DINAMICO BASATO SU ORA ===
        hour = datetime.now().hour
        if hour < 6:
            time_context = f"E' notte fonda. Se {name} e' sveglio, potrebbe avere problemi di sonno."
            time_tone = "Gentile, preoccupato per il riposo"
        elif hour < 12:
            time_context = "E' mattina. Momento ideale per energia e motivazione."
            time_tone = "Energico, motivante, carico"
        elif hour < 18:
            time_context = "E' pomeriggio. Focus su obiettivi e praticita."
            time_tone = "Pratico, concreto, orientato agli obiettivi"
        elif hour < 22:
            time_context = "E' sera. Tempo di bilanci e recupero."
            time_tone = "Riflessivo, bilancio della giornata, preparazione riposo"
        else:
            time_context = "E' tarda sera. Priorita al recupero."
            time_tone = "Calmo, focus sul riposo e recupero"
        
        # === TONO DINAMICO BASATO SU DATI ===
        recovery = context.get('recovery') or 50
        y = context.get('yesterday', {}) or {}
        wellness = context.get('wellness', {}) or {}
        fatigue = wellness.get('fatigue_today') or 5
        strain = y.get('strain') or 10
        
        if recovery < 50 or fatigue >= 7:
            data_tone = "Protettivo, NO pressione, focus recupero. Sconsiglia allenamenti intensi."
            push_level = "BASSO - non spingere, proteggere"
        elif recovery < 70:
            data_tone = "Moderato, ascolto del corpo, attivita leggera OK"
            push_level = "MEDIO - attivita leggera, no esagerare"
        elif recovery >= 85 and fatigue <= 3:
            data_tone = "Carico! Questo e' il momento di spingere. Entusiasmo!"
            push_level = "ALTO - puo' dare il massimo oggi"
        else:
            data_tone = "Equilibrato, allenamento normale, ascolta il corpo"
            push_level = "NORMALE - allenamento standard"
        
        # Helper per formattare tempi gara
        def fmt_race(secs):
            if not secs: return 'N/D'
            h, rem = divmod(int(secs), 3600)
            m, s = divmod(rem, 60)
            return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        
        # Race predictions
        race_text = f"5K: {fmt_race(context.get('race_5k'))} | 10K: {fmt_race(context.get('race_10k'))} | Mezza: {fmt_race(context.get('race_half'))} | Maratona: {fmt_race(context.get('race_marathon'))}"
        
        # Formatta dati di ieri
        yesterday_text = f"""Data: {y.get('date', 'N/D')}
RECUPERO: {y.get('recovery', 'N/D')}% | Strain: {y.get('strain', 'N/D')}/21
SONNO: {y.get('sleep_hours', 'N/D')}h (Score: {y.get('sleep_score', 'N/D')}) | {y.get('sleep_start', '?')}-{y.get('sleep_end', '?')}
  Deep: {y.get('deep_sleep_min', 'N/D')}min | REM: {y.get('rem_sleep_min', 'N/D')}min | Light: {y.get('light_sleep_min', 'N/D')}min | Sveglio: {y.get('awake_min', 'N/D')}min
CUORE: RHR {y.get('rhr', 'N/D')}bpm | HRV {y.get('hrv', 'N/D')}ms | Max {y.get('max_hr', 'N/D')}bpm
STRESS: Media {y.get('stress_avg', 'N/D')} | Max {y.get('stress_max', 'N/D')} | Alto: {y.get('high_stress_min', 'N/D')}min
ATTIVITA: {y.get('steps', 'N/D')} passi | {y.get('distance_km', 'N/D')}km | {y.get('active_calories', 'N/D')}kcal attive | {y.get('floors', 'N/D')} piani
INTENSITA: Moderata {y.get('moderate_min', 'N/D')}min | Vigorosa {y.get('vigorous_min', 'N/D')}min
BODY BATTERY: {y.get('body_battery_low', 'N/D')}-{y.get('body_battery_high', 'N/D')} | +{y.get('body_battery_charged', 'N/D')} -{y.get('body_battery_drained', 'N/D')}
RESPIRO: {y.get('respiration', 'N/D')} resp/min | SpO2: {y.get('spo2', 'N/D')}%""" if y else "Non disponibili"
        
        # Formatta trend
        t = context.get('trend', {})
        trend_text = f"""Sonno: {'+' if (t.get('sleep_change', 0) or 0) >= 0 else ''}{t.get('sleep_change', 0)}h | Recovery: {'+' if (t.get('recovery_change', 0) or 0) >= 0 else ''}{t.get('recovery_change', 0)}% | RHR: {'+' if (t.get('rhr_change', 0) or 0) >= 0 else ''}{t.get('rhr_change', 0)}bpm
HRV: {'+' if (t.get('hrv_change', 0) or 0) >= 0 else ''}{t.get('hrv_change', 0)}ms | Passi: {'+' if (t.get('steps_change', 0) or 0) >= 0 else ''}{t.get('steps_change', 0)} | Stress: {'+' if (t.get('stress_change', 0) or 0) >= 0 else ''}{t.get('stress_change', 0)}""" if t else "Non disponibile"
        
        # Formatta attività recenti
        activities = context.get('recent_activities', [])
        activities_text = "\n".join([
            f"- {a['date']}: {a['name']} | {a['duration_min']}min | {a['distance_km']}km | {a['calories']}kcal | HR {a['avg_hr']}/{a['max_hr']}bpm | Strain {a['strain']} | Aerobico {a['aerobic_effect']} Anaerobico {a['anaerobic_effect']} | Z4:{a['hr_zone_4_min']}min Z5:{a['hr_zone_5_min']}min"
            for a in activities
        ]) if activities else "Nessuna attivita recente"
        
        # Riepilogo settimana
        ws = context.get('week_activity_summary', {})
        week_summary = f"{ws.get('total_activities', 0)} attivita | {round(ws.get('total_duration_min', 0))}min totali | {ws.get('total_distance_km', 0)}km | {ws.get('total_calories', 0)}kcal | Strain medio {ws.get('avg_strain', 'N/D')}" if ws else "N/D"
        
        return f"""Sei SENSEI, preparatore atletico personale di {name}, {age} anni.

!!! REGOLE ASSOLUTE !!!
- MAI markdown (##, **, -, elenchi)
- MAI inventare dati
- Scrivi come parleresti davvero

PERSONALITA: Diretto, pratico, motivante. Chiama sempre {name} per nome.

═══════════════════════════════════════════════════
        REGOLE CONTESTUALI (RISPETTA QUESTE!)
═══════════════════════════════════════════════════
Ora: {context.get('temporal', {}).get('weekday', 'N/D').upper()} ore {context.get('temporal', {}).get('hour', '?')}:00
Giorno {context.get('temporal', {}).get('days_into_week', '?')}/7 della settimana

{context.get('ai_rules', 'Nessuna regola speciale')}
═══════════════════════════════════════════════════

STATO ATTUALE:
- Ultima attivita: {context.get('temporal', {}).get('days_since_activity', 'N/D')} giorni fa ({context.get('temporal', {}).get('last_activity_type', 'N/D')})
- Questa settimana: {context.get('temporal', {}).get('this_week_activities', 0)} attivita
- Streak: {context.get('temporal', {}).get('streak', 0)} giorni consecutivi
- Giorni abituali: {', '.join(context.get('temporal', {}).get('usual_training_days', [])) or 'non definiti'}

TONO: {time_tone}
PUSH LEVEL: {push_level}

COLLEGA: Dr. Sakura (coach mentale) - per temi emotivi/stress rimanda a lei.

--- DATI DI IERI ({y.get('date', 'N/D')}) ---
Recupero: {y.get('recovery', 'N/D')}% | Strain: {y.get('strain', 'N/D')}/21
Sonno: {y.get('sleep_hours', 'N/D')}h (Score: {y.get('sleep_score', 'N/D')})
Cuore: RHR {y.get('rhr', 'N/D')}bpm | HRV {y.get('hrv', 'N/D')}ms
Body Battery: {y.get('body_battery_low', 'N/D')}-{y.get('body_battery_high', 'N/D')}

--- MEDIE 30 GIORNI ---
Eta biologica: {context.get('biological_age', 'N/D')} (reale: {age}) | VO2 Max: {context.get('vo2_max', 'N/D')}
Recovery: {context.get('recovery', 'N/D')}%

--- ATTIVITA RECENTI ---
{activities_text}

--- SENSAZIONI ---
{_format_sensei_wellness(context.get('wellness', {}))}

Max 200 parole. RISPETTA LE REGOLE CONTESTUALI!"""

    def _get_sakura_prompt(user, context, memories):
        name = user.name or "amico"
        age = user.get_real_age()
        memories_text = "\n".join([f"- [{m.category}] {m.content}" for m in memories]) if memories else "Nessuna"
        
        # === TONO DINAMICO BASATO SU ORA ===
        hour = datetime.now().hour
        if hour < 6:
            time_context = f"E' notte fonda. {name} potrebbe avere difficolta a dormire o essere in un momento di riflessione notturna."
            time_tone = "Dolce, sussurrato, accompagnamento nel silenzio"
        elif hour < 12:
            time_context = "E' mattina. Un nuovo giorno da accogliere con presenza."
            time_tone = "Gentile risveglio, apertura, intenzione per la giornata"
        elif hour < 18:
            time_context = "E' pomeriggio. Momento di centratura nel mezzo della giornata."
            time_tone = "Grounding, presenza, pausa consapevole"
        elif hour < 22:
            time_context = "E' sera. Tempo di lasciare andare e prepararsi al riposo."
            time_tone = "Rilascio, gratitudine, chiusura gentile della giornata"
        else:
            time_context = "E' tarda sera. Accompagnamento verso il sonno."
            time_tone = "Calma profonda, preparazione al riposo, quiete"
        
        # === TONO DINAMICO BASATO SU DATI ===
        y = context.get('yesterday', {}) or {}
        wellness = context.get('wellness', {}) or {}
        stress_avg = y.get('stress_avg') or 40
        hrv = y.get('hrv') or 40
        sleep_score = y.get('sleep_score') or 70
        mental_check = wellness.get('sakura_checkin', {}) or {}
        answers = mental_check.get('answers', {}) or {}
        anxiety = answers.get('anxiety') or 2
        stress_reported = answers.get('stress') or 2
        
        # Determina stato emotivo
        if stress_avg > 60 or stress_reported >= 4 or anxiety >= 4:
            emotional_state = "STRESS/ANSIA ELEVATI"
            approach = "Massima empatia, nessuna pressione, tecniche di grounding, respiro"
        elif hrv < 30 or sleep_score < 60:
            emotional_state = "AFFATICAMENTO MENTALE"
            approach = "Compassione, gentilezza, non aggiungere pesi, presenza semplice"
        elif stress_avg < 30 and hrv > 50:
            emotional_state = "EQUILIBRIO BUONO"
            approach = "Gratitudine, espansione, esplorazione interiore, crescita"
        else:
            emotional_state = "STATO NEUTRO"
            approach = "Ascolto attivo, disponibilita, apertura a cio che emerge"
        
        # Formatta attivita recenti (per capire carico e stress)
        activities = context.get('recent_activities', [])
        activities_text = "\n".join([
            f"- {a['date']}: {a['name']} | {a['duration_min']}min | Strain {a['strain']}"
            for a in activities
        ]) if activities else "Nessuna attivita recente"
        
        # Riepilogo settimana
        ws = context.get('week_activity_summary', {})
        week_summary = f"{ws.get('total_activities', 0)} attivita | {round(ws.get('total_duration_min', 0))}min | Strain medio {ws.get('avg_strain', 'N/D')}" if ws else "N/D"
        
        # Formatta dati di ieri (focus benessere)
        yesterday_text = f"""Data: {y.get('date', 'N/D')}
SONNO: {y.get('sleep_hours', 'N/D')}h (Score: {y.get('sleep_score', 'N/D')}) | Ora: {y.get('sleep_start', '?')}-{y.get('sleep_end', '?')}
  Deep: {y.get('deep_sleep_min', 'N/D')}min | REM: {y.get('rem_sleep_min', 'N/D')}min | Sveglio: {y.get('awake_min', 'N/D')}min
RECUPERO: {y.get('recovery', 'N/D')}%
STRESS: Media {y.get('stress_avg', 'N/D')} | Max {y.get('stress_max', 'N/D')}
  Riposo: {y.get('rest_stress_min', 'N/D')}min | Basso: {y.get('low_stress_min', 'N/D')}min | Medio: {y.get('medium_stress_min', 'N/D')}min | Alto: {y.get('high_stress_min', 'N/D')}min
CUORE: HRV {y.get('hrv', 'N/D')}ms | RHR {y.get('rhr', 'N/D')}bpm
BODY BATTERY: Min {y.get('body_battery_low', 'N/D')} - Max {y.get('body_battery_high', 'N/D')} | Caricato +{y.get('body_battery_charged', 'N/D')} | Consumato -{y.get('body_battery_drained', 'N/D')}
RESPIRO: {y.get('respiration', 'N/D')} resp/min | SpO2: {y.get('spo2', 'N/D')}%""" if y else "Non disponibili"
        
        # Formatta trend
        t = context.get('trend', {})
        trend_text = f"""Sonno: {'+' if (t.get('sleep_change', 0) or 0) >= 0 else ''}{t.get('sleep_change', 0)}h | Recovery: {'+' if (t.get('recovery_change', 0) or 0) >= 0 else ''}{t.get('recovery_change', 0)}%
HRV: {'+' if (t.get('hrv_change', 0) or 0) >= 0 else ''}{t.get('hrv_change', 0)}ms | Stress: {'+' if (t.get('stress_change', 0) or 0) >= 0 else ''}{t.get('stress_change', 0)}""" if t else "Non disponibile"
        
        return f"""Sei SAKURA, guida mentale personale di {name}, {age} anni.

!!! REGOLE ASSOLUTE !!!
- MAI markdown (##, **, -, elenchi)
- MAI inventare dati
- Scrivi come parleresti davvero

PERSONALITA: Calma, empatica, femminile, mai giudicante. Chiama sempre {name} per nome.

═══════════════════════════════════════════════════
        REGOLE CONTESTUALI (RISPETTA QUESTE!)
═══════════════════════════════════════════════════
Ora: {context.get('temporal', {}).get('weekday', 'N/D')} ore {context.get('temporal', {}).get('hour', '?')}:00

{context.get('ai_rules', 'Nessuna regola speciale')}
═══════════════════════════════════════════════════

═══════════════════════════════════════════════════
        MEDITAZIONE GUIDATA (se richiesta)
═══════════════════════════════════════════════════
Usa [PAUSA:XX] per pause silenziose (XX = secondi).

CALCOLO DURATA (IMPORTANTE!):
- Parlato: ~25 parole = 30 secondi
- Pausa breve: [PAUSA:10] = 10 sec
- Pausa media: [PAUSA:30] = 30 sec  
- Pausa lunga: [PAUSA:60] = 60 sec

ESEMPIO 5 MINUTI (300 sec):
Intro 40 parole (~50s) + [PAUSA:20] + 
Respiro 30 parole (~35s) + [PAUSA:30] +
Corpo 35 parole (~40s) + [PAUSA:45] +
Silenzio [PAUSA:60] +
Chiusura 25 parole (~30s) = ~310 sec ✓

ESEMPIO 10 MINUTI (600 sec):
- Usa almeno 8-10 segmenti di testo
- Pause totali: ~400 secondi (usa [PAUSA:30], [PAUSA:45], [PAUSA:60])
- Testo totale: ~150-200 parole (~200 sec parlato)

ESEMPIO 15 MINUTI (900 sec):
- Usa almeno 12-15 segmenti
- Pause totali: ~600 secondi
- Includi 2-3 pause lunghe [PAUSA:60] o [PAUSA:90]

STRUTTURA:
1. Accoglienza (posizione, ambiente)
2. Respiro iniziale + [PAUSA:20-30]
3. Rilassamento corpo (scansione) + pause tra zone
4. Visualizzazione/tema + pause lunghe
5. Silenzio profondo [PAUSA:60-90]
6. Ritorno graduale
7. Chiusura dolce

STILE: Frasi brevi... pause naturali... puntini per respirare...
═══════════════════════════════════════════════════

TONO: {time_tone}
STATO RILEVATO: {emotional_state}
APPROCCIO: {approach}

COLLEGA: Dr. Sensei (preparatore atletico) - per temi fisici rimanda a lui.

--- DATI DI IERI ({y.get('date', 'N/D')}) ---
Sonno: {y.get('sleep_hours', 'N/D')}h | Deep {y.get('deep_sleep_min', 'N/D')}min | REM {y.get('rem_sleep_min', 'N/D')}min
Recupero: {y.get('recovery', 'N/D')}% | Stress medio: {y.get('stress_avg', 'N/D')}
HRV: {y.get('hrv', 'N/D')}ms

--- SENSAZIONI RIPORTATE ---
{_format_sakura_wellness(context.get('wellness', {}))}

RISPOSTE NORMALI: Max 200 parole.
MEDITAZIONI: Segui le istruzioni sopra per la durata richiesta (ignora limite parole)."""

    def _fatigue_label(value):
        """Converte valore fatica in etichetta"""
        if value <= 2: return "fresco"
        elif value <= 4: return "leggera fatica"
        elif value <= 6: return "fatica moderata"
        elif value <= 8: return "fatica alta"
        else: return "fatica estrema"

    def _format_sensei_wellness(w):
        """Formatta dati wellness per prompt Sensei"""
        if not w:
            return "Nessun dato riportato dall'utente"
        
        lines = []
        
        if w.get('fatigue_today'):
            lines.append(f"FATICA PERCEPITA OGGI: {w['fatigue_today']}/10 ({w.get('fatigue_today_label', '')})")
        
        if w.get('fatigue_week_avg'):
            lines.append(f"Media settimanale: {w['fatigue_week_avg']}/10")
            if w.get('fatigue_days_high', 0) > 0:
                lines.append(f"[!] Giorni con fatica alta (>=7): {w['fatigue_days_high']}")
        
        if w.get('sensei_checkin'):
            checkin = w['sensei_checkin']
            if checkin.get('is_recent'):
                lines.append(f"\nCHECK-IN FISICO (compilato {checkin['days_ago']} giorni fa):")
                labels = {
                    'energy': ('Energia', False),
                    'soreness': ('Dolori muscolari', True),
                    'performance': ('Performance', False),
                    'recovery': ('Recupero percepito', False),
                    'motivation': ('Motivazione', False),
                    'sleep_quality': ('Qualita sonno', False)
                }
                for key, (label, inverted) in labels.items():
                    if key in checkin['answers']:
                        val = checkin['answers'][key]
                        if inverted:
                            desc = ['nessuno', 'minimo', 'moderato', 'alto', 'estremo'][val-1]
                        else:
                            desc = ['pessimo', 'scarso', 'normale', 'buono', 'ottimo'][val-1]
                        lines.append(f"   - {label}: {val}/5 ({desc})")
        
        return "\n".join(lines) if lines else "Nessun dato riportato"
    
    def _format_sakura_wellness(w):
        """Formatta dati wellness per prompt Sakura"""
        if not w:
            return "Nessun dato riportato dall'utente"
        
        lines = []
        
        if w.get('fatigue_today'):
            lines.append(f"Fatica fisica oggi: {w['fatigue_today']}/10 ({w.get('fatigue_today_label', '')})")
        
        if w.get('sakura_checkin'):
            checkin = w['sakura_checkin']
            if checkin.get('is_recent'):
                lines.append(f"\nCHECK-IN MENTALE (compilato {checkin['days_ago']} giorni fa):")
                labels = {
                    'mood': ('Umore', False),
                    'stress': ('Stress mentale', True),
                    'anxiety': ('Ansia', True),
                    'focus': ('Concentrazione', False),
                    'social': ('Vita sociale', False),
                    'balance': ('Work-life balance', False)
                }
                for key, (label, inverted) in labels.items():
                    if key in checkin['answers']:
                        val = checkin['answers'][key]
                        if inverted:
                            desc = ['nessuno', 'minimo', 'moderato', 'alto', 'estremo'][val-1]
                        else:
                            desc = ['pessimo', 'scarso', 'normale', 'buono', 'ottimo'][val-1]
                        lines.append(f"   - {label}: {val}/5 ({desc})")
        
        return "\n".join(lines) if lines else "Nessun dato riportato"

    def _get_wellness_context(user_id):
        """Costruisce contesto wellness (fatica e check-in) per i coach AI"""
        today_date = date.today()
        wellness = {}
        
        # Fatica percepita oggi
        fatigue_today = FatigueLog.query.filter_by(user_id=user_id, date=today_date).first()
        if fatigue_today:
            wellness['fatigue_today'] = fatigue_today.value
            wellness['fatigue_today_label'] = _fatigue_label(fatigue_today.value)
        
        # Ultimi 7 giorni di fatica
        week_start = today_date - timedelta(days=7)
        recent_fatigue = FatigueLog.query.filter(
            FatigueLog.user_id == user_id,
            FatigueLog.date >= week_start
        ).all()
        
        if recent_fatigue:
            values = [f.value for f in recent_fatigue]
            wellness['fatigue_week_avg'] = round(sum(values) / len(values), 1)
            wellness['fatigue_week_max'] = max(values)
            wellness['fatigue_days_high'] = len([v for v in values if v >= 7])
        
        # Check-in settimanali
        for coach in ['sensei', 'sakura']:
            check = WeeklyCheck.query.filter_by(
                user_id=user_id,
                coach=coach
            ).order_by(WeeklyCheck.created_at.desc()).first()
            
            if check:
                answers = json.loads(check.answers)
                days_ago = (datetime.utcnow() - check.created_at).days if check.created_at else 999
                
                wellness[f'{coach}_checkin'] = {
                    'answers': answers,
                    'days_ago': days_ago,
                    'is_recent': days_ago <= 7
                }
                
                # Calcola score medio (invertendo domande negative)
                inverted = {'sensei': ['soreness'], 'sakura': ['stress', 'anxiety']}
                total = 0
                for key, val in answers.items():
                    if key in inverted.get(coach, []):
                        total += (6 - val)
                    else:
                        total += val
                wellness[f'{coach}_score'] = round(total / len(answers), 1) if answers else None
        
        return wellness

    def _build_context(user):
        """Costruisce contesto COMPLETO per i coach AI"""
        today = date.today()
        start_date = today - timedelta(days=30)
        metrics = DailyMetric.query.filter(
            DailyMetric.user_id == user.id, 
            DailyMetric.date >= start_date
        ).order_by(DailyMetric.date.desc()).all()
        
        if not metrics: 
            return {}
        
        def avg(lst):
            vals = [x for x in lst if x is not None]
            return round(sum(vals)/len(vals), 1) if vals else None
        
        def total(lst):
            vals = [x for x in lst if x is not None]
            return sum(vals) if vals else None
        
        # ═══ MEDIE 30 GIORNI ═══
        context = {
            'biological_age': avg([m.biological_age for m in metrics]),
            'recovery': avg([m.recovery_score for m in metrics]),
            'strain': avg([m.strain_score for m in metrics]),
            'sleep_performance': avg([m.sleep_performance for m in metrics]),
            'vo2_max': avg([m.vo2_max for m in metrics]),
            
            # Cuore
            'resting_hr': avg([m.resting_hr for m in metrics]),
            'min_hr': avg([m.min_hr for m in metrics]),
            'max_hr': avg([m.max_hr for m in metrics]),
            'hrv': avg([m.hrv_last_night for m in metrics]),
            'hrv_weekly': avg([m.hrv_weekly_avg for m in metrics]),
            
            # Sonno
            'sleep_hours': avg([m.sleep_seconds/3600 if m.sleep_seconds else None for m in metrics]),
            'deep_sleep_min': avg([m.deep_sleep_seconds/60 if m.deep_sleep_seconds else None for m in metrics]),
            'rem_sleep_min': avg([m.rem_sleep_seconds/60 if m.rem_sleep_seconds else None for m in metrics]),
            'light_sleep_min': avg([m.light_sleep_seconds/60 if m.light_sleep_seconds else None for m in metrics]),
            'awake_min': avg([m.awake_seconds/60 if m.awake_seconds else None for m in metrics]),
            'sleep_score': avg([m.sleep_score for m in metrics]),
            
            # Stress
            'stress_avg': avg([m.stress_avg for m in metrics]),
            'stress_max': avg([m.stress_max for m in metrics]),
            
            # Attività giornaliera
            'steps': avg([m.steps for m in metrics]),
            'total_calories': avg([m.total_calories for m in metrics]),
            'active_calories': avg([m.active_calories for m in metrics]),
            'distance_km': avg([m.distance_meters/1000 if m.distance_meters else None for m in metrics]),
            'floors': avg([m.floors_ascended for m in metrics]),
            'moderate_min': avg([m.moderate_intensity_minutes for m in metrics]),
            'vigorous_min': avg([m.vigorous_intensity_minutes for m in metrics]),
            
            # Body Battery
            'body_battery_high': avg([m.body_battery_high for m in metrics]),
            'body_battery_low': avg([m.body_battery_low for m in metrics]),
            
            # Respirazione e SpO2
            'respiration': avg([m.avg_respiration for m in metrics]),
            'spo2': avg([m.avg_spo2 for m in metrics]),
            
            # Fitness Age (da Garmin)
            'fitness_age': avg([m.fitness_age for m in metrics]),
        }
        
        # Race Predictions (prendi l'ultimo disponibile)
        for m in metrics:
            if m.race_time_5k:
                context['race_5k'] = m.race_time_5k
                context['race_10k'] = m.race_time_10k
                context['race_half'] = m.race_time_half
                context['race_marathon'] = m.race_time_marathon
                break
        
        # ═══ DATI DI IERI ═══
        yesterday = metrics[0] if metrics else None
        if yesterday:
            sleep_start_str = yesterday.sleep_start.strftime('%H:%M') if yesterday.sleep_start else None
            sleep_end_str = yesterday.sleep_end.strftime('%H:%M') if yesterday.sleep_end else None
            
            context['yesterday'] = {
                'date': yesterday.date.strftime('%d/%m'),
                'recovery': yesterday.recovery_score,
                'strain': yesterday.strain_score,
                'sleep_performance': yesterday.sleep_performance,
                
                # Sonno dettagliato
                'sleep_hours': round(yesterday.sleep_seconds / 3600, 1) if yesterday.sleep_seconds else None,
                'sleep_score': yesterday.sleep_score,
                'deep_sleep_min': round(yesterday.deep_sleep_seconds / 60) if yesterday.deep_sleep_seconds else None,
                'rem_sleep_min': round(yesterday.rem_sleep_seconds / 60) if yesterday.rem_sleep_seconds else None,
                'light_sleep_min': round(yesterday.light_sleep_seconds / 60) if yesterday.light_sleep_seconds else None,
                'awake_min': round(yesterday.awake_seconds / 60) if yesterday.awake_seconds else None,
                'sleep_start': sleep_start_str,
                'sleep_end': sleep_end_str,
                
                # Cuore
                'rhr': yesterday.resting_hr,
                'min_hr': yesterday.min_hr,
                'max_hr': yesterday.max_hr,
                'hrv': yesterday.hrv_last_night,
                
                # Stress dettagliato
                'stress_avg': yesterday.stress_avg,
                'stress_max': yesterday.stress_max,
                'rest_stress_min': round(yesterday.rest_stress_duration / 60) if yesterday.rest_stress_duration else None,
                'low_stress_min': round(yesterday.low_stress_duration / 60) if yesterday.low_stress_duration else None,
                'medium_stress_min': round(yesterday.medium_stress_duration / 60) if yesterday.medium_stress_duration else None,
                'high_stress_min': round(yesterday.high_stress_duration / 60) if yesterday.high_stress_duration else None,
                
                # Attività
                'steps': yesterday.steps,
                'total_calories': yesterday.total_calories,
                'active_calories': yesterday.active_calories,
                'distance_km': round(yesterday.distance_meters / 1000, 1) if yesterday.distance_meters else None,
                'floors': yesterday.floors_ascended,
                'moderate_min': yesterday.moderate_intensity_minutes,
                'vigorous_min': yesterday.vigorous_intensity_minutes,
                
                # Body Battery
                'body_battery_high': yesterday.body_battery_high,
                'body_battery_low': yesterday.body_battery_low,
                'body_battery_charged': yesterday.body_battery_charged,
                'body_battery_drained': yesterday.body_battery_drained,
                
                # Respirazione e SpO2
                'respiration': yesterday.avg_respiration,
                'spo2': yesterday.avg_spo2,
            }
        
        # ═══ TREND SETTIMANA ═══
        week1 = [m for m in metrics if m.date >= today - timedelta(days=7)]
        week2 = [m for m in metrics if today - timedelta(days=14) <= m.date < today - timedelta(days=7)]
        
        if week1 and week2:
            context['trend'] = {
                'sleep_change': round((avg([m.sleep_seconds/3600 if m.sleep_seconds else None for m in week1]) or 0) - 
                                      (avg([m.sleep_seconds/3600 if m.sleep_seconds else None for m in week2]) or 0), 1),
                'recovery_change': round((avg([m.recovery_score for m in week1]) or 0) - 
                                         (avg([m.recovery_score for m in week2]) or 0), 0),
                'rhr_change': round((avg([m.resting_hr for m in week1]) or 0) - 
                                    (avg([m.resting_hr for m in week2]) or 0), 0),
                'hrv_change': round((avg([m.hrv_last_night for m in week1]) or 0) - 
                                    (avg([m.hrv_last_night for m in week2]) or 0), 0),
                'steps_change': round((avg([m.steps for m in week1]) or 0) - 
                                      (avg([m.steps for m in week2]) or 0), 0),
                'stress_change': round((avg([m.stress_avg for m in week1]) or 0) - 
                                       (avg([m.stress_avg for m in week2]) or 0), 0),
            }
        
        # ═══ ATTIVITÀ RECENTI (30gg) - TUTTE ═══
        activities = Activity.query.filter(
            Activity.user_id == user.id,
            Activity.start_time >= datetime.now() - timedelta(days=30)
        ).order_by(Activity.start_time.desc()).all()
        
        if activities:
            context['recent_activities'] = [{
                'name': a.activity_name or a.activity_type,
                'type': a.activity_type,
                'date': a.start_time.strftime('%d/%m %H:%M') if a.start_time else None,
                'duration_min': round(a.duration_seconds / 60) if a.duration_seconds else None,
                'distance_km': round(a.distance_meters / 1000, 2) if a.distance_meters else None,
                'calories': a.calories,
                'avg_hr': a.avg_hr,
                'max_hr': a.max_hr,
                'strain': a.strain_score,
                'aerobic_effect': a.aerobic_effect,
                'anaerobic_effect': a.anaerobic_effect,
                'hr_zone_1_min': round(a.hr_zone_1 / 60) if a.hr_zone_1 else None,
                'hr_zone_2_min': round(a.hr_zone_2 / 60) if a.hr_zone_2 else None,
                'hr_zone_3_min': round(a.hr_zone_3 / 60) if a.hr_zone_3 else None,
                'hr_zone_4_min': round(a.hr_zone_4 / 60) if a.hr_zone_4 else None,
                'hr_zone_5_min': round(a.hr_zone_5 / 60) if a.hr_zone_5 else None,
                'moderate_min': a.moderate_intensity_minutes,
                'vigorous_min': a.vigorous_intensity_minutes,
            } for a in activities]
            
            # Riepilogo settimanale attività
            context['week_activity_summary'] = {
                'total_activities': len(activities),
                'total_duration_min': sum([a.duration_seconds/60 for a in activities if a.duration_seconds]),
                'total_calories': sum([a.calories for a in activities if a.calories]),
                'total_distance_km': round(sum([a.distance_meters/1000 for a in activities if a.distance_meters]), 1),
                'avg_strain': avg([a.strain_score for a in activities]),
            }
        
        # ═══ CONTESTO TEMPORALE INTELLIGENTE ═══
        now = datetime.now()
        weekday_names = ['lunedì', 'martedì', 'mercoledì', 'giovedì', 'venerdì', 'sabato', 'domenica']
        current_weekday = now.weekday()  # 0=lunedì
        current_hour = now.hour
        
        # Giorni passati questa settimana (lunedì = giorno 0)
        days_into_week = current_weekday + 1  # 1-7
        
        # Ultima attività e giorni di riposo
        days_since_activity = None
        last_activity_type = None
        if activities:
            last_activity_date = activities[0].start_time.date() if activities[0].start_time else None
            if last_activity_date:
                days_since_activity = (now.date() - last_activity_date).days
                last_activity_type = activities[0].activity_type
        
        # Attività questa settimana (dal lunedì corrente)
        week_start = now.date() - timedelta(days=current_weekday)
        this_week_activities = [a for a in activities if a.start_time and a.start_time.date() >= week_start]
        
        # Attività settimana scorsa (stesso periodo, es: se è mercoledì, lun-mer scorsi)
        last_week_start = week_start - timedelta(days=7)
        last_week_end = last_week_start + timedelta(days=current_weekday)
        last_week_same_period = [a for a in activities if a.start_time and 
                                  last_week_start <= a.start_time.date() <= last_week_end]
        
        # Pattern settimanale (su quali giorni si allena di solito)
        training_days_count = {}
        for a in activities:
            if a.start_time:
                day = a.start_time.weekday()
                training_days_count[day] = training_days_count.get(day, 0) + 1
        
        usual_training_days = [weekday_names[d] for d, count in sorted(training_days_count.items(), key=lambda x: -x[1]) if count >= 2][:3]
        
        # Streak di giorni consecutivi con attività
        activity_dates = set(a.start_time.date() for a in activities if a.start_time)
        streak = 0
        check_date = now.date()
        while check_date in activity_dates:
            streak += 1
            check_date -= timedelta(days=1)
        
        # ═══════════════════════════════════════════════════════════════
        #            LAYER DI INTERPRETAZIONE INTELLIGENTE
        # ═══════════════════════════════════════════════════════════════
        
        # Lista di cose che l'AI NON deve dire (basate su contesto)
        ai_dont_say = []
        ai_can_say = []
        
        # --- REGOLE ORARIO ---
        if current_hour < 10:
            ai_dont_say.append(f"NON commentare passi/calorie/attività di OGGI - sono solo le {current_hour}:00, la giornata è appena iniziata")
            ai_can_say.append("Puoi parlare di IERI e dei dati notturni (sonno, recupero, HRV)")
        elif current_hour < 14:
            ai_dont_say.append("NON giudicare i passi di oggi come 'pochi' - è ancora mattina/primo pomeriggio")
        
        # --- REGOLE GIORNO SETTIMANA ---
        if current_weekday == 0:  # Lunedì
            ai_dont_say.append("NON dire 'hai fatto poco questa settimana' - è LUNEDÌ, la settimana è appena iniziata!")
            ai_can_say.append("Puoi confrontare con la settimana SCORSA")
        elif current_weekday == 1:  # Martedì
            ai_dont_say.append("NON giudicare il volume settimanale - siamo solo a martedì")
        
        # --- REGOLE RIPOSO ---
        if days_since_activity is not None:
            if days_since_activity == 0:
                ai_can_say.append(f"Si è allenato OGGI ({last_activity_type}) - puoi commentare l'attività")
            elif days_since_activity == 1:
                ai_can_say.append("Ieri si è allenato - oggi può essere giorno di recupero attivo")
            elif days_since_activity == 2:
                ai_can_say.append("2 giorni di riposo - normale, non allarmante")
            elif days_since_activity >= 3:
                ai_can_say.append(f"Sono {days_since_activity} giorni senza allenamento - puoi suggerire di riprendere se il recupero è buono")
        
        # --- REGOLE WEEKEND ---
        if current_weekday >= 5:  # Sabato o Domenica
            ai_can_say.append("È weekend - momento ideale per attività più lunghe o riposo completo")
        
        # --- REGOLE PATTERN ---
        if usual_training_days:
            today_name = weekday_names[current_weekday]
            if today_name in usual_training_days and days_since_activity and days_since_activity >= 1:
                ai_can_say.append(f"Oggi è {today_name}, di solito si allena - puoi suggerirlo se i dati lo permettono")
        
        # --- REGOLE STREAK ---
        if streak >= 3:
            ai_can_say.append(f"Ha uno streak di {streak} giorni consecutivi - celebralo!")
        elif streak == 0 and days_since_activity and days_since_activity >= 2:
            ai_dont_say.append("NON essere giudicante sul riposo - potrebbe essere necessario")
        
        # Costruisci il blocco di regole per il prompt
        ai_rules_text = ""
        if ai_dont_say:
            ai_rules_text += "🚫 NON DIRE:\n" + "\n".join([f"  - {rule}" for rule in ai_dont_say]) + "\n\n"
        if ai_can_say:
            ai_rules_text += "✅ PUOI DIRE:\n" + "\n".join([f"  - {rule}" for rule in ai_can_say])
        
        context['temporal'] = {
            'weekday': weekday_names[current_weekday],
            'weekday_num': current_weekday,
            'hour': current_hour,
            'days_into_week': days_into_week,
            'days_since_activity': days_since_activity,
            'last_activity_type': last_activity_type,
            'this_week_activities': len(this_week_activities),
            'last_week_same_period': len(last_week_same_period),
            'usual_training_days': usual_training_days,
            'streak': streak,
            'is_rest_day': days_since_activity and days_since_activity >= 1,
        }
        
        context['ai_rules'] = ai_rules_text
        
        # Aggiungi dati wellness (fatica e check-in)
        wellness = _get_wellness_context(user.id)
        if wellness:
            context['wellness'] = wellness
        
        return context

    def _extract_memories(response, user_id, coach):
        import re
        pattern = r'\[MEMORY:\s*(\w+)\s*\|\s*([^\]]+)\]'
        for cat, content in re.findall(pattern, response):
            mem = UserMemory(user_id=user_id, category=cat.lower(), content=content.strip(), coach=coach)
            db.session.add(mem)
        return re.sub(pattern, '', response).strip()

    @app.route('/api/chat', methods=['POST'])
    @token_required
    def chat(current_user):
        if not openai_client:
            return jsonify({'error': 'OpenAI non configurato'}), 500
        
        data = request.get_json()
        msg = data.get('message', '').strip()
        coach = data.get('coach', 'sensei')
        if not msg:
            return jsonify({'error': 'Messaggio vuoto'}), 400
        
        try:
            context = _build_context(current_user)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({'error': f'Errore context: {str(e)}'}), 500
        
        try:
            memories = UserMemory.query.filter_by(user_id=current_user.id, is_active=True).limit(20).all()
        except Exception as e:
            memories = []
        
        # Get history
        try:
            if coach == 'sensei':
                history = ChatMessage.query.filter(
                    ChatMessage.user_id == current_user.id,
                    or_(ChatMessage.coach == 'sensei', ChatMessage.coach == None)
                ).order_by(ChatMessage.created_at.desc()).limit(10).all()
            else:
                history = ChatMessage.query.filter_by(user_id=current_user.id, coach=coach).order_by(ChatMessage.created_at.desc()).limit(10).all()
            history.reverse()
        except Exception as e:
            history = []
        
        try:
            system_prompt = _get_sakura_prompt(current_user, context, memories) if coach == 'sakura' else _get_sensei_prompt(current_user, context, memories)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({'error': f'Errore prompt: {str(e)}'}), 500
        
        messages = [{"role": "system", "content": system_prompt}]
        messages += [{"role": m.role, "content": m.content} for m in history]
        messages.append({"role": "user", "content": msg})
        
        # Aumenta token per meditazioni
        meditation_keywords = ['meditazione', 'medita', 'guidami', 'rilassamento', 'respiro', 'mindfulness', 'minuti']
        is_meditation_request = any(kw in msg.lower() for kw in meditation_keywords) and coach == 'sakura'
        max_tokens = 2000 if is_meditation_request else 800
        
        try:
            resp = openai_client.chat.completions.create(model="gpt-4.1", messages=messages, max_tokens=max_tokens, temperature=0.8)
            ai_raw = resp.choices[0].message.content
            
            # Save messages
            db.session.add(ChatMessage(user_id=current_user.id, role='user', content=msg, coach=coach))
            ai_clean = _extract_memories(ai_raw, current_user.id, coach)
            db.session.add(ChatMessage(user_id=current_user.id, role='assistant', content=ai_clean, coach=coach))
            db.session.commit()
            
            return jsonify({'response': ai_clean, 'coach': coach})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({'error': f'Errore AI: {str(e)}'}), 500

    @app.route('/api/chat/history', methods=['GET'])
    @token_required
    def chat_history(current_user):
        coach = request.args.get('coach', 'sensei')
        limit = request.args.get('limit', 50, type=int)
        
        if coach == 'sensei':
            msgs = ChatMessage.query.filter(
                ChatMessage.user_id == current_user.id,
                or_(ChatMessage.coach == 'sensei', ChatMessage.coach == None)
            ).order_by(ChatMessage.created_at.desc()).limit(limit).all()
        else:
            msgs = ChatMessage.query.filter_by(user_id=current_user.id, coach=coach).order_by(ChatMessage.created_at.desc()).limit(limit).all()
        
        msgs.reverse()
        return jsonify([{'role': m.role, 'content': m.content} for m in msgs])

    @app.route('/api/chat/reset', methods=['DELETE'])
    @token_required
    def chat_reset(current_user):
        coach = request.args.get('coach', 'sensei')
        if coach == 'sensei':
            ChatMessage.query.filter(
                ChatMessage.user_id == current_user.id,
                or_(ChatMessage.coach == 'sensei', ChatMessage.coach == None)
            ).delete(synchronize_session=False)
        else:
            ChatMessage.query.filter_by(user_id=current_user.id, coach=coach).delete(synchronize_session=False)
        db.session.commit()
        return jsonify({'message': f'Chat {coach} resettata'})

    @app.route('/api/chat/memories', methods=['GET'])
    @token_required
    def get_memories(current_user):
        mems = UserMemory.query.filter_by(user_id=current_user.id, is_active=True).all()
        return jsonify([{'id': m.id, 'category': m.category, 'content': m.content, 'coach': m.coach} for m in mems])

    @app.route('/api/chat/memories/<int:mid>', methods=['DELETE'])
    @token_required
    def delete_memory(current_user, mid):
        m = UserMemory.query.filter_by(id=mid, user_id=current_user.id).first()
        if m: m.is_active = False; db.session.commit()
        return jsonify({'message': 'OK'})
    
    # ═══════════════════════════════════════════════════════════════
    #                    DEEP ANALYSIS CON GPT-5
    # ═══════════════════════════════════════════════════════════════
    @app.route('/api/deep-analysis', methods=['POST'])
    @token_required
    def deep_analysis(current_user):
        """Analisi profonda dei pattern con GPT-5 reasoning"""
        if not openai_client:
            return jsonify({'error': 'AI non configurata'}), 500
        
        try:
            # Prendi 90 giorni di dati
            today = date.today()
            start_date = today - timedelta(days=90)
            metrics = DailyMetric.query.filter(
                DailyMetric.user_id == current_user.id,
                DailyMetric.date >= start_date
            ).order_by(DailyMetric.date.asc()).all()
            
            if len(metrics) < 14:
                return jsonify({'error': 'Servono almeno 14 giorni di dati per l\'analisi'}), 400
            
            # Prepara dati per analisi
            data_rows = []
            for m in metrics:
                data_rows.append({
                    'date': str(m.date),
                    'weekday': m.date.strftime('%A'),
                    'recovery': m.recovery_score,
                    'strain': m.strain_score,
                    'sleep_hours': round(m.sleep_seconds / 3600, 1) if m.sleep_seconds else None,
                    'deep_sleep': round(m.deep_sleep_seconds / 60) if m.deep_sleep_seconds else None,
                    'rem_sleep': round(m.rem_sleep_seconds / 60) if m.rem_sleep_seconds else None,
                    'rhr': m.resting_hr,
                    'hrv': m.hrv_last_night or m.hrv_weekly_avg,
                    'stress_avg': m.stress_avg,
                    'steps': m.steps,
                    'active_cal': m.active_calories,
                    'bio_age': m.biological_age
                })
            
            # Prendi attivita
            activities = Activity.query.filter(
                Activity.user_id == current_user.id,
                Activity.start_time >= datetime.combine(start_date, datetime.min.time())
            ).order_by(Activity.start_time.desc()).all()
            
            activity_data = []
            for a in activities:
                activity_data.append({
                    'date': str(a.start_time.date()) if a.start_time else None,
                    'weekday': a.start_time.strftime('%A') if a.start_time else None,
                    'type': a.activity_type,
                    'duration_min': round(a.duration_seconds / 60) if a.duration_seconds else None,
                    'strain': a.strain_score,
                    'avg_hr': a.avg_hr,
                    'max_hr': a.max_hr
                })
            
            # Prendi fatica percepita
            fatigue_logs = FatigueLog.query.filter(
                FatigueLog.user_id == current_user.id,
                FatigueLog.date >= start_date
            ).all()
            fatigue_data = {str(f.date): f.value for f in fatigue_logs}
            
            # Costruisci prompt per GPT-5 con personalità Dr. Data
            import json
            analysis_prompt = f"""Sei DR. DATA, scienziato dei dati biometrici del Performance Lab.

PERSONALITA:
- Parli come uno scienziato appassionato ma accessibile
- Usi analogie scientifiche per spiegare i pattern
- Sei entusiasta quando trovi correlazioni interessanti
- Chiami l'utente "{current_user.name or 'atleta'}" e hai un tono professionale ma caldo
- Collabori con Dr. Sensei (preparatore atletico) e Dr. Sakura (coach mentale)

DATI DISPONIBILI: 90 giorni di dati biometrici di {current_user.name or 'utente'}, {current_user.get_real_age()} anni.

=== DATI GIORNALIERI ({len(data_rows)} giorni) ===
{json.dumps(data_rows[-30:], indent=1)}

=== ATTIVITA ({len(activity_data)} sessioni) ===
{json.dumps(activity_data[:20], indent=1)}

=== FATICA PERCEPITA ===
{json.dumps(fatigue_data, indent=1)}

=== STATISTICHE RAPIDE ===
- Recovery media: {sum(m['recovery'] or 0 for m in data_rows)/len(data_rows):.1f}%
- Sonno medio: {sum(m['sleep_hours'] or 0 for m in data_rows)/len(data_rows):.1f}h
- HRV medio: {sum(m['hrv'] or 0 for m in data_rows if m['hrv'])/max(1, len([m for m in data_rows if m['hrv']])):.0f}ms
- Stress medio: {sum(m['stress_avg'] or 0 for m in data_rows if m['stress_avg'])/max(1, len([m for m in data_rows if m['stress_avg']])):.0f}

ANALIZZA E RISPONDI IN ITALIANO. Struttura la risposta cosi:

🔬 **CORRELAZIONI SCOPERTE**
(3-5 pattern significativi con numeri precisi)

📈 **TREND E PROGRESSI**
(confronto ultime 2 settimane vs prima)

⚠️ **ANOMALIE RILEVATE**
(valori fuori norma, giorni critici)

🔮 **PREDIZIONI**
(recovery domani, rischio overtraining)

💡 **RACCOMANDAZIONI**
(3-5 azioni concrete e specifiche)

Sii specifico, cita numeri esatti, evita generalita. Parla come uno scienziato entusiasta!"""

            # Chiama GPT-4o per analisi profonda (più affidabile di GPT-5 per questo task)
            full_prompt = f"""RUOLO: Sei Dr. Data, scienziato dei dati biometrici. Analizzi pattern complessi con entusiasmo scientifico, spiegando le correlazioni in modo accessibile ma rigoroso. Fai parte del Performance Lab insieme a Dr. Sensei e Dr. Sakura.

{analysis_prompt}"""
            
            response = openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "Sei Dr. Data, scienziato dei dati biometrici del Performance Lab. Analizzi pattern complessi con entusiasmo scientifico."},
                    {"role": "user", "content": analysis_prompt}
                ],
                max_tokens=2500,
                temperature=0.7
            )
            
            # Estrai il contenuto
            analysis_text = None
            if response.choices and len(response.choices) > 0:
                message = response.choices[0].message
                analysis_text = message.content
            
            if not analysis_text:
                return jsonify({
                    'error': 'Risposta vuota. Riprova.'
                }), 500
            
            return jsonify({
                'analysis': analysis_text,
                'days_analyzed': len(data_rows),
                'activities_analyzed': len(activity_data),
                'model': 'gpt-4o'
            })
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({'error': str(e)}), 500

    @app.route('/api/recalculate', methods=['POST'])
    @token_required
    def recalculate_bio_age(current_user):
        """Ricalcola età biologica con formula NORMALIZZATA"""
        
        # Prima assicuriamoci che le colonne esistano
        try:
            db.session.execute(db.text('''
                ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS biological_age FLOAT;
                ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS bio_age_rhr_impact FLOAT;
                ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS bio_age_vo2_impact FLOAT;
                ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS bio_age_sleep_impact FLOAT;
                ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS bio_age_steps_impact FLOAT;
                ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS bio_age_stress_impact FLOAT;
                ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS bio_age_hrz_impact FLOAT;
                ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS vo2_max FLOAT;
            '''))
            db.session.commit()
        except Exception as e:
            db.session.rollback()
        
        metrics = DailyMetric.query.filter_by(user_id=current_user.id).all()
        count = 0
        real_age = current_user.get_real_age()
        MAX_YEARS = 8  # Range finale ±8 anni
        
        for metric in metrics:
            # Calcola impatti normalizzati (-1 a +1)
            impacts = {}
            
            # 1. RHR (baseline 60, range 40-80)
            if metric.resting_hr and metric.resting_hr > 0:
                rhr = metric.resting_hr
                raw = (rhr - 60) / 20
                impacts['rhr'] = max(-1, min(1, raw))
            
            # 2. VO2 Max (baseline 42, range 30-55)
            if metric.vo2_max and metric.vo2_max > 0:
                vo2 = metric.vo2_max
                raw = (42 - vo2) / 13
                impacts['vo2'] = max(-1, min(1, raw))
            
            # 3. Sleep (ottimale 7-8.5h)
            if metric.sleep_seconds and metric.sleep_seconds > 0:
                sleep_hours = metric.sleep_seconds / 3600
                if sleep_hours >= 7 and sleep_hours <= 8.5:
                    impacts['sleep'] = -0.3
                elif sleep_hours < 7:
                    impacts['sleep'] = min(1, (7 - sleep_hours) / 3)
                else:
                    impacts['sleep'] = min(0.5, (sleep_hours - 8.5) / 3)
            
            # 4. Steps
            if metric.steps and metric.steps > 0:
                steps = metric.steps
                if steps >= 12000: impacts['steps'] = -0.8
                elif steps >= 10000: impacts['steps'] = -0.5
                elif steps >= 8000: impacts['steps'] = -0.2
                elif steps >= 6000: impacts['steps'] = 0.0
                elif steps >= 4000: impacts['steps'] = 0.3
                elif steps >= 2000: impacts['steps'] = 0.6
                else: impacts['steps'] = 1.0
            
            # 5. Intensity Minutes
            moderate = metric.moderate_intensity_minutes or 0
            vigorous = metric.vigorous_intensity_minutes or 0
            intensity_score = moderate + (vigorous * 2)
            
            if intensity_score > 0 or (metric.steps and metric.steps > 0):
                if intensity_score >= 60: impacts['hrz'] = -0.8
                elif intensity_score >= 45: impacts['hrz'] = -0.5
                elif intensity_score >= 30: impacts['hrz'] = -0.2
                elif intensity_score >= 15: impacts['hrz'] = 0.0
                elif intensity_score >= 5: impacts['hrz'] = 0.3
                else: impacts['hrz'] = 0.5
            
            # Minimo 2 metriche
            if len(impacts) < 2:
                metric.biological_age = None
                metric.bio_age_rhr_impact = None
                metric.bio_age_vo2_impact = None
                metric.bio_age_sleep_impact = None
                metric.bio_age_steps_impact = None
                metric.bio_age_hrz_impact = None
                metric.bio_age_stress_impact = None
                continue
            
            # Media normalizzata
            avg_impact = sum(impacts.values()) / len(impacts)
            final_impact = avg_impact * MAX_YEARS
            
            # Salva impatti scalati per display
            scale = MAX_YEARS / len(impacts)
            metric.bio_age_rhr_impact = round(impacts.get('rhr', 0) * scale, 1) if 'rhr' in impacts else None
            metric.bio_age_vo2_impact = round(impacts.get('vo2', 0) * scale, 1) if 'vo2' in impacts else None
            metric.bio_age_sleep_impact = round(impacts.get('sleep', 0) * scale, 1) if 'sleep' in impacts else None
            metric.bio_age_steps_impact = round(impacts.get('steps', 0) * scale, 1) if 'steps' in impacts else None
            metric.bio_age_hrz_impact = round(impacts.get('hrz', 0) * scale, 1) if 'hrz' in impacts else None
            metric.bio_age_stress_impact = None
            
            metric.biological_age = round(real_age + final_impact, 1)
            count += 1
        
        db.session.commit()
        return jsonify({'message': f'Ricalcolati {count} giorni (formula normalizzata v2)', 'count': count, 'real_age': real_age})
    
    @app.route('/api/debug/bio', methods=['GET'])
    @token_required
    def debug_bio(current_user):
        """Debug: mostra ultimi 5 giorni con tutti i valori"""
        metrics = DailyMetric.query.filter_by(user_id=current_user.id).order_by(DailyMetric.date.desc()).limit(5).all()
        return jsonify([{
            'date': m.date.isoformat(),
            'sleep_seconds': m.sleep_seconds,
            'sleep_hours': round(m.sleep_seconds / 3600, 2) if m.sleep_seconds else None,
            'resting_hr': m.resting_hr,
            'hrv_last_night': m.hrv_last_night,
            'hrv_weekly_avg': m.hrv_weekly_avg,
            'steps': m.steps,
            'stress_avg': m.stress_avg,
            'recovery_score': m.recovery_score,
            'strain_score': m.strain_score,
            'bio_age_sleep_impact': m.bio_age_sleep_impact,
            'bio_age_rhr_impact': m.bio_age_rhr_impact,
            'bio_age_steps_impact': m.bio_age_steps_impact,
            'bio_age_stress_impact': m.bio_age_stress_impact,
            'bio_age_hrz_impact': m.bio_age_hrz_impact,
            'biological_age': m.biological_age
        } for m in metrics])
    
    @app.route('/api/debug/hrv', methods=['GET'])
    @token_required
    def debug_hrv(current_user):
        """Debug: verifica TUTTI i dati disponibili da Garmin"""
        import os
        
        try:
            from garminconnect import Garmin
        except ImportError as e:
            return jsonify({'error': f'Import garminconnect fallito: {str(e)}'}), 500
        
        if not current_user.garmin_email:
            return jsonify({'error': 'Garmin non connesso'}), 400
        
        encryption_key = os.environ.get('ENCRYPTION_KEY')
        if not encryption_key:
            return jsonify({'error': 'ENCRYPTION_KEY non configurata'}), 500
            
        if not current_user.garmin_password_encrypted:
            return jsonify({'error': 'Password Garmin non salvata'}), 400
        
        try:
            garmin_password = current_user.get_garmin_password(encryption_key)
        except Exception as e:
            return jsonify({'error': f'Decrypt password fallito: {str(e)}'}), 500
        
        try:
            client = Garmin(current_user.garmin_email, garmin_password)
            client.login()
        except Exception as e:
            return jsonify({'error': f'Login Garmin fallito: {str(e)}'}), 500
        
        today = date.today()
        yesterday = today - timedelta(days=1)
        day_str = yesterday.strftime('%Y-%m-%d')
        
        results = {'date': day_str, 'data': {}}
        
        # Lista di tutti i metodi da testare
        methods = [
            ('stats', lambda: client.get_stats(day_str)),
            ('hrv', lambda: client.get_hrv_data(day_str)),
            ('body_battery', lambda: client.get_body_battery(day_str)),
            ('stress', lambda: client.get_stress_data(day_str)),
            ('heart_rates', lambda: client.get_heart_rates(day_str)),
            ('respiration', lambda: client.get_respiration_data(day_str)),
            ('spo2', lambda: client.get_spo2_data(day_str)),
            ('sleep', lambda: client.get_sleep_data(day_str)),
            ('steps', lambda: client.get_steps_data(day_str)),
            ('floors', lambda: client.get_floors(day_str)),
            ('intensity_minutes', lambda: client.get_intensity_minutes_data(day_str)),
            ('rhr', lambda: client.get_rhr_day(day_str)),
            ('training_status', lambda: client.get_training_status(day_str)),
            ('training_readiness', lambda: client.get_training_readiness(day_str)),
            ('max_metrics', lambda: client.get_max_metrics(day_str)),
            ('fitness_age', lambda: client.get_fitnessage_data(day_str)),
            ('endurance_score', lambda: client.get_endurance_score(day_str)),
            ('hill_score', lambda: client.get_hill_score(day_str)),
            ('race_predictions', lambda: client.get_race_predictions()),
            ('devices', lambda: client.get_devices()),
        ]
        
        for name, method in methods:
            try:
                data = method()
                # Limita output per evitare risposte enormi
                if isinstance(data, dict):
                    results['data'][name] = {k: v for k, v in list(data.items())[:20]}
                elif isinstance(data, list):
                    results['data'][name] = data[:5] if len(data) > 5 else data
                else:
                    results['data'][name] = data
            except Exception as e:
                results['data'][name] = {'error': str(e)}
        
        return jsonify(results)
    
    @app.route('/api/activity/<int:activity_id>/comment', methods=['POST'])
    @token_required
    def get_activity_comment(current_user, activity_id):
        """Genera un commento AI per un'attività"""
        if not openai_client:
            return jsonify({'error': 'OpenAI non configurato'}), 500
        
        activity = Activity.query.filter_by(id=activity_id, user_id=current_user.id).first()
        if not activity:
            return jsonify({'error': 'Attività non trovata'}), 404
        
        # Prepara il contesto dell'attività
        duration_min = round(activity.duration_seconds / 60) if activity.duration_seconds else 0
        distance_km = round(activity.distance_meters / 1000, 2) if activity.distance_meters else 0
        
        prompt = f"""Analizza brevemente questa attività sportiva e dai un commento motivazionale/tecnico in 2-3 frasi.

Attività: {activity.activity_name or activity.activity_type}
Durata: {duration_min} minuti
Distanza: {distance_km} km
Calorie: {activity.calories or 'N/A'}
HR Media: {activity.avg_hr or 'N/A'} bpm
HR Max: {activity.max_hr or 'N/A'} bpm
Effetto Aerobico: {activity.aerobic_effect or 'N/A'}
Strain: {activity.strain_score or 'N/A'}/21

Rispondi in italiano, in modo diretto e motivante."""

        try:
            response = openai_client.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {"role": "system", "content": "Sei Sensei, un coach sportivo esperto. Dai feedback brevi e motivanti sulle attività."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=150,
                temperature=0.7
            )
            comment = response.choices[0].message.content
            return jsonify({'comment': comment})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/tts', methods=['POST'])
    @token_required
    def text_to_speech(current_user):
        """Converte testo in audio usando OpenAI TTS con istruzioni vocali"""
        if not openai_client:
            return jsonify({'error': 'OpenAI non configurato'}), 500
        
        import re
        import base64
        
        data = request.get_json()
        text = data.get('text', '')[:2000]
        coach = data.get('coach', 'sensei')
        is_meditation = data.get('meditation', False) or '[PAUSA:' in text
        
        if not text:
            return jsonify({'error': 'Testo vuoto'}), 400
        
        # Pulisci testo da formattazioni markdown
        clean_text = text
        clean_text = re.sub(r'\*\*([^*]+)\*\*', r'\1', clean_text)
        clean_text = re.sub(r'\*([^*]+)\*', r'\1', clean_text)
        clean_text = re.sub(r'#{1,6}\s*', '', clean_text)
        clean_text = re.sub(r'---+', '', clean_text)
        clean_text = re.sub(r'\[PAUSA:\d+\]', '', clean_text)
        clean_text = clean_text.strip()
        
        if not clean_text:
            return jsonify({'error': 'Testo vuoto dopo pulizia'}), 400
        
        # Configurazione voci con istruzioni
        if coach == 'sensei':
            voice = 'ash'
            instructions = """Voice Affect: Calm, confident, grounded; embody expertise and trust.
Tone: Warm, motivating, reassuring; convey genuine support and professionalism.
Pacing: Measured, steady, natural; pause briefly after key points to let advice sink in.
Emotion: Encouraging and supportive; express genuine care for the listener's progress.
Pronunciation: Clear, precise Italian articulation, natural flow.
Pauses: Use brief pauses after important recommendations, enhancing clarity and impact."""
        elif coach == 'sakura' and is_meditation:
            # MODALITÀ MEDITAZIONE - voce sussurrata, lenta, intima
            voice = 'nova'
            instructions = """Voice Affect: Whispered, intimate, deeply sensual; like a lover speaking softly in your ear.
Tone: Hypnotic, seductive, incredibly soothing; create an atmosphere of complete surrender and relaxation.
Pacing: Extremely slow, breathy, each word savored; long pauses between phrases to let the listener melt.
Emotion: Tender, intimate, caring; as if guiding someone precious through a dream.
Pronunciation: Soft, breathy Italian; elongate vowels sensually, let words flow like silk.
Breathing: Audible soft breaths between sentences, creating intimacy.
Pauses: Long, pregnant pauses after each instruction; let silence embrace the listener.
Style: ASMR-like whisper, gentle and mesmerizing, almost hypnotic."""
        else:
            # Sakura normale
            voice = 'nova'
            instructions = """Voice Affect: Soft, gentle, soothing; embody tranquility and warmth.
Tone: Calm, reassuring, peaceful; convey genuine warmth and serenity.
Pacing: Slow, deliberate, and unhurried; pause gently after instructions.
Emotion: Deeply soothing and comforting; express genuine kindness and care.
Pronunciation: Smooth, soft Italian articulation, slightly elongating vowels.
Pauses: Use thoughtful pauses, especially between sentences, enhancing relaxation."""
        
        try:
            response = openai_client.audio.speech.create(
                model="gpt-4o-mini-tts",
                voice=voice,
                input=clean_text,
                instructions=instructions,
                response_format="mp3"
            )
            
            audio_b64 = base64.b64encode(response.content).decode('utf-8')
            return jsonify({'audio': audio_b64, 'format': 'mp3', 'meditation': is_meditation})
            
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    # ========== FATIGUE (Fatica Percepita) ==========
    
    @app.route('/api/fatigue', methods=['GET'])
    @token_required
    def get_fatigue(current_user):
        """Ottieni fatica per una data specifica"""
        date_str = request.args.get('date')
        
        if not date_str:
            return jsonify({'error': 'Date required'}), 400
        
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'error': 'Invalid date format'}), 400
        
        fatigue = FatigueLog.query.filter_by(
            user_id=current_user.id,
            date=target_date
        ).first()
        
        if fatigue:
            return jsonify({
                'date': date_str,
                'value': fatigue.value,
                'created_at': fatigue.created_at.isoformat() if fatigue.created_at else None
            })
        return jsonify({'date': date_str, 'value': None})
    
    @app.route('/api/fatigue', methods=['POST'])
    @token_required
    def save_fatigue(current_user):
        """Salva fatica giornaliera (1-10)"""
        data = request.get_json()
        
        date_str = data.get('date')
        value = data.get('value')
        
        if not date_str or value is None:
            return jsonify({'error': 'Date and value required'}), 400
        
        if not (1 <= value <= 10):
            return jsonify({'error': 'Value must be 1-10'}), 400
        
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'error': 'Invalid date format'}), 400
        
        # Upsert: trova esistente o crea nuovo
        fatigue = FatigueLog.query.filter_by(
            user_id=current_user.id,
            date=target_date
        ).first()
        
        if fatigue:
            fatigue.value = value
            fatigue.created_at = datetime.utcnow()
        else:
            fatigue = FatigueLog(
                user_id=current_user.id,
                date=target_date,
                value=value
            )
            db.session.add(fatigue)
        
        db.session.commit()
        return jsonify({'success': True, 'date': date_str, 'value': value})
    
    @app.route('/api/fatigue/history', methods=['GET'])
    @token_required
    def get_fatigue_history(current_user):
        """Ottieni storico fatica ultimi N giorni"""
        days = request.args.get('days', 30, type=int)
        start_date = date.today() - timedelta(days=days)
        
        logs = FatigueLog.query.filter(
            FatigueLog.user_id == current_user.id,
            FatigueLog.date >= start_date
        ).order_by(FatigueLog.date.desc()).all()
        
        return jsonify([{
            'date': log.date.isoformat(),
            'value': log.value
        } for log in logs])
    
    # ========== WEEKLY CHECK (Check-in Settimanali) ==========
    
    @app.route('/api/weekly-check', methods=['POST'])
    @token_required
    def save_weekly_check(current_user):
        """Salva check-in settimanale per un coach"""
        data = request.get_json()
        
        coach = data.get('coach')
        answers = data.get('answers')
        
        if not coach or coach not in ['sensei', 'sakura']:
            return jsonify({'error': 'Invalid coach'}), 400
        
        if not answers or not isinstance(answers, dict):
            return jsonify({'error': 'Answers required'}), 400
        
        # Crea nuovo check-in
        check = WeeklyCheck(
            user_id=current_user.id,
            coach=coach,
            answers=json.dumps(answers)
        )
        db.session.add(check)
        db.session.commit()
        
        return jsonify({'success': True, 'coach': coach, 'id': check.id})
    
    @app.route('/api/weekly-check/latest', methods=['GET'])
    @token_required
    def get_latest_weekly_check(current_user):
        """Ottieni ultimo check-in per un coach"""
        coach = request.args.get('coach')
        
        if not coach or coach not in ['sensei', 'sakura']:
            return jsonify({'error': 'Invalid coach'}), 400
        
        check = WeeklyCheck.query.filter_by(
            user_id=current_user.id,
            coach=coach
        ).order_by(WeeklyCheck.created_at.desc()).first()
        
        if check:
            return jsonify({
                'coach': coach,
                'answers': json.loads(check.answers),
                'created_at': check.created_at.isoformat() if check.created_at else None
            })
        return jsonify({'coach': coach, 'answers': None})
    
    @app.route('/api/weekly-check/history', methods=['GET'])
    @token_required
    def get_weekly_check_history(current_user):
        """Ottieni storico check-in per un coach"""
        coach = request.args.get('coach')
        weeks = request.args.get('weeks', 12, type=int)
        
        if not coach or coach not in ['sensei', 'sakura']:
            return jsonify({'error': 'Invalid coach'}), 400
        
        checks = WeeklyCheck.query.filter_by(
            user_id=current_user.id,
            coach=coach
        ).order_by(WeeklyCheck.created_at.desc()).limit(weeks).all()
        
        return jsonify([{
            'answers': json.loads(c.answers),
            'created_at': c.created_at.isoformat() if c.created_at else None
        } for c in checks])
    
    # ========== WELLNESS SCORE (Punteggio combinato) ==========
    
    @app.route('/api/wellness/score', methods=['GET'])
    @token_required
    def get_wellness_score(current_user):
        """Ottieni punteggio benessere combinato"""
        today_date = date.today()
        
        # Fatica di oggi
        fatigue_log = FatigueLog.query.filter_by(
            user_id=current_user.id,
            date=today_date
        ).first()
        fatigue = fatigue_log.value if fatigue_log else None
        
        # Recovery Garmin di oggi
        today_metric = DailyMetric.query.filter_by(
            user_id=current_user.id,
            date=today_date
        ).first()
        garmin_recovery = today_metric.recovery_score if today_metric else None
        
        # Ultimi check-in settimanali
        scores = {}
        for coach in ['sensei', 'sakura']:
            check = WeeklyCheck.query.filter_by(
                user_id=current_user.id,
                coach=coach
            ).order_by(WeeklyCheck.created_at.desc()).first()
            
            if check:
                answers = json.loads(check.answers)
                inverted = {
                    'sensei': ['soreness'],
                    'sakura': ['stress', 'anxiety']
                }
                total = 0
                for key, val in answers.items():
                    if key in inverted.get(coach, []):
                        total += (6 - val)
                    else:
                        total += val
                scores[coach] = round(total / len(answers), 1) if answers else None
        
        # Calcola readiness combinato (0-100)
        readiness = None
        components = []
        weights = []
        
        if garmin_recovery:
            components.append(garmin_recovery)
            weights.append(0.4)
        
        if fatigue:
            fatigue_score = (11 - fatigue) * 10
            components.append(fatigue_score)
            weights.append(0.3)
        
        if scores.get('sensei'):
            physical_score = scores['sensei'] * 20
            components.append(physical_score)
            weights.append(0.2)
        
        if scores.get('sakura'):
            mental_score = scores['sakura'] * 20
            components.append(mental_score)
            weights.append(0.1)
        
        if components:
            total_weight = sum(weights)
            readiness = round(sum(c * w / total_weight for c, w in zip(components, weights)))
        
        # Genera raccomandazione
        recommendation = _get_readiness_recommendation(readiness, fatigue, garmin_recovery)
        
        return jsonify({
            'garmin_recovery': garmin_recovery,
            'fatigue_today': fatigue,
            'physical_score': scores.get('sensei'),
            'mental_score': scores.get('sakura'),
            'readiness': readiness,
            'recommendation': recommendation
        })
    
    return app


def _get_readiness_recommendation(readiness, fatigue, garmin_recovery):
    """Genera raccomandazione allenamento basata sui punteggi"""
    if fatigue and fatigue >= 8:
        return "[STOP] Riposo consigliato - fatica muscolare alta"
    
    if fatigue and fatigue >= 6 and (not garmin_recovery or garmin_recovery < 60):
        return "[REST] Giornata di recupero - ascolta il corpo"
    
    if readiness is None:
        if fatigue:
            if fatigue <= 3:
                return "[GO] Pronto per allenamento intenso"
            elif fatigue <= 5:
                return "[OK] OK per allenamento normale"
            elif fatigue <= 7:
                return "[LIGHT] Meglio attivita leggera"
            else:
                return "[REST] Riposo consigliato"
        elif garmin_recovery:
            if garmin_recovery >= 70:
                return "[GO] Recovery alto - via libera!"
            elif garmin_recovery >= 50:
                return "[OK] Recovery ok - allenamento moderato"
            else:
                return "[LIGHT] Recovery basso - attivita leggera"
        return "[INFO] Compila i check-in per consigli personalizzati"
    
    if readiness >= 80:
        return "[GO] Pronto per allenamento intenso"
    elif readiness >= 65:
        return "[OK] OK per allenamento normale"
    elif readiness >= 50:
        return "[LIGHT] Meglio attivita leggera"
    elif readiness >= 35:
        return "[YOGA] Recupero attivo consigliato"
    else:
        return "[REST] Giornata di riposo"


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
    app.run(debug=True, port=5000)# Updated Wed Dec 10 11:51:30 UTC 2025