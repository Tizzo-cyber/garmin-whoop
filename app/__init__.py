"""
SENSEI & SAKURA - Dual AI Coach System
"""

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
from functools import wraps
from sqlalchemy import text, or_
import jwt
import os
import json

from config import Config
from app.models import db, User, DailyMetric, Activity, SyncLog, ChatMessage, UserMemory
from app.garmin_sync import GarminSyncService

from openai import OpenAI


def create_app():
    app = Flask(__name__, static_folder='../static', static_url_path='')
    app.config.from_object(Config)
    
    db.init_app(app)
    CORS(app)
    
    openai_client = None
    if os.environ.get('OPENAI_API_KEY'):
        openai_client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
    
    with app.app_context():
        db.create_all()
        
        migrations = [
            'ALTER TABLE users ADD COLUMN IF NOT EXISTS birth_year INTEGER',
            'ALTER TABLE users ADD COLUMN IF NOT EXISTS name VARCHAR(100)',
            'ALTER TABLE users ADD COLUMN IF NOT EXISTS sport_goals TEXT',
            'ALTER TABLE users ADD COLUMN IF NOT EXISTS injuries TEXT',
            'ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS biological_age FLOAT',
            'ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS vo2_max FLOAT',
            'ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS bio_age_rhr_impact FLOAT',
            'ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS bio_age_vo2_impact FLOAT',
            'ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS bio_age_sleep_impact FLOAT',
            'ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS bio_age_steps_impact FLOAT',
            'ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS bio_age_stress_impact FLOAT',
            'ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS bio_age_hrz_impact FLOAT',
            'ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS coach VARCHAR(20)',
            'ALTER TABLE user_memories ADD COLUMN IF NOT EXISTS coach VARCHAR(20)',
        ]
        for sql in migrations:
            try:
                db.session.execute(text(sql))
                db.session.commit()
            except:
                db.session.rollback()
        
        try:
            db.session.execute(text('''
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id),
                    role VARCHAR(20) NOT NULL,
                    content TEXT NOT NULL,
                    coach VARCHAR(20),
                    context_summary TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            '''))
            db.session.execute(text('''
                CREATE TABLE IF NOT EXISTS user_memories (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id),
                    category VARCHAR(50),
                    content TEXT NOT NULL,
                    coach VARCHAR(20),
                    is_active BOOLEAN DEFAULT TRUE,
                    source_message_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            '''))
            db.session.commit()
        except:
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
    
    # ==================== AUTH ====================
    
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
            birth_year=data.get('birth_year'),
            name=data.get('name')
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
                'name': user.name,
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
        if data.get('name'):
            current_user.name = data['name']
        if data.get('sport_goals'):
            current_user.sport_goals = data['sport_goals']
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
    
    @app.route('/api/sync', methods=['POST'])
    @token_required
    def sync_now(current_user):
        if not current_user.garmin_email:
            return jsonify({'error': 'Account Garmin non collegato'}), 400
        days_back = request.get_json().get('days_back', 7) if request.get_json() else 7
        service = GarminSyncService(app.config['ENCRYPTION_KEY'])
        result = service.sync_user(current_user, days_back=days_back)
        return jsonify(result)
    
    # ==================== METRICS ====================
    
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
        
        return jsonify({
            'period': {'start': start_date.isoformat(), 'end': date.today().isoformat(), 'days_with_data': len(metrics)},
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
            'steps': m.steps,
        } for m in metrics]
        
        pace_of_aging = None
        if len(metrics) >= 30:
            recent = [m.biological_age for m in metrics[-15:] if m.biological_age]
            older = [m.biological_age for m in metrics[:15] if m.biological_age]
            if recent and older:
                pace_of_aging = round((sum(recent)/len(recent) - sum(older)/len(older)) * 12, 1)
        
        return jsonify({'data': trend_data, 'real_age': current_user.get_real_age(), 'pace_of_aging': pace_of_aging})
    
    @app.route('/api/activities', methods=['GET'])
    @token_required
    def get_activities(current_user):
        limit = request.args.get('limit', 10, type=int)
        activities = Activity.query.filter_by(user_id=current_user.id).order_by(Activity.start_time.desc()).limit(limit).all()
        return jsonify([{
            'id': a.id, 'name': a.activity_name, 'type': a.activity_type,
            'start_time': a.start_time.isoformat() if a.start_time else None,
            'duration_minutes': round(a.duration_seconds / 60, 1) if a.duration_seconds else None,
            'calories': a.calories, 'avg_hr': a.avg_hr, 'strain': a.strain_score
        } for a in activities])
    
    # ==================== DUAL AI COACH ====================
    
    def _get_sensei_prompt(user, context, memories):
        name = user.name or "atleta"
        age = user.get_real_age()
        
        memories_text = ""
        if memories:
            memories_text = "\n\nINFORMAZIONI RICORDATE:\n"
            for m in memories:
                memories_text += f"- [{m.category}] {m.content}\n"
        
        return f"""Sei SENSEI, un preparatore atletico e medico sportivo con 25 anni di esperienza.
Parli con {name}, {age} anni.

IL TUO CARATTERE:
- Sei diretto, pragmatico, motivante ma mai duro
- Parli come un vero preparatore atletico italiano
- Usi un linguaggio semplice e chiaro
- Sei appassionato di performance e ottimizzazione
- Dai sempre consigli pratici e attuabili

DATI GARMIN ATTUALI (30 giorni):
- Età biologica: {context.get('biological_age', 'N/D')} anni (reale: {age})
- Recovery: {context.get('recovery', 'N/D')}%
- Sonno medio: {context.get('sleep_hours', 'N/D')} ore
- RHR: {context.get('resting_hr', 'N/D')} bpm
- HRV: {context.get('hrv', 'N/D')} ms
- Passi medi: {context.get('steps', 'N/D')}
- Stress: {context.get('stress_avg', 'N/D')}
- Strain: {context.get('strain', 'N/D')}/21
- VO2 Max: {context.get('vo2_max', 'N/D')}
{memories_text}

IL TUO FOCUS:
1. Allenamento e performance fisica
2. Recupero e prevenzione infortuni  
3. Analisi dati Garmin e metriche
4. Pianificazione training
5. Nutrizione sportiva base

REGOLE:
- Se l'utente menziona infortuni/dolori/obiettivi, salvali con [MEMORY: categoria | info]
- Categorie: injury, goal, training, nutrition, performance
- Chiedi follow-up su infortuni precedenti
- Non fare diagnosi mediche, ma consigli sportivi
- Usa i dati Garmin per personalizzare ogni risposta
- Rispondi in italiano, max 200 parole
- NON parlare di aspetti mentali/emotivi (quello è compito di Sakura)
"""

    def _get_sakura_prompt(user, context, memories):
        name = user.name or "amico"
        age = user.get_real_age()
        
        memories_text = ""
        if memories:
            memories_text = "\n\nINFORMAZIONI RICORDATE:\n"
            for m in memories:
                memories_text += f"- [{m.category}] {m.content}\n"
        
        return f"""Sei SAKURA, una coach mentale e guida spirituale con formazione in psicologia dello sport e mindfulness.
Parli con {name}, {age} anni.

IL TUO CARATTERE:
- Sei calma, empatica, profonda ma accessibile
- Parli con dolcezza ma anche con saggezza
- Usi metafore dalla natura e filosofia orientale
- Sei interessata al benessere interiore della persona
- Ascolti prima di consigliare

DATI BENESSERE (indicatori di stress/recupero):
- Livello stress medio: {context.get('stress_avg', 'N/D')}
- Qualità sonno: {context.get('sleep_hours', 'N/D')} ore
- Recovery (energia): {context.get('recovery', 'N/D')}%
- HRV (equilibrio nervoso): {context.get('hrv', 'N/D')} ms
{memories_text}

IL TUO FOCUS:
1. Benessere mentale e emotivo
2. Gestione dello stress e ansia
3. Mindfulness e meditazione
4. Equilibrio vita-sport
5. Motivazione profonda e scopo
6. Qualità del sonno (aspetto mentale)
7. Relazioni e supporto sociale

REGOLE:
- Se l'utente menziona emozioni/pensieri/difficoltà, salvali con [MEMORY: categoria | info]
- Categorie: emotion, stress, mindset, relationship, sleep_mental, life_balance
- Chiedi come si sente, non solo cosa fa
- Offri tecniche pratiche (respirazione, meditazione breve)
- Non sei una psicologa clinica, ma una coach
- Usa i dati di stress/sonno per capire il suo stato
- Rispondi in italiano, max 200 parole
- NON parlare di allenamento/performance fisica (quello è compito di Sensei)
"""
    
    def _build_context(user):
        start_date = date.today() - timedelta(days=30)
        metrics = DailyMetric.query.filter(
            DailyMetric.user_id == user.id,
            DailyMetric.date >= start_date
        ).all()
        
        if not metrics:
            return {}
        
        def safe_avg(lst):
            vals = [x for x in lst if x is not None]
            return round(sum(vals) / len(vals), 1) if vals else None
        
        return {
            'biological_age': safe_avg([m.biological_age for m in metrics]),
            'recovery': safe_avg([m.recovery_score for m in metrics]),
            'sleep_hours': safe_avg([m.sleep_seconds / 3600 if m.sleep_seconds else None for m in metrics]),
            'resting_hr': safe_avg([m.resting_hr for m in metrics]),
            'hrv': safe_avg([m.hrv_last_night for m in metrics]),
            'steps': safe_avg([m.steps for m in metrics]),
            'stress_avg': safe_avg([m.stress_avg for m in metrics]),
            'strain': safe_avg([m.strain_score for m in metrics]),
            'vo2_max': safe_avg([m.vo2_max for m in metrics]),
        }
    
    def _extract_memories(ai_response, user_id, message_id, coach):
        import re
        pattern = r'\[MEMORY:\s*(\w+)\s*\|\s*([^\]]+)\]'
        matches = re.findall(pattern, ai_response)
        
        for category, content in matches:
            memory = UserMemory(
                user_id=user_id,
                category=category.lower(),
                content=content.strip(),
                coach=coach,
                source_message_id=message_id
            )
            db.session.add(memory)
        
        clean_response = re.sub(pattern, '', ai_response).strip()
        return clean_response
    
    @app.route('/api/chat', methods=['POST'])
    @token_required
    def chat_with_ai(current_user):
        if not openai_client:
            return jsonify({'error': 'OpenAI non configurato'}), 500
        
        data = request.get_json()
        user_message = data.get('message', '').strip()
        coach = data.get('coach', 'sensei')
        
        if not user_message:
            return jsonify({'error': 'Messaggio vuoto'}), 400
        
        context = _build_context(current_user)
        
        # Get all memories (both coaches share)
        memories = UserMemory.query.filter_by(user_id=current_user.id, is_active=True)\
            .order_by(UserMemory.created_at.desc()).limit(20).all()
        
        # Get chat history for this coach
        if coach == 'sensei':
            recent_messages = ChatMessage.query.filter(
                ChatMessage.user_id == current_user.id,
                or_(ChatMessage.coach == 'sensei', ChatMessage.coach == None)
            ).order_by(ChatMessage.created_at.desc()).limit(10).all()
        else:
            recent_messages = ChatMessage.query.filter(
                ChatMessage.user_id == current_user.id,
                ChatMessage.coach == coach
            ).order_by(ChatMessage.created_at.desc()).limit(10).all()
        
        recent_messages.reverse()
        
        if coach == 'sakura':
            system_prompt = _get_sakura_prompt(current_user, context, memories)
        else:
            system_prompt = _get_sensei_prompt(current_user, context, memories)
        
        messages = [{"role": "system", "content": system_prompt}]
        for msg in recent_messages:
            messages.append({"role": msg.role, "content": msg.content})
        messages.append({"role": "user", "content": user_message})
        
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=800,
                temperature=0.8
            )
            
            ai_response_raw = response.choices[0].message.content
            
            user_msg = ChatMessage(
                user_id=current_user.id,
                role='user',
                content=user_message,
                coach=coach,
                context_summary=json.dumps(context)
            )
            db.session.add(user_msg)
            db.session.flush()
            
            ai_response = _extract_memories(ai_response_raw, current_user.id, user_msg.id, coach)
            
            ai_msg = ChatMessage(
                user_id=current_user.id,
                role='assistant',
                content=ai_response,
                coach=coach
            )
            db.session.add(ai_msg)
            db.session.commit()
            
            return jsonify({'response': ai_response, 'coach': coach})
            
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/chat/history', methods=['GET'])
    @token_required
    def get_chat_history(current_user):
        coach = request.args.get('coach', 'sensei')
        limit = request.args.get('limit', 50, type=int)
        
        if coach == 'sensei':
            messages = ChatMessage.query.filter(
                ChatMessage.user_id == current_user.id,
                or_(ChatMessage.coach == 'sensei', ChatMessage.coach == None)
            ).order_by(ChatMessage.created_at.desc()).limit(limit).all()
        else:
            messages = ChatMessage.query.filter(
                ChatMessage.user_id == current_user.id,
                ChatMessage.coach == coach
            ).order_by(ChatMessage.created_at.desc()).limit(limit).all()
        
        messages.reverse()
        return jsonify([{'role': m.role, 'content': m.content, 'created_at': m.created_at.isoformat()} for m in messages])
    
    @app.route('/api/chat/memories', methods=['GET'])
    @token_required
    def get_memories(current_user):
        memories = UserMemory.query.filter_by(user_id=current_user.id, is_active=True)\
            .order_by(UserMemory.created_at.desc()).all()
        return jsonify([{
            'id': m.id, 'category': m.category, 'content': m.content, 
            'coach': m.coach or 'sensei', 'created_at': m.created_at.isoformat()
        } for m in memories])
    
    @app.route('/api/chat/memories/<int:memory_id>', methods=['DELETE'])
    @token_required
    def delete_memory(current_user, memory_id):
        memory = UserMemory.query.filter_by(id=memory_id, user_id=current_user.id).first()
        if memory:
            memory.is_active = False
            db.session.commit()
        return jsonify({'message': 'Memoria rimossa'})
    
    @app.route('/api/health', methods=['GET'])
    def health_check():
        return jsonify({'status': 'ok', 'ai': bool(openai_client)})
    
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
            'rhr': m.bio_age_rhr_impact, 'vo2': m.bio_age_vo2_impact,
            'sleep': m.bio_age_sleep_impact, 'steps': m.bio_age_steps_impact,
            'stress': m.bio_age_stress_impact, 'hrz': m.bio_age_hrz_impact
        },
        'real_age': user.get_real_age(),
        'heart': {'resting_hr': m.resting_hr, 'vo2_max': m.vo2_max, 'hrv': m.hrv_last_night},
        'sleep': {
            'total_hours': round(m.sleep_seconds / 3600, 1) if m.sleep_seconds else None,
        },
        'stress': {'average': m.stress_avg},
        'activity': {'steps': m.steps, 'active_calories': m.active_calories}
    }


app = create_app()

if __name__ == '__main__':
    app.run(debug=True, port=5000)