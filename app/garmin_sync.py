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
    
    def sync_user(self, user: User, days_back: int = 7) -> dict:
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
            garmin_password = user.get_garmin_password(self.encryption_key)
            if not user.garmin_email or not garmin_password:
                raise ValueError("Credenziali Garmin non configurate")
            
            client = Garmin(user.garmin_email, garmin_password)
            client.login()
            
            for i in range(days_back):
                day = date.today() - timedelta(days=i)
                try:
                    synced = self._sync_daily_metrics(client, user, day)
                    if synced:
                        result['metrics_synced'] += 1
                except Exception as e:
                    result['errors'].append(f"Metrics {day}: {str(e)}")
            
            try:
                activities = client.get_activities(0, 20)
                for act in activities:
                    try:
                        synced = self._sync_activity(user, act)
                        if synced:
                            result['activities_synced'] += 1
                    except Exception as e:
                        result['errors'].append(f"Activity {act.get('activityId')}: {str(e)}")
            except Exception as e:
                result['errors'].append(f"Activities fetch: {str(e)}")
            
            user.last_sync = datetime.utcnow()
            
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
        day_str = day.isoformat()
        
        existing = DailyMetric.query.filter_by(user_id=user.id, date=day).first()
        if existing:
            metric = existing
        else:
            metric = DailyMetric(user_id=user.id, date=day)
        
        raw_data = {}
        
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
            
            metric.stress_avg = summary.get('averageStressLevel')
            metric.stress_max = summary.get('maxStressLevel')
            metric.rest_stress_duration = summary.get('restStressDuration')
            metric.low_stress_duration = summary.get('lowStressDuration')
            metric.medium_stress_duration = summary.get('mediumStressDuration')
            metric.high_stress_duration = summary.get('highStressDuration')
            
            metric.body_battery_high = summary.get('bodyBatteryHighestValue')
            metric.body_battery_low = summary.get('bodyBatteryLowestValue')
            metric.body_battery_charged = summary.get('bodyBatteryChargedValue')
            metric.body_battery_drained = summary.get('bodyBatteryDrainedValue')
            
            metric.avg_respiration = summary.get('avgWakingRespirationValue')
            metric.min_respiration = summary.get('lowestRespirationValue')
            metric.max_respiration = summary.get('highestRespirationValue')
            
        except Exception as e:
            raw_data['summary_error'] = str(e)
        
        try:
            sleep = client.get_sleep_data(day_str)
            raw_data['sleep'] = sleep
            
            daily_sleep = sleep.get('dailySleepDTO', {})
            metric.sleep_seconds = daily_sleep.get('sleepTimeSeconds')
            metric.deep_sleep_seconds = daily_sleep.get('deepSleepSeconds')
            metric.light_sleep_seconds = daily_sleep.get('lightSleepSeconds')
            metric.rem_sleep_seconds = daily_sleep.get('remSleepSeconds')
            metric.awake_seconds = daily_sleep.get('awakeSleepSeconds')
            
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
                    
            sleep_scores = daily_sleep.get('sleepScores', {})
            if sleep_scores:
                metric.sleep_score = sleep_scores.get('overall', {}).get('value')
                
        except Exception as e:
            raw_data['sleep_error'] = str(e)
        
        try:
            hrv = client.get_hrv_data(day_str)
            raw_data['hrv'] = hrv
            
            if hrv:
                hrv_summary = hrv.get('hrvSummary', hrv)
                metric.hrv_weekly_avg = hrv_summary.get('weeklyAvg')
                metric.hrv_last_night = hrv_summary.get('lastNightAvg')
        except Exception as e:
            raw_data['hrv_error'] = str(e)
        
        try:
            spo2 = client.get_spo2_data(day_str)
            raw_data['spo2'] = spo2
            
            if spo2:
                metric.avg_spo2 = spo2.get('averageSpO2')
                metric.min_spo2 = spo2.get('lowestSpO2')
        except Exception as e:
            raw_data['spo2_error'] = str(e)
        
        self._calculate_scores(metric, user)
        
        metric.raw_json = json.dumps(raw_data, default=str)
        
        if not existing:
            db.session.add(metric)
        
        return True
    
    def _sync_activity(self, user: User, activity_data: dict) -> bool:
        garmin_id = activity_data.get('activityId')
        if not garmin_id:
            return False
        
        existing = Activity.query.filter_by(garmin_activity_id=garmin_id).first()
        if existing:
            return False
        
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
        
        activity.strain_score = self._calculate_activity_strain(activity_data)
        
        db.session.add(activity)
        return True
    
    def _calculate_scores(self, metric: DailyMetric, user: User):
        # --- RECOVERY SCORE (0-100) ---
        recovery_components = []
        
        if metric.body_battery_high:
            bb_score = min(100, metric.body_battery_high)
            recovery_components.append(('body_battery', bb_score, 0.4))
        
        if metric.resting_hr:
            rhr_baseline = 60
            rhr_diff = metric.resting_hr - rhr_baseline
            rhr_score = max(0, min(100, 100 - (rhr_diff * 3)))
            recovery_components.append(('rhr', rhr_score, 0.3))
        
        if metric.sleep_seconds:
            sleep_hours = metric.sleep_seconds / 3600
            if sleep_hours >= 7:
                sleep_duration_score = 100
            elif sleep_hours >= 6:
                sleep_duration_score = 80
            elif sleep_hours >= 5:
                sleep_duration_score = 60
            else:
                sleep_duration_score = max(0, sleep_hours / 5 * 60)
            
            if metric.deep_sleep_seconds and metric.rem_sleep_seconds:
                quality_ratio = (metric.deep_sleep_seconds + metric.rem_sleep_seconds) / metric.sleep_seconds
                quality_score = min(100, quality_ratio / 0.4 * 100)
                sleep_score = (sleep_duration_score + quality_score) / 2
            else:
                sleep_score = sleep_duration_score
            
            recovery_components.append(('sleep', sleep_score, 0.3))
        
        if recovery_components:
            total_weight = sum(c[2] for c in recovery_components)
            weighted_sum = sum(c[1] * c[2] for c in recovery_components)
            metric.recovery_score = int(weighted_sum / total_weight)
        
        # --- STRAIN SCORE (0-21) ---
        strain = 0
        
        moderate = metric.moderate_intensity_minutes or 0
        vigorous = metric.vigorous_intensity_minutes or 0
        strain += moderate * 0.05 + vigorous * 0.15
        
        if metric.active_calories:
            strain += metric.active_calories / 100
        
        if metric.stress_avg and metric.stress_avg > 50:
            strain += (metric.stress_avg - 50) / 50
        
        metric.strain_score = min(21.0, round(strain, 1))
        
        # --- SLEEP PERFORMANCE (0-100) ---
        if metric.sleep_seconds:
            sleep_hours = metric.sleep_seconds / 3600
            duration_pct = min(100, (sleep_hours / 8) * 100)
            
            quality_pct = 50
            if metric.deep_sleep_seconds and metric.rem_sleep_seconds and metric.sleep_seconds > 0:
                deep_pct = metric.deep_sleep_seconds / metric.sleep_seconds
                rem_pct = metric.rem_sleep_seconds / metric.sleep_seconds
                deep_score = min(100, deep_pct / 0.175 * 100)
                rem_score = min(100, rem_pct / 0.225 * 100)
                quality_pct = (deep_score + rem_score) / 2
            
            metric.sleep_performance = int((duration_pct * 0.6) + (quality_pct * 0.4))
        
        # --- BIOLOGICAL AGE ---
        base_age = user.get_real_age()
        bio_adjustment = 0
        
        # RHR impact: ogni bpm sotto 55 = -0.3 anni, sopra 65 = +0.3 anni
        if metric.resting_hr:
            if metric.resting_hr < 55:
                bio_adjustment -= (55 - metric.resting_hr) * 0.3
            elif metric.resting_hr > 65:
                bio_adjustment += (metric.resting_hr - 65) * 0.3
        
        # Sleep impact
        if metric.sleep_seconds:
            sleep_h = metric.sleep_seconds / 3600
            if sleep_h < 5:
                bio_adjustment += 3
            elif sleep_h < 6:
                bio_adjustment += 1.5
            elif 7 <= sleep_h <= 8.5:
                bio_adjustment -= 1
            elif sleep_h > 9:
                bio_adjustment += 0.5
        
        # Steps impact
        if metric.steps:
            if metric.steps < 3000:
                bio_adjustment += 2
            elif metric.steps < 5000:
                bio_adjustment += 1
            elif metric.steps >= 8000:
                bio_adjustment -= 0.5
            elif metric.steps >= 10000:
                bio_adjustment -= 1
        
        # Stress impact
        if metric.stress_avg:
            if metric.stress_avg > 60:
                bio_adjustment += 1.5
            elif metric.stress_avg > 40:
                bio_adjustment += 0.5
            elif metric.stress_avg < 25:
                bio_adjustment -= 0.5
        
        # Body Battery impact
        if metric.body_battery_low and metric.body_battery_low < 15:
            bio_adjustment += 1
        if metric.body_battery_high and metric.body_battery_high > 90:
            bio_adjustment -= 0.5
        
        # Activity impact (intensity minutes)
        total_intensity = (metric.moderate_intensity_minutes or 0) + (metric.vigorous_intensity_minutes or 0) * 2
        if total_intensity >= 30:
            bio_adjustment -= 1
        elif total_intensity >= 15:
            bio_adjustment -= 0.5
        elif total_intensity == 0:
            bio_adjustment += 0.5
        
        metric.biological_age = round(base_age + bio_adjustment, 1)
    
    def _calculate_activity_strain(self, activity: dict) -> float:
        strain = 0
        
        aerobic = activity.get('aerobicTrainingEffect', 0) or 0
        strain += aerobic * 2
        
        anaerobic = activity.get('anaerobicTrainingEffect', 0) or 0
        strain += anaerobic * 1.5
        
        zone4 = activity.get('hrTimeInZone_4', 0) or 0
        zone5 = activity.get('hrTimeInZone_5', 0) or 0
        strain += (zone4 / 60) * 0.3 + (zone5 / 60) * 0.5
        
        duration = activity.get('duration', 0) or 0
        strain += (duration / 3600) * 0.5
        
        return min(21.0, round(strain, 1))


def sync_all_users(app, encryption_key: str):
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