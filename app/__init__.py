"""
Garmin WHOOP - Flask App with AI Coaches
Version: 2.2.2 - Fixed HRV debug - 2024-12-07
"""

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
from functools import wraps
from sqlalchemy import or_
import jwt
import os

from config import Config
from app.models import db, User, DailyMetric, Activity, SyncLog, ChatMessage, UserMemory
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
        """Ottieni trend età biologica"""
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
        
        # Formatta dati di ieri
        y = context.get('yesterday', {})
        yesterday_text = f"""📅 {y.get('date', 'N/D')}
RECUPERO: {y.get('recovery', 'N/D')}% | Strain: {y.get('strain', 'N/D')}/21
SONNO: {y.get('sleep_hours', 'N/D')}h (Score: {y.get('sleep_score', 'N/D')}) | {y.get('sleep_start', '?')}-{y.get('sleep_end', '?')}
  Deep: {y.get('deep_sleep_min', 'N/D')}min | REM: {y.get('rem_sleep_min', 'N/D')}min | Light: {y.get('light_sleep_min', 'N/D')}min | Sveglio: {y.get('awake_min', 'N/D')}min
CUORE: RHR {y.get('rhr', 'N/D')}bpm | HRV {y.get('hrv', 'N/D')}ms | Max {y.get('max_hr', 'N/D')}bpm
STRESS: Media {y.get('stress_avg', 'N/D')} | Max {y.get('stress_max', 'N/D')} | Alto: {y.get('high_stress_min', 'N/D')}min
ATTIVITÀ: {y.get('steps', 'N/D')} passi | {y.get('distance_km', 'N/D')}km | {y.get('active_calories', 'N/D')}kcal attive | {y.get('floors', 'N/D')} piani
INTENSITÀ: Moderata {y.get('moderate_min', 'N/D')}min | Vigorosa {y.get('vigorous_min', 'N/D')}min
BODY BATTERY: {y.get('body_battery_low', 'N/D')}-{y.get('body_battery_high', 'N/D')} | +{y.get('body_battery_charged', 'N/D')} -{y.get('body_battery_drained', 'N/D')}
RESPIRO: {y.get('respiration', 'N/D')} resp/min | SpO2: {y.get('spo2', 'N/D')}%""" if y else "Non disponibili"
        
        # Formatta trend
        t = context.get('trend', {})
        trend_text = f"""Sonno: {'+' if (t.get('sleep_change', 0) or 0) >= 0 else ''}{t.get('sleep_change', 0)}h | Recovery: {'+' if (t.get('recovery_change', 0) or 0) >= 0 else ''}{t.get('recovery_change', 0)}% | RHR: {'+' if (t.get('rhr_change', 0) or 0) >= 0 else ''}{t.get('rhr_change', 0)}bpm
HRV: {'+' if (t.get('hrv_change', 0) or 0) >= 0 else ''}{t.get('hrv_change', 0)}ms | Passi: {'+' if (t.get('steps_change', 0) or 0) >= 0 else ''}{t.get('steps_change', 0)} | Stress: {'+' if (t.get('stress_change', 0) or 0) >= 0 else ''}{t.get('stress_change', 0)}""" if t else "Non disponibile"
        
        # Formatta attività recenti
        activities = context.get('recent_activities', [])
        activities_text = "\n".join([
            f"• {a['date']}: {a['name']} | {a['duration_min']}min | {a['distance_km']}km | {a['calories']}kcal | HR {a['avg_hr']}/{a['max_hr']}bpm | Strain {a['strain']} | Aerobico {a['aerobic_effect']} Anaerobico {a['anaerobic_effect']} | Z4:{a['hr_zone_4_min']}min Z5:{a['hr_zone_5_min']}min"
            for a in activities
        ]) if activities else "Nessuna attività recente"
        
        # Riepilogo settimana
        ws = context.get('week_activity_summary', {})
        week_summary = f"{ws.get('total_activities', 0)} attività | {round(ws.get('total_duration_min', 0))}min totali | {ws.get('total_distance_km', 0)}km | {ws.get('total_calories', 0)}kcal | Strain medio {ws.get('avg_strain', 'N/D')}" if ws else "N/D"
        
        return f"""Sei SENSEI (Dr. Sensei), preparatore atletico italiano con 25 anni di esperienza nel Performance Lab.
Parli con {name}, {age} anni.

CARATTERE: Diretto, pragmatico, motivante. Parli come un vero coach italiano.

COLLEGA: Dr. Sakura (coach mentale). Per temi emotivi/stress/ansia, suggerisci di parlare con lei.

══════════════════════════════════════
        DATI COMPLETI DI IERI
══════════════════════════════════════
{yesterday_text}

══════════════════════════════════════
           MEDIE 30 GIORNI
══════════════════════════════════════
ETÀ BIOLOGICA: {context.get('biological_age', 'N/D')} (reale: {age}) | VO2 Max: {context.get('vo2_max', 'N/D')}
SCORES: Recovery {context.get('recovery', 'N/D')}% | Strain {context.get('strain', 'N/D')}/21 | Sleep {context.get('sleep_performance', 'N/D')}%
CUORE: RHR {context.get('resting_hr', 'N/D')}bpm | HRV {context.get('hrv', 'N/D')}ms
SONNO: {context.get('sleep_hours', 'N/D')}h | Deep {context.get('deep_sleep_min', 'N/D')}min | REM {context.get('rem_sleep_min', 'N/D')}min
STRESS: {context.get('stress_avg', 'N/D')} media
ATTIVITÀ: {context.get('steps', 'N/D')} passi | {context.get('distance_km', 'N/D')}km | {context.get('active_calories', 'N/D')}kcal
INTENSITÀ: Moderata {context.get('moderate_min', 'N/D')}min | Vigorosa {context.get('vigorous_min', 'N/D')}min

══════════════════════════════════════
      TREND VS SETTIMANA SCORSA
══════════════════════════════════════
{trend_text}

══════════════════════════════════════
       ATTIVITÀ ULTIMI 7 GIORNI
══════════════════════════════════════
RIEPILOGO: {week_summary}

DETTAGLIO:
{activities_text}

══════════════════════════════════════
              MEMORIE
══════════════════════════════════════
{memories_text}

FOCUS: Allenamento, performance, recupero, prevenzione infortuni, nutrizione sportiva, analisi dati.
REGOLA: Salva info importanti con [MEMORY: categoria | contenuto]. Categorie: injury, goal, training, nutrition, performance
Rispondi in italiano, max 300 parole. USA I DATI SPECIFICI nelle risposte!"""

    def _get_sakura_prompt(user, context, memories):
        name = user.name or "amico"
        age = user.get_real_age()
        memories_text = "\n".join([f"- [{m.category}] {m.content}" for m in memories]) if memories else "Nessuna"
        
        # Formatta dati di ieri (focus benessere)
        y = context.get('yesterday', {})
        yesterday_text = f"""📅 {y.get('date', 'N/D')}
SONNO: {y.get('sleep_hours', 'N/D')}h (Score: {y.get('sleep_score', 'N/D')}) | Ora: {y.get('sleep_start', '?')}-{y.get('sleep_end', '?')}
  Deep: {y.get('deep_sleep_min', 'N/D')}min | REM: {y.get('rem_sleep_min', 'N/D')}min | Sveglio: {y.get('awake_min', 'N/D')}min
RECUPERO: {y.get('recovery', 'N/D')}%
STRESS: Media {y.get('stress_avg', 'N/D')} | Max {y.get('stress_max', 'N/D')}
  Riposo: {y.get('rest_stress_min', 'N/D')}min | Basso: {y.get('low_stress_min', 'N/D')}min | Medio: {y.get('medium_stress_min', 'N/D')}min | Alto: {y.get('high_stress_min', 'N/D')}min
CUORE: HRV {y.get('hrv', 'N/D')}ms | RHR {y.get('rhr', 'N/D')}bpm
BODY BATTERY: Min {y.get('body_battery_low', 'N/D')} → Max {y.get('body_battery_high', 'N/D')} | Caricato +{y.get('body_battery_charged', 'N/D')} | Consumato -{y.get('body_battery_drained', 'N/D')}
RESPIRO: {y.get('respiration', 'N/D')} resp/min | SpO2: {y.get('spo2', 'N/D')}%""" if y else "Non disponibili"
        
        # Formatta trend
        t = context.get('trend', {})
        trend_text = f"""Sonno: {'+' if (t.get('sleep_change', 0) or 0) >= 0 else ''}{t.get('sleep_change', 0)}h | Recovery: {'+' if (t.get('recovery_change', 0) or 0) >= 0 else ''}{t.get('recovery_change', 0)}%
HRV: {'+' if (t.get('hrv_change', 0) or 0) >= 0 else ''}{t.get('hrv_change', 0)}ms | Stress: {'+' if (t.get('stress_change', 0) or 0) >= 0 else ''}{t.get('stress_change', 0)}""" if t else "Non disponibile"
        
        return f"""Sei SAKURA (Dr. Sakura), coach mentale con background in psicologia dello sport e mindfulness nel Performance Lab.
Parli con {name}, {age} anni.

CARATTERE: Calma, empatica, saggia. Usi metafore dalla natura e filosofia orientale.

COLLEGA: Dr. Sensei (preparatore atletico). Per allenamenti/performance fisica, suggerisci di parlare con lui.

══════════════════════════════════════
       STATO BENESSERE DI IERI
══════════════════════════════════════
{yesterday_text}

══════════════════════════════════════
           MEDIE 30 GIORNI
══════════════════════════════════════
SONNO: {context.get('sleep_hours', 'N/D')}h media | Score {context.get('sleep_performance', 'N/D')}%
  Deep: {context.get('deep_sleep_min', 'N/D')}min | REM: {context.get('rem_sleep_min', 'N/D')}min
STRESS: {context.get('stress_avg', 'N/D')} media
CUORE: HRV {context.get('hrv', 'N/D')}ms | RHR {context.get('resting_hr', 'N/D')}bpm
RECUPERO: {context.get('recovery', 'N/D')}%
BODY BATTERY: {context.get('body_battery_low', 'N/D')}-{context.get('body_battery_high', 'N/D')}

══════════════════════════════════════
      TREND VS SETTIMANA SCORSA
══════════════════════════════════════
{trend_text}

══════════════════════════════════════
              MEMORIE
══════════════════════════════════════
{memories_text}

══════════════════════════════════════
       INTERPRETAZIONE DATI
══════════════════════════════════════
HRV (Variabilità Cardiaca):
- HRV alto (>50ms) = Sistema nervoso equilibrato, buona resilienza
- HRV basso (<30ms) = Stress accumulato, sistema in allerta

STRESS Garmin:
- <25 = Riposo/rilassato
- 25-50 = Basso/normale
- 50-75 = Medio
- >75 = Alto, serve recupero

DEEP SLEEP:
- Basso (<45min) = Possibile stress, ansia, sovraccarico mentale
- Ottimale (60-90min) = Buon recupero fisico e mentale

REM SLEEP:
- Basso (<60min) = Possibile esaurimento emotivo, elaborazione incompleta
- Ottimale (90-120min) = Buona elaborazione emotiva e memoria

BODY BATTERY:
- Mattina <50 = Non hai recuperato abbastanza
- Sera >30 = Buona gestione energie

═══ MODALITÀ MEDITAZIONE GUIDATA ═══
Quando l'utente chiede una meditazione, respirazione guidata, o rilassamento:
- IMPORTANTE: Usa ESATTAMENTE questo formato per le pause: [PAUSA:XX] dove XX sono i secondi
- NON scrivere "Pausa (20 sec)" o "**Pausa**" - scrivi SOLO [PAUSA:20]
- NON usare asterischi ** o formattazione markdown
- Parla in modo lento, calmo, con frasi brevi
- Usa molti "..." per creare ritmo lento

ESEMPIO CORRETTO di meditazione:
"Chiudi gli occhi... trova una posizione comoda... [PAUSA:10] Inspira profondamente dal naso... senti l'aria che riempie i polmoni... [PAUSA:20] Espira lentamente dalla bocca... lascia andare ogni tensione... [PAUSA:20] Continua a respirare... con calma... [PAUSA:30]"

ESEMPIO SBAGLIATO (non fare così):
"**Inizio** Chiudi gli occhi **Pausa (20 sec)**" ← NO!

FOCUS: Benessere mentale, gestione stress, mindfulness, equilibrio vita-sport, motivazione, crescita personale.
REGOLA: Salva info importanti con [MEMORY: categoria | contenuto]. Categorie: emotion, stress, mindset, relationship, sleep_mental, life_balance
Rispondi in italiano, max 400 parole per le meditazioni, 300 per il resto. USA I DATI SPECIFICI nelle risposte!"""

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
        }
        
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
        
        # ═══ ATTIVITÀ RECENTI (7gg) - DETTAGLIATE ═══
        activities = Activity.query.filter(
            Activity.user_id == user.id,
            Activity.start_time >= datetime.now() - timedelta(days=7)
        ).order_by(Activity.start_time.desc()).limit(10).all()
        
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
        
        context = _build_context(current_user)
        memories = UserMemory.query.filter_by(user_id=current_user.id, is_active=True).limit(20).all()
        
        # Get history
        if coach == 'sensei':
            history = ChatMessage.query.filter(
                ChatMessage.user_id == current_user.id,
                or_(ChatMessage.coach == 'sensei', ChatMessage.coach == None)
            ).order_by(ChatMessage.created_at.desc()).limit(10).all()
        else:
            history = ChatMessage.query.filter_by(user_id=current_user.id, coach=coach).order_by(ChatMessage.created_at.desc()).limit(10).all()
        history.reverse()
        
        system_prompt = _get_sakura_prompt(current_user, context, memories) if coach == 'sakura' else _get_sensei_prompt(current_user, context, memories)
        messages = [{"role": "system", "content": system_prompt}]
        messages += [{"role": m.role, "content": m.content} for m in history]
        messages.append({"role": "user", "content": msg})
        
        try:
            resp = openai_client.chat.completions.create(model="gpt-4o-mini", messages=messages, max_tokens=800, temperature=0.8)
            ai_raw = resp.choices[0].message.content
            
            # Save messages
            db.session.add(ChatMessage(user_id=current_user.id, role='user', content=msg, coach=coach))
            ai_clean = _extract_memories(ai_raw, current_user.id, coach)
            db.session.add(ChatMessage(user_id=current_user.id, role='assistant', content=ai_clean, coach=coach))
            db.session.commit()
            
            return jsonify({'response': ai_clean, 'coach': coach})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

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
    
    @app.route('/api/recalculate', methods=['POST'])
    @token_required
    def recalculate_bio_age(current_user):
        """Ricalcola età biologica con formula WHOOP-like"""
        
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
        
        for metric in metrics:
            total_impact = 0
            
            # 1. RHR (formula WHOOP: +1 anno ogni 10 bpm sopra 60)
            if metric.resting_hr:
                rhr = metric.resting_hr
                metric.bio_age_rhr_impact = round((rhr - 60) / 10, 1)
                metric.bio_age_rhr_impact = max(-3, min(3, metric.bio_age_rhr_impact))
                total_impact += metric.bio_age_rhr_impact
            else:
                metric.bio_age_rhr_impact = None
            
            # 2. VO2 Max (baseline 42, ogni 5 punti = 1 anno)
            if metric.vo2_max:
                vo2 = metric.vo2_max
                metric.bio_age_vo2_impact = round((42 - vo2) / 5, 1)
                metric.bio_age_vo2_impact = max(-3, min(3, metric.bio_age_vo2_impact))
                total_impact += metric.bio_age_vo2_impact
            else:
                metric.bio_age_vo2_impact = 0.0
            
            # 3. Sleep (ottimale 7-8.5h = -0.5, altrimenti penalità)
            if metric.sleep_seconds and metric.sleep_seconds > 0:
                sleep_hours = metric.sleep_seconds / 3600
                if sleep_hours >= 7 and sleep_hours <= 8.5:
                    metric.bio_age_sleep_impact = -0.5
                else:
                    diff = abs(sleep_hours - 7.5)
                    metric.bio_age_sleep_impact = round(diff * 0.6, 1)
                metric.bio_age_sleep_impact = max(-2, min(2, metric.bio_age_sleep_impact))
                total_impact += metric.bio_age_sleep_impact
            else:
                metric.bio_age_sleep_impact = None
            
            # 4. Steps (soglie meno severe)
            if metric.steps and metric.steps > 0:
                steps = metric.steps
                if steps >= 10000: metric.bio_age_steps_impact = -1.0
                elif steps >= 8000: metric.bio_age_steps_impact = -0.5
                elif steps >= 6000: metric.bio_age_steps_impact = 0.0
                elif steps >= 4000: metric.bio_age_steps_impact = 0.5
                else: metric.bio_age_steps_impact = 1.0
                total_impact += metric.bio_age_steps_impact
            else:
                metric.bio_age_steps_impact = None
            
            # 5. HR Zones
            moderate = metric.moderate_intensity_minutes or 0
            vigorous = metric.vigorous_intensity_minutes or 0
            intensity_score = moderate + (vigorous * 2)
            if intensity_score > 0:
                if intensity_score >= 45: metric.bio_age_hrz_impact = -1.0
                elif intensity_score >= 30: metric.bio_age_hrz_impact = -0.5
                elif intensity_score >= 15: metric.bio_age_hrz_impact = 0.0
                elif intensity_score >= 5: metric.bio_age_hrz_impact = 0.3
                else: metric.bio_age_hrz_impact = 0.5
                total_impact += metric.bio_age_hrz_impact
            else:
                metric.bio_age_hrz_impact = None
            
            # 6. Stress - NON usato in WHOOP
            metric.bio_age_stress_impact = None
            
            metric.biological_age = round(real_age + total_impact, 1)
            count += 1
        
        db.session.commit()
        return jsonify({'message': f'Ricalcolati {count} giorni (formula WHOOP)', 'count': count, 'real_age': real_age})
    
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
        """Debug: verifica dati HRV raw da Garmin"""
        from garminconnect import Garmin
        from datetime import date, timedelta
        import os
        
        if not current_user.garmin_email:
            return jsonify({'error': 'Garmin non connesso'}), 400
        
        # Ottieni password Garmin
        encryption_key = os.environ.get('ENCRYPTION_KEY')
        if not encryption_key or not current_user.garmin_password_encrypted:
            return jsonify({'error': 'Password Garmin non disponibile'}), 400
        
        from cryptography.fernet import Fernet
        fernet = Fernet(encryption_key.encode())
        garmin_password = fernet.decrypt(current_user.garmin_password_encrypted.encode()).decode()
        
        results = []
        
        try:
            client = Garmin(current_user.garmin_email, garmin_password)
            client.login()
            
            today = date.today()
            
            for i in range(3):
                day = today - timedelta(days=i)
                day_str = day.strftime('%Y-%m-%d')
                
                try:
                    hrv_data = client.get_hrv_data(day_str)
                    results.append({
                        'date': day_str,
                        'raw_hrv': hrv_data
                    })
                except Exception as e:
                    results.append({
                        'date': day_str,
                        'error': str(e)
                    })
                    
        except Exception as e:
            return jsonify({'error': f'Login Garmin fallito: {str(e)}'}), 500
        
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
                model="gpt-4o-mini",
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
        else:
            voice = 'nova'
            instructions = """Voice Affect: Soft, gentle, soothing; embody tranquility.
Tone: Calm, reassuring, peaceful; convey genuine warmth and serenity.
Pacing: Slow, deliberate, and unhurried; pause gently after instructions to allow the listener time to relax and follow along.
Emotion: Deeply soothing and comforting; express genuine kindness and care.
Pronunciation: Smooth, soft articulation, slightly elongating vowels to create a sense of ease.
Pauses: Use thoughtful pauses, especially between breathing instructions and visualization guidance, enhancing relaxation and mindfulness."""
        
        try:
            response = openai_client.audio.speech.create(
                model="gpt-4o-mini-tts",
                voice=voice,
                input=clean_text,
                instructions=instructions,
                response_format="mp3"
            )
            
            audio_b64 = base64.b64encode(response.content).decode('utf-8')
            return jsonify({'audio': audio_b64, 'format': 'mp3'})
            
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
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