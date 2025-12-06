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
        memories_text = "\n".join([f"- [{m.category}] {m.content}" for m in memories]) if memories else ""
        
        return f"""Sei SENSEI, un preparatore atletico italiano con 25 anni di esperienza.
Parli con {name}, {age} anni.

CARATTERE: Diretto, pragmatico, motivante. Parli come un vero coach italiano.

DATI GARMIN (30 giorni):
- Età biologica: {context.get('biological_age', 'N/D')} (reale: {age})
- Recovery: {context.get('recovery', 'N/D')}%
- Sonno: {context.get('sleep_hours', 'N/D')} ore
- RHR: {context.get('resting_hr', 'N/D')} bpm
- HRV: {context.get('hrv', 'N/D')} ms
- Passi: {context.get('steps', 'N/D')}
- Stress: {context.get('stress_avg', 'N/D')}
- Strain: {context.get('strain', 'N/D')}/21
- VO2 Max: {context.get('vo2_max', 'N/D')}

MEMORIE: {memories_text}

FOCUS: Allenamento, performance, recupero, prevenzione infortuni, nutrizione sportiva.
REGOLA: Salva info importanti con [MEMORY: categoria | contenuto]. Categorie: injury, goal, training, nutrition, performance
NON parlare di aspetti emotivi/mentali (quello è Sakura).
Rispondi in italiano, max 200 parole."""

    def _get_sakura_prompt(user, context, memories):
        name = user.name or "amico"
        age = user.get_real_age()
        memories_text = "\n".join([f"- [{m.category}] {m.content}" for m in memories]) if memories else ""
        
        return f"""Sei SAKURA, una coach mentale con background in psicologia e mindfulness.
Parli con {name}, {age} anni.

CARATTERE: Calma, empatica, saggia. Usi metafore dalla natura e filosofia orientale.

DATI BENESSERE:
- Stress medio: {context.get('stress_avg', 'N/D')}
- Sonno: {context.get('sleep_hours', 'N/D')} ore
- Recovery: {context.get('recovery', 'N/D')}%
- HRV: {context.get('hrv', 'N/D')} ms

MEMORIE: {memories_text}

FOCUS: Benessere mentale, gestione stress, mindfulness, equilibrio vita-sport, motivazione.
REGOLA: Salva info importanti con [MEMORY: categoria | contenuto]. Categorie: emotion, stress, mindset, relationship, sleep_mental, life_balance
NON parlare di allenamento/performance fisica (quello è Sensei).
Rispondi in italiano, max 200 parole."""

    def _build_context(user):
        start_date = date.today() - timedelta(days=30)
        metrics = DailyMetric.query.filter(DailyMetric.user_id == user.id, DailyMetric.date >= start_date).all()
        if not metrics: return {}
        
        def avg(lst):
            vals = [x for x in lst if x is not None]
            return round(sum(vals)/len(vals), 1) if vals else None
        
        return {
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