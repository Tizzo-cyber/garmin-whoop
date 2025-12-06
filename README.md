# Garmin WHOOP

Un'alternativa a WHOOP che usa i dati del tuo Garmin per calcolare Recovery, Strain e Sleep Performance.

## Features

- 🔄 **Sync automatico** con Garmin Connect
- 📊 **Recovery Score** (0-100) basato su Body Battery, RHR e sonno
- 💪 **Strain Score** (0-21) basato su attività e HR zones
- 😴 **Sleep Performance** (0-100) basato su durata e qualità
- 👥 **Multi-utente** con autenticazione JWT
- 🔒 **Credenziali Garmin criptate** nel database

## API Endpoints

### Autenticazione
- `POST /api/register` - Registra nuovo utente
- `POST /api/login` - Login (ritorna JWT token)

### Garmin
- `POST /api/garmin/connect` - Collega account Garmin
- `POST /api/garmin/disconnect` - Scollega account
- `POST /api/sync` - Sincronizza dati ora

### Metriche
- `GET /api/metrics/today` - Metriche di oggi
- `GET /api/metrics/range?start=YYYY-MM-DD&end=YYYY-MM-DD` - Range di date
- `GET /api/metrics/summary` - Summary settimanale

### Attività
- `GET /api/activities?limit=20` - Lista attività

## Setup Locale

```bash
# 1. Clona e entra nella cartella
cd garmin_whoop

# 2. Crea virtual environment (opzionale ma consigliato)
python -m venv venv
source venv/bin/activate  # Linux/Mac
# oppure: venv\Scripts\activate  # Windows

# 3. Installa dipendenze
pip install -r requirements.txt

# 4. Configura environment
cp .env.example .env
# Edita .env con i tuoi valori

# 5. Avvia
python -m app
```

## Deploy su Railway

1. Crea un nuovo progetto su [Railway](https://railway.app)
2. Collega il tuo repository GitHub
3. Aggiungi le variabili d'ambiente:
   - `SECRET_KEY` - chiave segreta per JWT
   - `ENCRYPTION_KEY` - chiave per criptare password Garmin
4. (Opzionale) Aggiungi PostgreSQL dal marketplace Railway
5. Deploy!

### Variabili Railway
```
SECRET_KEY=<genera-una-stringa-random-lunga>
ENCRYPTION_KEY=<genera-una-stringa-di-almeno-32-caratteri>
```

## Uso API

### 1. Registrazione
```bash
curl -X POST https://tuo-app.railway.app/api/register \
  -H "Content-Type: application/json" \
  -d '{"email": "tuo@email.com", "password": "tuapassword"}'
```

### 2. Login
```bash
curl -X POST https://tuo-app.railway.app/api/login \
  -H "Content-Type: application/json" \
  -d '{"email": "tuo@email.com", "password": "tuapassword"}'
```

### 3. Collega Garmin
```bash
curl -X POST https://tuo-app.railway.app/api/garmin/connect \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <TOKEN>" \
  -d '{"garmin_email": "garmin@email.com", "garmin_password": "garminpassword"}'
```

### 4. Sync
```bash
curl -X POST https://tuo-app.railway.app/api/sync \
  -H "Authorization: Bearer <TOKEN>"
```

### 5. Ottieni metriche
```bash
curl https://tuo-app.railway.app/api/metrics/today \
  -H "Authorization: Bearer <TOKEN>"
```

## Struttura Database

### users
- id, email, password_hash
- garmin_email, garmin_password_encrypted
- last_sync, sync_enabled

### daily_metrics
- Metriche giornaliere (HR, sleep, stress, body battery, etc.)
- Scores calcolati (recovery, strain, sleep_performance)

### activities
- Singole attività/workout
- HR zones, training effect, strain

### sync_logs
- Log delle sincronizzazioni

## Calcolo Metriche

### Recovery Score (0-100)
- 40% Body Battery
- 30% RHR vs baseline
- 30% Qualità sonno (durata + fasi)

### Strain Score (0-21)
- Intensity minutes
- Calorie attive
- Stress level
- Training effect dalle attività

### Sleep Performance (0-100)
- 60% Durata (target 8h)
- 40% Qualità fasi (deep + REM)

## TODO

- [ ] Frontend dashboard
- [ ] Età biologica
- [ ] Consigli personalizzati
- [ ] Notifiche push
- [ ] Export dati
