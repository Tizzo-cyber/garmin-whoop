"""
Garmin Sync Service
Recupera dati da Garmin Connect e li salva nel database
"""

from datetime import date, datetime, timedelta
from garminconnect import Garmin
from app.models import db, User, DailyMetric, Activity, SyncLog
import json
import traceback


class GarminSyncService:
    
    def __init__(self, encryption_key: str):
        self.encryption_key = encryption_key
    
    def sync_user(self, user: User, days_back: int = 7, offset_days: int = 0) -> dict:
        """
        Sincronizza i dati di un utente.
        
        Args:
            user: User da sincronizzare
            days_back: Quanti giorni sincronizzare
            offset_days: Da quale giorno partire (0 = oggi, 30 = 30 giorni fa)
        
        Returns:
            dict con risultato sync
        """
        log = SyncLog(user_id=user.id, status='running')
        db.session.add(log)
        db.session.commit()
        
        result = {
            'success': False,
            'metrics_synced': 0,
            'activities_synced': 0,
            'errors': []
        }
        
        try:
            # Login a Garmin
            garmin_password = user.get_garmin_password(self.encryption_key)
            if not user.garmin_email or not garmin_password:
                raise ValueError("Credenziali Garmin non configurate")
            
            client = Garmin(user.garmin_email, garmin_password)
            client.login()
            
            # Sync metriche giornaliere (con offset)
            for i in range(days_back):
                day = date.today() - timedelta(days=i + offset_days)
                try:
                    synced = self._sync_daily_metrics(client, user, day)
                    if synced:
                        result['metrics_synced'] += 1
                except Exception as e:
                    result['errors'].append(f"Metrics {day}: {str(e)}")
            
            # Sync attività recenti (solo se offset=0)
            if offset_days == 0:
                try:
                    activities = client.get_activities(0, 20)  # Ultime 20
                    for act in activities:
                        try:
                            synced = self._sync_activity(user, act)
                            if synced:
                                result['activities_synced'] += 1
                        except Exception as e:
                            result['errors'].append(f"Activity {act.get('activityId')}: {str(e)}")
                except Exception as e:
                    result['errors'].append(f"Activities fetch: {str(e)}")
            
            # Update user last sync
            user.last_sync = datetime.utcnow()
            
            # Update log
            log.status = 'success' if not result['errors'] else 'partial'
            log.metrics_synced = result['metrics_synced']
            log.activities_synced = result['activities_synced']
            if result['errors']:
                log.error_message = '\n'.join(result['errors'][:10])
            
            result['success'] = True
            
        except Exception as e:
            log.status = 'error'
            log.error_message = f"{str(e)}\n{traceback.format_exc()}"
            result['errors'].append(str(e))
        
        finally:
            log.finished_at = datetime.utcnow()
            db.session.commit()
        
        return result
    
    def _sync_daily_metrics(self, client: Garmin, user: User, day: date) -> bool:
        """Sincronizza le metriche di un giorno specifico"""
        
        day_str = day.isoformat()
        
        # Controlla se esiste già
        existing = DailyMetric.query.filter_by(user_id=user.id, date=day).first()
        if existing:
            metric = existing
        else:
            metric = DailyMetric(user_id=user.id, date=day)
        
        # Fetch dati da Garmin
        raw_data = {}
        
        # Daily summary
        try:
            summary = client.get_stats(day_str)
            raw_data['summary'] = summary
            
            metric.resting_hr = summary.get('restingHeartRate')
            metric.min_hr = summary.get('minHeartRate')
            metric.max_hr = summary.get('maxHeartRate')
            metric.steps = summary.get('totalSteps')
            metric.total_calories = int(summary.get('totalKilocalories') or 0)
            metric.active_calories = int(summary.get('activeKilocalories') or 0)
            metric.distance_meters = summary.get('totalDistanceMeters')
            metric.floors_ascended = summary.get('floorsAscended')
            metric.moderate_intensity_minutes = summary.get('moderateIntensityMinutes')
            metric.vigorous_intensity_minutes = summary.get('vigorousIntensityMinutes')
            metric.active_seconds = summary.get('activeSeconds')
            metric.sedentary_seconds = summary.get('sedentarySeconds')
            
            # Stress
            metric.stress_avg = summary.get('averageStressLevel')
            metric.stress_max = summary.get('maxStressLevel')
            metric.rest_stress_duration = summary.get('restStressDuration')
            metric.low_stress_duration = summary.get('lowStressDuration')
            metric.medium_stress_duration = summary.get('mediumStressDuration')
            metric.high_stress_duration = summary.get('highStressDuration')
            
            # Body Battery
            metric.body_battery_high = summary.get('bodyBatteryHighestValue')
            metric.body_battery_low = summary.get('bodyBatteryLowestValue')
            metric.body_battery_charged = summary.get('bodyBatteryChargedValue')
            metric.body_battery_drained = summary.get('bodyBatteryDrainedValue')
            
            # Respiration
            metric.avg_respiration = summary.get('avgWakingRespirationValue')
            metric.min_respiration = summary.get('lowestRespirationValue')
            metric.max_respiration = summary.get('highestRespirationValue')
            
        except Exception as e:
            raw_data['summary_error'] = str(e)
        
        # Sleep data
        try:
            sleep = client.get_sleep_data(day_str)
            raw_data['sleep'] = sleep
            
            daily_sleep = sleep.get('dailySleepDTO', {})
            metric.sleep_seconds = daily_sleep.get('sleepTimeSeconds')
            metric.deep_sleep_seconds = daily_sleep.get('deepSleepSeconds')
            metric.light_sleep_seconds = daily_sleep.get('lightSleepSeconds')
            metric.rem_sleep_seconds = daily_sleep.get('remSleepSeconds')
            metric.awake_seconds = daily_sleep.get('awakeSleepSeconds')
            
            # Sleep times
            if daily_sleep.get('sleepStartTimestampLocal'):
                try:
                    metric.sleep_start = datetime.fromisoformat(
                        daily_sleep['sleepStartTimestampLocal'].replace('.0', '')
                    )
                except:
                    pass
            if daily_sleep.get('sleepEndTimestampLocal'):
                try:
                    metric.sleep_end = datetime.fromisoformat(
                        daily_sleep['sleepEndTimestampLocal'].replace('.0', '')
                    )
                except:
                    pass
                    
            # Sleep score (se disponibile)
            sleep_scores = daily_sleep.get('sleepScores', {})
            if sleep_scores:
                metric.sleep_score = sleep_scores.get('overall', {}).get('value')
                
        except Exception as e:
            raw_data['sleep_error'] = str(e)
        
        # HRV (se disponibile)
        try:
            hrv = client.get_hrv_data(day_str)
            raw_data['hrv'] = hrv
            
            if hrv:
                hrv_summary = hrv.get('hrvSummary', hrv)
                metric.hrv_weekly_avg = hrv_summary.get('weeklyAvg')
                metric.hrv_last_night = hrv_summary.get('lastNightAvg')
        except Exception as e:
            raw_data['hrv_error'] = str(e)
        
        # SpO2
        try:
            spo2 = client.get_spo2_data(day_str)
            raw_data['spo2'] = spo2
            
            if spo2:
                metric.avg_spo2 = spo2.get('averageSpO2')
                metric.min_spo2 = spo2.get('lowestSpO2')
        except Exception as e:
            raw_data['spo2_error'] = str(e)
        
        # Fitness Age
        try:
            fitness = client.get_fitnessage_data(day_str)
            raw_data['fitness_age'] = fitness
            
            if fitness:
                # fitness_age può essere un int diretto o in un dict
                if isinstance(fitness, dict):
                    # Cerca il campo corretto
                    metric.fitness_age = fitness.get('fitnessAge') or fitness.get('chronologicalAge')
                elif isinstance(fitness, (int, float)):
                    metric.fitness_age = int(fitness)
        except Exception as e:
            raw_data['fitness_age_error'] = str(e)
        
        # VO2 Max
        try:
            max_metrics = client.get_max_metrics(day_str)
            raw_data['max_metrics'] = max_metrics
            
            if max_metrics and isinstance(max_metrics, list) and len(max_metrics) > 0:
                # Cerca VO2 Max nei risultati
                for m in max_metrics:
                    if m.get('generic', {}).get('vo2MaxPreciseValue'):
                        metric.vo2_max = m['generic']['vo2MaxPreciseValue']
                        break
                    elif m.get('generic', {}).get('vo2MaxValue'):
                        metric.vo2_max = m['generic']['vo2MaxValue']
                        break
                    elif m.get('cycling', {}).get('vo2MaxPreciseValue'):
                        metric.vo2_max = m['cycling']['vo2MaxPreciseValue']
                        break
                    elif m.get('running', {}).get('vo2MaxPreciseValue'):
                        metric.vo2_max = m['running']['vo2MaxPreciseValue']
                        break
        except Exception as e:
            raw_data['max_metrics_error'] = str(e)
        
        # Race Predictions
        try:
            race = client.get_race_predictions()
            raw_data['race_predictions'] = race
            
            if race:
                metric.race_time_5k = race.get('time5K')
                metric.race_time_10k = race.get('time10K')
                metric.race_time_half = race.get('timeHalfMarathon')
                metric.race_time_marathon = race.get('timeMarathon')
        except Exception as e:
            raw_data['race_predictions_error'] = str(e)
        
        # Calcola metriche derivate
        self._calculate_scores(metric, user)
        
        # Salva raw JSON
        metric.raw_json = json.dumps(raw_data, default=str)
        
        if not existing:
            db.session.add(metric)
        
        return True
    
    def _sync_activity(self, user: User, activity_data: dict) -> bool:
        """Sincronizza una singola attività"""
        
        garmin_id = activity_data.get('activityId')
        if not garmin_id:
            return False
        
        # Controlla se esiste già
        existing = Activity.query.filter_by(garmin_activity_id=garmin_id).first()
        if existing:
            return False  # Già sincronizzata
        
        activity = Activity(
            user_id=user.id,
            garmin_activity_id=garmin_id,
            activity_name=activity_data.get('activityName'),
            activity_type=activity_data.get('activityType', {}).get('typeKey'),
            duration_seconds=activity_data.get('duration'),
            distance_meters=activity_data.get('distance'),
            calories=activity_data.get('calories'),
            avg_hr=activity_data.get('averageHR'),
            max_hr=activity_data.get('maxHR'),
            aerobic_effect=activity_data.get('aerobicTrainingEffect'),
            anaerobic_effect=activity_data.get('anaerobicTrainingEffect'),
            hr_zone_1=activity_data.get('hrTimeInZone_1'),
            hr_zone_2=activity_data.get('hrTimeInZone_2'),
            hr_zone_3=activity_data.get('hrTimeInZone_3'),
            hr_zone_4=activity_data.get('hrTimeInZone_4'),
            hr_zone_5=activity_data.get('hrTimeInZone_5'),
            moderate_intensity_minutes=activity_data.get('moderateIntensityMinutes'),
            vigorous_intensity_minutes=activity_data.get('vigorousIntensityMinutes'),
        )
        
        # Parse start time
        start_str = activity_data.get('startTimeLocal')
        if start_str:
            try:
                activity.start_time = datetime.strptime(start_str, '%Y-%m-%d %H:%M:%S')
            except:
                pass
        
        end_str = activity_data.get('endTimeGMT')
        if end_str:
            try:
                activity.end_time = datetime.strptime(end_str, '%Y-%m-%d %H:%M:%S')
            except:
                pass
        
        # Calcola strain dell'attività
        activity.strain_score = self._calculate_activity_strain(activity_data)
        
        db.session.add(activity)
        return True
    
    def _calculate_scores(self, metric: DailyMetric, user: User):
        """Calcola Recovery, Strain, Sleep Performance e Biological Age"""
        
        # --- RECOVERY SCORE (0-100) ---
        # Basato su: Body Battery, RHR vs baseline, Sleep quality
        recovery_components = []
        
        # Body Battery (peso: 40%)
        if metric.body_battery_high:
            bb_score = min(100, metric.body_battery_high)
            recovery_components.append(('body_battery', bb_score, 0.4))
        
        # RHR vs baseline (peso: 30%)
        # TODO: calcolare baseline da storico
        # Per ora usiamo un valore fisso di riferimento
        if metric.resting_hr:
            # RHR più basso = meglio. Assumiamo baseline 60
            rhr_baseline = 60
            rhr_diff = metric.resting_hr - rhr_baseline
            rhr_score = max(0, min(100, 100 - (rhr_diff * 3)))
            recovery_components.append(('rhr', rhr_score, 0.3))
        
        # Sleep quality (peso: 30%)
        if metric.sleep_seconds:
            # Target: 7-8 ore
            sleep_hours = metric.sleep_seconds / 3600
            if sleep_hours >= 7:
                sleep_duration_score = 100
            elif sleep_hours >= 6:
                sleep_duration_score = 80
            elif sleep_hours >= 5:
                sleep_duration_score = 60
            else:
                sleep_duration_score = max(0, sleep_hours / 5 * 60)
            
            # Deep + REM dovrebbero essere ~40% del sonno
            if metric.deep_sleep_seconds and metric.rem_sleep_seconds:
                quality_ratio = (metric.deep_sleep_seconds + metric.rem_sleep_seconds) / metric.sleep_seconds
                quality_score = min(100, quality_ratio / 0.4 * 100)
                sleep_score = (sleep_duration_score + quality_score) / 2
            else:
                sleep_score = sleep_duration_score
            
            recovery_components.append(('sleep', sleep_score, 0.3))
        
        # Calcola recovery pesato
        if recovery_components:
            total_weight = sum(c[2] for c in recovery_components)
            weighted_sum = sum(c[1] * c[2] for c in recovery_components)
            metric.recovery_score = int(weighted_sum / total_weight)
        
        # --- STRAIN SCORE (0-21 stile WHOOP) ---
        strain = 0
        
        # Base da intensity minutes
        moderate = metric.moderate_intensity_minutes or 0
        vigorous = metric.vigorous_intensity_minutes or 0
        strain += moderate * 0.05 + vigorous * 0.15
        
        # Da calorie attive
        if metric.active_calories:
            strain += metric.active_calories / 100
        
        # Da stress (se alto e prolungato)
        if metric.stress_avg and metric.stress_avg > 50:
            strain += (metric.stress_avg - 50) / 50
        
        # Cap a 21
        metric.strain_score = min(21.0, round(strain, 1))
        
        # --- SLEEP PERFORMANCE (0-100) ---
        if metric.sleep_seconds:
            sleep_hours = metric.sleep_seconds / 3600
            
            # Durata (target 8h)
            duration_pct = min(100, (sleep_hours / 8) * 100)
            
            # Qualità fasi
            quality_pct = 50  # default
            if metric.deep_sleep_seconds and metric.rem_sleep_seconds and metric.sleep_seconds > 0:
                deep_pct = metric.deep_sleep_seconds / metric.sleep_seconds
                rem_pct = metric.rem_sleep_seconds / metric.sleep_seconds
                # Deep ideale: 15-20%, REM ideale: 20-25%
                deep_score = min(100, deep_pct / 0.175 * 100)
                rem_score = min(100, rem_pct / 0.225 * 100)
                quality_pct = (deep_score + rem_score) / 2
            
            metric.sleep_performance = int((duration_pct * 0.6) + (quality_pct * 0.4))
        
        # --- BIOLOGICAL AGE ---
        self._calculate_biological_age(metric, user)
    
    def _calculate_biological_age(self, metric: DailyMetric, user: User):
        """
        Calcola l'età biologica con pesi normalizzati.
        Se mancano metriche, i pesi vengono ridistribuiti così
        chi ha meno dati non viene penalizzato.
        
        Range finale: circa -8 a +8 anni rispetto all'età reale
        """
        real_age = user.get_real_age()
        
        # Ogni metrica può dare un impatto da -1 a +1 (normalizzato)
        # Poi lo scaliamo al range finale desiderato
        impacts = {}
        
        # 1. RHR Impact (baseline 60, range 40-80)
        if metric.resting_hr and metric.resting_hr > 0:
            rhr = metric.resting_hr
            # -1 a 40bpm, 0 a 60bpm, +1 a 80bpm
            raw = (rhr - 60) / 20
            impacts['rhr'] = max(-1, min(1, raw))
        
        # 2. VO2 Max Impact (baseline 42, range 30-55)
        if metric.vo2_max and metric.vo2_max > 0:
            vo2 = metric.vo2_max
            # +1 a 30, 0 a 42, -1 a 55
            raw = (42 - vo2) / 13
            impacts['vo2'] = max(-1, min(1, raw))
        
        # 3. Sleep Impact (ottimale 7-8.5h)
        if metric.sleep_seconds and metric.sleep_seconds > 0:
            sleep_hours = metric.sleep_seconds / 3600
            if sleep_hours >= 7 and sleep_hours <= 8.5:
                impacts['sleep'] = -0.3  # Bonus
            elif sleep_hours < 7:
                # Meno sonno = peggio
                impacts['sleep'] = min(1, (7 - sleep_hours) / 3)
            else:
                # Troppo sonno = leggermente peggio
                impacts['sleep'] = min(0.5, (sleep_hours - 8.5) / 3)
        
        # 4. Steps Impact (target 10k)
        if metric.steps and metric.steps > 0:
            steps = metric.steps
            if steps >= 12000:
                impacts['steps'] = -0.8
            elif steps >= 10000:
                impacts['steps'] = -0.5
            elif steps >= 8000:
                impacts['steps'] = -0.2
            elif steps >= 6000:
                impacts['steps'] = 0.0
            elif steps >= 4000:
                impacts['steps'] = 0.3
            elif steps >= 2000:
                impacts['steps'] = 0.6
            else:
                impacts['steps'] = 1.0
        
        # 5. Intensity Minutes Impact
        moderate = metric.moderate_intensity_minutes or 0
        vigorous = metric.vigorous_intensity_minutes or 0
        intensity_score = moderate + (vigorous * 2)
        
        if intensity_score > 0 or (moderate == 0 and vigorous == 0 and metric.steps and metric.steps > 0):
            # Se abbiamo dati di passi ma no intensity, consideriamo 0
            if intensity_score >= 60:
                impacts['hrz'] = -0.8
            elif intensity_score >= 45:
                impacts['hrz'] = -0.5
            elif intensity_score >= 30:
                impacts['hrz'] = -0.2
            elif intensity_score >= 15:
                impacts['hrz'] = 0.0
            elif intensity_score >= 5:
                impacts['hrz'] = 0.3
            else:
                impacts['hrz'] = 0.5
        
        # Minimo 2 metriche per calcolare
        if len(impacts) < 2:
            metric.biological_age = None
            metric.bio_age_rhr_impact = None
            metric.bio_age_vo2_impact = None
            metric.bio_age_sleep_impact = None
            metric.bio_age_steps_impact = None
            metric.bio_age_hrz_impact = None
            metric.bio_age_stress_impact = None
            return
        
        # Media degli impatti (normalizzati tra -1 e +1)
        avg_impact = sum(impacts.values()) / len(impacts)
        
        # Scala al range finale: -8 a +8 anni
        MAX_YEARS = 8
        final_impact = avg_impact * MAX_YEARS
        
        # Salva gli impatti individuali scalati (per display)
        # Mostriamo l'impatto in anni per ogni metrica
        scale = MAX_YEARS / len(impacts)  # Distribuisci equamente
        metric.bio_age_rhr_impact = round(impacts.get('rhr', 0) * scale, 1) if 'rhr' in impacts else None
        metric.bio_age_vo2_impact = round(impacts.get('vo2', 0) * scale, 1) if 'vo2' in impacts else None
        metric.bio_age_sleep_impact = round(impacts.get('sleep', 0) * scale, 1) if 'sleep' in impacts else None
        metric.bio_age_steps_impact = round(impacts.get('steps', 0) * scale, 1) if 'steps' in impacts else None
        metric.bio_age_hrz_impact = round(impacts.get('hrz', 0) * scale, 1) if 'hrz' in impacts else None
        metric.bio_age_stress_impact = None
        
        # Età biologica finale
        metric.biological_age = round(real_age + final_impact, 1)
    
    def _calculate_activity_strain(self, activity: dict) -> float:
        """Calcola lo strain di una singola attività"""
        strain = 0
        
        # Training effect aerobico (0-5) → contributo principale
        aerobic = activity.get('aerobicTrainingEffect', 0) or 0
        strain += aerobic * 2
        
        # Training effect anaerobico
        anaerobic = activity.get('anaerobicTrainingEffect', 0) or 0
        strain += anaerobic * 1.5
        
        # HR zones (tempo in zone alte)
        zone4 = activity.get('hrTimeInZone_4', 0) or 0
        zone5 = activity.get('hrTimeInZone_5', 0) or 0
        strain += (zone4 / 60) * 0.3 + (zone5 / 60) * 0.5
        
        # Durata (contributo minore)
        duration = activity.get('duration', 0) or 0
        strain += (duration / 3600) * 0.5
        
        return min(21.0, round(strain, 1))


def sync_all_users(app, encryption_key: str):
    """Funzione per il cron job che sincronizza tutti gli utenti"""
    with app.app_context():
        service = GarminSyncService(encryption_key)
        users = User.query.filter_by(sync_enabled=True).all()
        
        results = []
        for user in users:
            try:
                result = service.sync_user(user)
                results.append({
                    'user_id': user.id,
                    'email': user.email,
                    **result
                })
            except Exception as e:
                results.append({
                    'user_id': user.id,
                    'email': user.email,
                    'success': False,
                    'error': str(e)
                })
        
        return results