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
    
    db.init_app(app)
    CORS(app)
    
    with app.app_context():
        db.create_all()
    
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
        return send_from_directory(app.static_folder, 'index.html')
    
    # ========== AUTH ==========
    
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
        
        if data.get('garmin_email') and data.get('garmin_password'):
            user.garmin_email = data['garmin_email']
            user.set_garmin_password(data['garmin_password'], app.config['ENCRYPTION_KEY'])
        
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
                'last_sync': user.last_sync.isoformat() if user.last_sync else None,
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
    
    # ========== GARMIN ==========
    
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
    
    # ========== SYNC ==========
    
    @app.route('/api/sync', methods=['POST'])
    @token_required
    def sync_now(current_user):
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
        week_ago = date.today() - timedelta(days=7)
        
        metrics = DailyMetric.query.filter(
            DailyMetric.user_id == current_user.id,
            DailyMetric.date >= week_ago
        ).order_by(DailyMetric.date.desc()).all()
        
        if not metrics:
            return jsonify({'message': 'Nessun dato disponibile'}), 404
        
        recovery_scores = [m.recovery_score for m in metrics if m.recovery_score]
        strain_scores = [m.strain_score for m in metrics if m.strain_score]
        sleep_scores = [m.sleep_performance for m in metrics if m.sleep_performance]
        sleep_hours = [m.sleep_seconds / 3600 for m in metrics if m.sleep_seconds]
        bio_ages = [m.biological_age for m in metrics if m.biological_age]
        
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
                'sleep_hours': round(sum(sleep_hours) / len(sleep_hours), 1) if sleep_hours else None,
                'biological_age': round(sum(bio_ages) / len(bio_ages), 1) if bio_ages else None
            },
            'today': _metric_to_dict(metrics[0]) if metrics else None
        })
    
    @app.route('/api/metrics/trend', methods=['GET'])
    @token_required
    def get_trend(current_user):
        """Ottieni trend ultimi 30 giorni per grafici"""
        days = request.args.get('days', 30, type=int)
        start_date = date.today() - timedelta(days=days)
        
        metrics = DailyMetric.query.filter(
            DailyMetric.user_id == current_user.id,
            DailyMetric.date >= start_date
        ).order_by(DailyMetric.date.asc()).all()
        
        trend_data = []
        for m in metrics:
            trend_data.append({
                'date': m.date.isoformat(),
                'recovery': m.recovery_score,
                'strain': m.strain_score,
                'sleep_performance': m.sleep_performance,
                'sleep_hours': round(m.sleep_seconds / 3600, 1) if m.sleep_seconds else None,
                'biological_age': m.biological_age,
                'resting_hr': m.resting_hr,
                'steps': m.steps,
                'stress': m.stress_avg,
                'body_battery_high': m.body_battery_high,
                'body_battery_low': m.body_battery_low
            })
        
        # Calcola trend (miglioramento/peggioramento)
        if len(trend_data) >= 7:
            first_week = trend_data[:7]
            last_week = trend_data[-7:]
            
            def avg(lst, key):
                vals = [x[key] for x in lst if x[key] is not None]
                return sum(vals) / len(vals) if vals else None
            
            trends = {}
            for key in ['recovery', 'sleep_performance', 'biological_age', 'resting_hr', 'steps']:
                first_avg = avg(first_week, key)
                last_avg = avg(last_week, key)
                if first_avg and last_avg:
                    change = last_avg - first_avg
                    # Per bio age e RHR, diminuire è meglio
                    if key in ['biological_age', 'resting_hr']:
                        trends[key] = {'change': round(change, 1), 'improving': change < 0}
                    else:
                        trends[key] = {'change': round(change, 1), 'improving': change > 0}
                else:
                    trends[key] = None
        else:
            trends = {}
        
        return jsonify({
            'data': trend_data,
            'trends': trends
        })
    
    @app.route('/api/metrics/advice', methods=['GET'])
    @token_required
    def get_advice(current_user):
        """Genera consigli personalizzati basati sui dati"""
        week_ago = date.today() - timedelta(days=7)
        
        metrics = DailyMetric.query.filter(
            DailyMetric.user_id == current_user.id,
            DailyMetric.date >= week_ago
        ).order_by(DailyMetric.date.desc()).all()
        
        if not metrics:
            return jsonify({'advice': []})
        
        advice = []
        today = metrics[0] if metrics else None
        
        # Analisi Recovery
        if today and today.recovery_score:
            if today.recovery_score >= 80:
                advice.append({
                    'type': 'recovery',
                    'priority': 'success',
                    'coach': "🔥 SEI UNA MACCHINA! Recovery eccellente, oggi puoi spaccare!",
                    'zen': "L'energia scorre potente in te. Usa questa forza con saggezza.",
                    'action': "Allenamento intenso consigliato"
                })
            elif today.recovery_score >= 50:
                advice.append({
                    'type': 'recovery',
                    'priority': 'warning',
                    'coach': "💪 Recovery discreto. Ascolta il tuo corpo oggi.",
                    'zen': "Come l'acqua che trova la sua via, adatta l'intensità al momento.",
                    'action': "Allenamento moderato o tecnico"
                })
            else:
                advice.append({
                    'type': 'recovery',
                    'priority': 'danger',
                    'coach': "⚠️ STOP! Il tuo corpo chiede riposo. Rispettalo.",
                    'zen': "Anche la spada più affilata deve riposare nel fodero.",
                    'action': "Riposo attivo: stretching, camminata leggera, meditazione"
                })
        
        # Analisi Sonno
        avg_sleep = sum(m.sleep_seconds for m in metrics if m.sleep_seconds) / len([m for m in metrics if m.sleep_seconds]) if any(m.sleep_seconds for m in metrics) else 0
        avg_sleep_hours = avg_sleep / 3600 if avg_sleep else 0
        
        if avg_sleep_hours > 0:
            if avg_sleep_hours < 6:
                advice.append({
                    'type': 'sleep',
                    'priority': 'danger',
                    'coach': f"😴 ALLARME SONNO! Media di {avg_sleep_hours:.1f}h. Stai sabotando i tuoi progressi!",
                    'zen': "La notte è il tempio della rigenerazione. Onorala.",
                    'action': f"Obiettivo: vai a letto 1 ora prima stasera. Punta a 7-8 ore."
                })
            elif avg_sleep_hours < 7:
                advice.append({
                    'type': 'sleep',
                    'priority': 'warning',
                    'coach': f"😴 Sonno migliorabile. Media {avg_sleep_hours:.1f}h, servono 7-8h.",
                    'zen': "Il riposo profondo è la fonte della forza mattutina.",
                    'action': "Crea una routine serale: niente schermi 1h prima di dormire"
                })
        
        # Analisi Passi/Movimento
        avg_steps = sum(m.steps for m in metrics if m.steps) / len([m for m in metrics if m.steps]) if any(m.steps for m in metrics) else 0
        
        if avg_steps > 0:
            if avg_steps < 5000:
                advice.append({
                    'type': 'activity',
                    'priority': 'danger',
                    'coach': f"🚶 MUOVITI! Solo {int(avg_steps)} passi medi. Il corpo è fatto per muoversi!",
                    'zen': "L'acqua ferma diventa stagnante. Fluisci.",
                    'action': "Obiettivo oggi: 8000 passi. Fai una camminata di 30 minuti."
                })
            elif avg_steps < 8000:
                advice.append({
                    'type': 'activity',
                    'priority': 'warning',
                    'coach': f"🚶 {int(avg_steps)} passi medi. Buono, ma puoi fare di più!",
                    'zen': "Ogni passo è un seme di salute che pianti.",
                    'action': "Aggiungi una camminata dopo pranzo o dopo cena"
                })
            elif avg_steps >= 10000:
                advice.append({
                    'type': 'activity',
                    'priority': 'success',
                    'coach': f"🏃 GRANDE! {int(avg_steps)} passi medi. Continua così!",
                    'zen': "Il viaggio di mille miglia inizia con un passo. Tu ne fai molti.",
                    'action': "Mantieni questa costanza, sei sulla strada giusta"
                })
        
        # Analisi Stress
        avg_stress = sum(m.stress_avg for m in metrics if m.stress_avg) / len([m for m in metrics if m.stress_avg]) if any(m.stress_avg for m in metrics) else 0
        
        if avg_stress > 50:
            advice.append({
                'type': 'stress',
                'priority': 'danger',
                'coach': f"🧘 STRESS ALTO! Media {int(avg_stress)}. Il tuo sistema nervoso è sotto pressione.",
                'zen': "La mente agitata non riflette la verità. Trova la quiete.",
                'action': "5 minuti di respirazione profonda ora. Box breathing: 4-4-4-4."
            })
        elif avg_stress > 35:
            advice.append({
                'type': 'stress',
                'priority': 'warning',
                'coach': f"🧘 Stress moderato ({int(avg_stress)}). Attenzione a non accumulare.",
                'zen': "Come la corda dell'arco, non stare sempre in tensione.",
                'action': "Pianifica momenti di pausa durante la giornata"
            })
        
        # Analisi Età Biologica
        if today and today.biological_age:
            real_age = current_user.get_real_age()
            diff = today.biological_age - real_age
            
            if diff <= -3:
                advice.append({
                    'type': 'bio_age',
                    'priority': 'success',
                    'coach': f"🎯 FENOMENO! Età biologica {today.biological_age:.0f} vs anagrafica {real_age}. Sei {abs(diff):.0f} anni più giovane!",
                    'zen': "Il tempo scorre, ma tu fluisci controcorrente.",
                    'action': "Le tue abitudini funzionano. Mantieni questa rotta!"
                })
            elif diff <= 0:
                advice.append({
                    'type': 'bio_age',
                    'priority': 'success',
                    'coach': f"👍 Bene! Età biologica {today.biological_age:.0f} vs anagrafica {real_age}.",
                    'zen': "Stai onorando il tuo corpo. Continua.",
                    'action': "Piccoli miglioramenti portano grandi risultati nel tempo"
                })
            else:
                advice.append({
                    'type': 'bio_age',
                    'priority': 'warning',
                    'coach': f"⚠️ Età biologica {today.biological_age:.0f} vs anagrafica {real_age}. Puoi invertire la rotta!",
                    'zen': "Mai è troppo tardi per rinascere. Oggi è il giorno.",
                    'action': "Focus su: più movimento, più sonno, meno stress"
                })
        
        return jsonify({'advice': advice})
    
    # ========== ACTIVITIES ==========
    
    @app.route('/api/activities', methods=['GET'])
    @token_required
    def get_activities(current_user):
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
    
    # ========== HEALTH ==========
    
    @app.route('/api/health', methods=['GET'])
    def health_check():
        return jsonify({'status': 'ok', 'timestamp': datetime.utcnow().isoformat()})
    
    @app.route('/api', methods=['GET'])
    def api_info():
        return jsonify({
            'app': 'Garmin WHOOP - SENSEI',
            'version': '2.0.0',
            'endpoints': [
                'POST /api/register',
                'POST /api/login',
                'PUT /api/profile',
                'POST /api/garmin/connect',
                'POST /api/sync',
                'GET /api/metrics/today',
                'GET /api/metrics/range',
                'GET /api/metrics/summary',
                'GET /api/metrics/trend',
                'GET /api/metrics/advice',
                'GET /api/activities'
            ]
        })
    
    return app


def _metric_to_dict(m: DailyMetric) -> dict:
    return {
        'date': m.date.isoformat(),
        'scores': {
            'recovery': m.recovery_score,
            'strain': m.strain_score,
            'sleep_performance': m.sleep_performance,
            'biological_age': m.biological_age
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


app = create_app()

if __name__ == '__main__':
    app.run(debug=True, port=5000)