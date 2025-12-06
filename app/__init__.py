"""
Garmin WHOOP - Flask App with AI Coaches
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
        yesterday = context.get('yesterday', {})
        yesterday_text = f"""Data: {yesterday.get('date', 'N/D')}
- Recovery: {yesterday.get('recovery', 'N/D')}%
- Sonno: {yesterday.get('sleep_hours', 'N/D')}h (Deep: {yesterday.get('deep_sleep_min', 'N/D')}min, REM: {yesterday.get('rem_sleep_min', 'N/D')}min)
- RHR: {yesterday.get('rhr', 'N/D')} bpm | HRV: {yesterday.get('hrv', 'N/D')}ms
- Passi: {yesterday.get('steps', 'N/D')} | Stress: {yesterday.get('stress', 'N/D')}
- Strain: {yesterday.get('strain', 'N/D')}/21 | Body Battery: {yesterday.get('body_battery', 'N/D')}""" if yesterday else "Non disponibili"
        
        # Formatta trend
        trend = context.get('trend', {})
        trend_text = f"Sonno: {'+' if trend.get('sleep_change', 0) >= 0 else ''}{trend.get('sleep_change', 0)}h | Recovery: {'+' if trend.get('recovery_change', 0) >= 0 else ''}{trend.get('recovery_change', 0)}% | RHR: {'+' if trend.get('rhr_change', 0) >= 0 else ''}{trend.get('rhr_change', 0)}bpm" if trend else "Non disponibile"
        
        # Formatta attività recenti
        activities = context.get('recent_activities', [])
        activities_text = "\n".join([f"- {a['date']}: {a['name']} ({a['duration_min']}min, {a['calories']}kcal, HR {a['avg_hr']}bpm, Strain {a['strain']})" for a in activities]) if activities else "Nessuna attività recente"
        
        return f"""Sei SENSEI (Dr. Sensei), preparatore atletico italiano con 25 anni di esperienza nel Performance Lab.
Parli con {name}, {age} anni.

CARATTERE: Diretto, pragmatico, motivante. Parli come un vero coach italiano.

LA TUA COLLEGA: Lavori con Dr. Sakura (coach mentale). Per temi emotivi/stress/ansia, suggerisci di parlare con lei.

═══ DATI DI IERI ═══
{yesterday_text}

═══ MEDIE 30 GIORNI ═══
- Età biologica: {context.get('biological_age', 'N/D')} (reale: {age})
- Recovery: {context.get('recovery', 'N/D')}% | Strain: {context.get('strain', 'N/D')}/21
- Sonno: {context.get('sleep_hours', 'N/D')}h | RHR: {context.get('resting_hr', 'N/D')}bpm | HRV: {context.get('hrv', 'N/D')}ms
- Passi: {context.get('steps', 'N/D')} | Stress: {context.get('stress_avg', 'N/D')} | VO2 Max: {context.get('vo2_max', 'N/D')}

═══ TREND (vs settimana scorsa) ═══
{trend_text}

═══ ATTIVITÀ RECENTI (7gg) ═══
{activities_text}

═══ MEMORIE ═══
{memories_text}

FOCUS: Allenamento, performance, recupero, prevenzione infortuni, nutrizione sportiva, analisi dati.
REGOLA: Salva info importanti con [MEMORY: categoria | contenuto]. Categorie: injury, goal, training, nutrition, performance
Rispondi in italiano, max 250 parole. Usa i dati specifici quando rispondi."""

    def _get_sakura_prompt(user, context, memories):
        name = user.name or "amico"
        age = user.get_real_age()
        memories_text = "\n".join([f"- [{m.category}] {m.content}" for m in memories]) if memories else "Nessuna"
        
        # Formatta dati di ieri (focus su benessere)
        yesterday = context.get('yesterday', {})
        yesterday_text = f"""- Sonno: {yesterday.get('sleep_hours', 'N/D')}h (Deep: {yesterday.get('deep_sleep_min', 'N/D')}min, REM: {yesterday.get('rem_sleep_min', 'N/D')}min)
- Recovery: {yesterday.get('recovery', 'N/D')}% | Body Battery: {yesterday.get('body_battery', 'N/D')}
- Stress: {yesterday.get('stress', 'N/D')} | HRV: {yesterday.get('hrv', 'N/D')}ms""" if yesterday else "Non disponibili"
        
        # Formatta trend
        trend = context.get('trend', {})
        trend_text = f"Sonno: {'+' if trend.get('sleep_change', 0) >= 0 else ''}{trend.get('sleep_change', 0)}h | Recovery: {'+' if trend.get('recovery_change', 0) >= 0 else ''}{trend.get('recovery_change', 0)}%" if trend else "Non disponibile"
        
        return f"""Sei SAKURA (Dr. Sakura), coach mentale con background in psicologia dello sport e mindfulness nel Performance Lab.
Parli con {name}, {age} anni.

CARATTERE: Calma, empatica, saggia. Usi metafore dalla natura e filosofia orientale.

IL TUO COLLEGA: Lavori con Dr. Sensei (preparatore atletico). Per allenamenti/performance fisica, suggerisci di parlare con lui.

═══ STATO DI IERI ═══
{yesterday_text}

═══ MEDIE 30 GIORNI ═══
- Stress medio: {context.get('stress_avg', 'N/D')}
- Sonno: {context.get('sleep_hours', 'N/D')}h
- Recovery: {context.get('recovery', 'N/D')}%
- HRV: {context.get('hrv', 'N/D')}ms (indicatore equilibrio sistema nervoso)

═══ TREND (vs settimana scorsa) ═══
{trend_text}

═══ MEMORIE ═══
{memories_text}

INTERPRETAZIONE DATI:
- HRV alto + Stress basso = buon equilibrio, sistema nervoso rilassato
- HRV basso + Stress alto = sovraccarico, serve recupero mentale
- Sonno Deep basso = possibile stress o ansia
- Sonno REM basso = possibile esaurimento emotivo

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
Rispondi in italiano, max 400 parole per le meditazioni, 250 per il resto."""

    def _build_context(user):
        """Costruisce contesto dettagliato per i coach AI"""
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
        
        # Dati medi 30 giorni
        context = {
            'biological_age': avg([m.biological_age for m in metrics]),
            'recovery': avg([m.recovery_score for m in metrics]),
            'sleep_hours': avg([m.sleep_seconds/3600 if m.sleep_seconds else None for m in metrics]),
            'resting_hr': avg([m.resting_hr for m in metrics]),
            'hrv': avg([m.hrv_last_night for m in metrics]),
            'steps': avg([m.steps for m in metrics]),
            'stress_avg': avg([m.stress_avg for m in metrics]),
            'strain': avg([m.strain_score for m in metrics]),
            'vo2_max': avg([m.vo2_max for m in metrics]),
        }
        
        # Dati di IERI (ultimo giorno con dati)
        yesterday = metrics[0] if metrics else None
        if yesterday:
            context['yesterday'] = {
                'date': yesterday.date.strftime('%d/%m'),
                'recovery': yesterday.recovery_score,
                'sleep_hours': round(yesterday.sleep_seconds / 3600, 1) if yesterday.sleep_seconds else None,
                'deep_sleep_min': round(yesterday.deep_sleep_seconds / 60) if yesterday.deep_sleep_seconds else None,
                'rem_sleep_min': round(yesterday.rem_sleep_seconds / 60) if yesterday.rem_sleep_seconds else None,
                'rhr': yesterday.resting_hr,
                'hrv': yesterday.hrv_last_night,
                'steps': yesterday.steps,
                'stress': yesterday.stress_avg,
                'strain': yesterday.strain_score,
                'body_battery': yesterday.body_battery_high,
            }
        
        # Trend ultima settimana vs settimana precedente
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
            }
        
        # Attività recenti (ultime 5)
        activities = Activity.query.filter(
            Activity.user_id == user.id,
            Activity.start_time >= datetime.now() - timedelta(days=7)
        ).order_by(Activity.start_time.desc()).limit(5).all()
        
        if activities:
            context['recent_activities'] = [{
                'name': a.activity_name or a.activity_type,
                'date': a.start_time.strftime('%d/%m') if a.start_time else None,
                'duration_min': round(a.duration_seconds / 60) if a.duration_seconds else None,
                'calories': a.calories,
                'avg_hr': a.avg_hr,
                'strain': a.strain_score,
            } for a in activities]
        
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
            'steps': m.steps,
            'stress_avg': m.stress_avg,
            'bio_age_sleep_impact': m.bio_age_sleep_impact,
            'bio_age_rhr_impact': m.bio_age_rhr_impact,
            'bio_age_steps_impact': m.bio_age_steps_impact,
            'bio_age_stress_impact': m.bio_age_stress_impact,
            'bio_age_hrz_impact': m.bio_age_hrz_impact,
            'biological_age': m.biological_age
        } for m in metrics])
    
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
        """Converte testo in audio usando OpenAI TTS"""
        if not openai_client:
            return jsonify({'error': 'OpenAI non configurato'}), 500
        
        data = request.get_json()
        text = data.get('text', '')[:1500]  # Max 1500 caratteri per meditazioni
        coach = data.get('coach', 'sensei')
        
        if not text:
            return jsonify({'error': 'Testo vuoto'}), 400
        
        # Voci e velocità diverse per i coach
        # Sensei: onyx (profonda, maschile), velocità normale
        # Sakura: shimmer (soft, femminile), velocità lenta per meditazioni
        if coach == 'sensei':
            voice = 'onyx'
            speed = 1.0
        else:
            voice = 'shimmer'  # Più soft di nova
            speed = 0.85  # Più lenta, perfetta per meditazioni
        
        try:
            response = openai_client.audio.speech.create(
                model="tts-1-hd",
                voice=voice,
                input=text,
                speed=speed,
                response_format="mp3"
            )
            
            # Ritorna audio come base64
            import base64
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