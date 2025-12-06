"""
Garmin Sync Service
"""

from datetime import date, datetime, timedelta
from garminconnect import Garmin
from app.models import db, User, DailyMetric, Activity, SyncLog
import json


class GarminSyncService:
    
    def __init__(self, encryption_key: str):
        self.encryption_key = encryption_key
    
    def sync_user(self, user: User, days_back: int = 7) -> dict:
        log = SyncLog(user_id=user.id, status='running')
        db.session.add(log)
        db.session.commit()
        
        result = {'success': False, 'metrics_synced': 0, 'activities_synced': 0, 'errors': []}
        
        try:
            garmin_password = user.get_garmin_password(self.encryption_key)
            if not user.garmin_email or not garmin_password:
                raise ValueError("Credenziali Garmin non configurate")
            
            client = Garmin(user.garmin_email, garmin_password)
            client.login()
            
            for i in range(days_back):
                day = date.today() - timedelta(days=i)
                try:
                    if self._sync_daily_metrics(client, user, day):
                        result['metrics_synced'] += 1
                except Exception as e:
                    result['errors'].append(f"{day}: {str(e)}")
            
            try:
                activities = client.get_activities(0, 50)
                for act in activities:
                    try:
                        if self._sync_activity(user, act):
                            result['activities_synced'] += 1
                    except:
                        pass
            except Exception as e:
                result['errors'].append(f"Activities: {str(e)}")
            
            user.last_sync = datetime.utcnow()
            log.status = 'success'
            log.metrics_synced = result['metrics_synced']
            log.activities_synced = result['activities_synced']
            result['success'] = True
            
        except Exception as e:
            log.status = 'error'
            log.error_message = str(e)
            result['errors'].append(str(e))
        
        finally:
            log.finished_at = datetime.utcnow()
            db.session.commit()
        
        return result
    
    def _sync_daily_metrics(self, client: Garmin, user: User, day: date) -> bool:
        day_str = day.isoformat()
        
        existing = DailyMetric.query.filter_by(user_id=user.id, date=day).first()
        metric = existing if existing else DailyMetric(user_id=user.id, date=day)
        
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
            metric.stress_avg = summary.get('averageStressLevel')
            metric.stress_max = summary.get('maxStressLevel')
            metric.body_battery_high = summary.get('bodyBatteryHighestValue')
            metric.body_battery_low = summary.get('bodyBatteryLowestValue')
            metric.body_battery_charged = summary.get('bodyBatteryChargedValue')
            metric.body_battery_drained = summary.get('bodyBatteryDrainedValue')
            metric.avg_respiration = summary.get('avgWakingRespirationValue')
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
                    metric.sleep_start = datetime.fromisoformat(daily_sleep['sleepStartTimestampLocal'].replace('.0', ''))
                except:
                    pass
            if daily_sleep.get('sleepEndTimestampLocal'):
                try:
                    metric.sleep_end = datetime.fromisoformat(daily_sleep['sleepEndTimestampLocal'].replace('.0', ''))
                except:
                    pass
        except Exception as e:
            raw_data['sleep_error'] = str(e)
        
        try:
            fitness = client.get_max_metrics(day_str)
            if fitness:
                for item in fitness:
                    if item.get('generic', {}).get('vo2MaxValue'):
                        metric.vo2_max = item['generic']['vo2MaxValue']
                        break
        except:
            pass
        
        try:
            hrv = client.get_hrv_data(day_str)
            if hrv:
                hrv_summary = hrv.get('hrvSummary', hrv)
                metric.hrv_weekly_avg = hrv_summary.get('weeklyAvg')
                metric.hrv_last_night = hrv_summary.get('lastNightAvg')
        except:
            pass
        
        try:
            spo2 = client.get_spo2_data(day_str)
            if spo2:
                metric.avg_spo2 = spo2.get('averageSpO2')
                metric.min_spo2 = spo2.get('lowestSpO2')
        except:
            pass
        
        self._calculate_scores(metric, user)
        metric.raw_json = json.dumps(raw_data, default=str)
        
        if not existing:
            db.session.add(metric)
        
        return True
    
    def _sync_activity(self, user: User, activity_data: dict) -> bool:
        garmin_id = activity_data.get('activityId')
        if not garmin_id:
            return False
        
        if Activity.query.filter_by(garmin_activity_id=garmin_id).first():
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
        )
        
        start_str = activity_data.get('startTimeLocal')
        if start_str:
            try:
                activity.start_time = datetime.strptime(start_str, '%Y-%m-%d %H:%M:%S')
            except:
                pass
        
        activity.strain_score = self._calculate_activity_strain(activity_data)
        db.session.add(activity)
        return True
    
    def _calculate_scores(self, metric: DailyMetric, user: User):
        # Recovery
        recovery_components = []
        if metric.body_battery_high:
            recovery_components.append(min(100, metric.body_battery_high) * 0.4)
        if metric.resting_hr:
            rhr_score = max(0, min(100, 100 - (metric.resting_hr - 55) * 3))
            recovery_components.append(rhr_score * 0.3)
        if metric.sleep_seconds:
            sleep_hours = metric.sleep_seconds / 3600
            sleep_score = min(100, (sleep_hours / 8) * 100)
            recovery_components.append(sleep_score * 0.3)
        if recovery_components:
            metric.recovery_score = int(sum(recovery_components))
        
        # Strain
        strain = 0
        moderate = metric.moderate_intensity_minutes or 0
        vigorous = metric.vigorous_intensity_minutes or 0
        strain += moderate * 0.05 + vigorous * 0.15
        if metric.active_calories:
            strain += metric.active_calories / 100
        metric.strain_score = min(21.0, round(strain, 1))
        
        # Sleep Performance
        if metric.sleep_seconds:
            sleep_hours = metric.sleep_seconds / 3600
            duration_score = min(100, (sleep_hours / 8) * 100)
            quality_score = 50
            if metric.deep_sleep_seconds and metric.rem_sleep_seconds and metric.sleep_seconds > 0:
                deep_pct = metric.deep_sleep_seconds / metric.sleep_seconds
                rem_pct = metric.rem_sleep_seconds / metric.sleep_seconds
                quality_score = min(100, ((deep_pct / 0.20) + (rem_pct / 0.25)) * 50)
            metric.sleep_performance = int(duration_score * 0.6 + quality_score * 0.4)
        
        # Biological Age
        base_age = user.get_real_age()
        
        rhr_impact = 0
        if metric.resting_hr:
            if metric.resting_hr <= 50: rhr_impact = -2.0
            elif metric.resting_hr <= 55: rhr_impact = -1.0
            elif metric.resting_hr <= 60: rhr_impact = -0.5
            elif metric.resting_hr <= 65: rhr_impact = 0
            elif metric.resting_hr <= 70: rhr_impact = 0.5
            elif metric.resting_hr <= 75: rhr_impact = 1.0
            else: rhr_impact = 2.0
        metric.bio_age_rhr_impact = rhr_impact
        
        vo2_impact = 0
        if metric.vo2_max:
            if metric.vo2_max >= 50: vo2_impact = -2.5
            elif metric.vo2_max >= 45: vo2_impact = -1.5
            elif metric.vo2_max >= 40: vo2_impact = -1.0
            elif metric.vo2_max >= 35: vo2_impact = -0.5
            elif metric.vo2_max >= 30: vo2_impact = 0
            else: vo2_impact = 1.0
        metric.bio_age_vo2_impact = vo2_impact
        
        sleep_impact = 0
        if metric.sleep_seconds:
            sleep_h = metric.sleep_seconds / 3600
            if 7 <= sleep_h <= 8.5: sleep_impact = -0.5
            elif 6.5 <= sleep_h < 7: sleep_impact = 0
            elif 6 <= sleep_h < 6.5: sleep_impact = 0.5
            elif sleep_h < 6: sleep_impact = 1.5
            elif sleep_h > 9: sleep_impact = 0.3
        metric.bio_age_sleep_impact = sleep_impact
        
        steps_impact = 0
        if metric.steps:
            if metric.steps >= 12000: steps_impact = -1.0
            elif metric.steps >= 10000: steps_impact = -0.5
            elif metric.steps >= 8000: steps_impact = -0.3
            elif metric.steps >= 5000: steps_impact = 0
            elif metric.steps >= 3000: steps_impact = 0.5
            else: steps_impact = 1.0
        metric.bio_age_steps_impact = steps_impact
        
        stress_impact = 0
        if metric.stress_avg:
            if metric.stress_avg <= 25: stress_impact = -0.5
            elif metric.stress_avg <= 35: stress_impact = 0
            elif metric.stress_avg <= 50: stress_impact = 0.3
            else: stress_impact = 1.0
        metric.bio_age_stress_impact = stress_impact
        
        hrz_impact = 0
        vigorous = metric.vigorous_intensity_minutes or 0
        if vigorous >= 30: hrz_impact = -1.0
        elif vigorous >= 15: hrz_impact = -0.5
        elif vigorous >= 5: hrz_impact = 0
        else: hrz_impact = 0.3
        metric.bio_age_hrz_impact = hrz_impact
        
        total_impact = rhr_impact + vo2_impact + sleep_impact + steps_impact + stress_impact + hrz_impact
        metric.biological_age = round(base_age + total_impact, 1)
    
    def _calculate_activity_strain(self, activity: dict) -> float:
        strain = 0
        aerobic = activity.get('aerobicTrainingEffect', 0) or 0
        strain += aerobic * 2
        anaerobic = activity.get('anaerobicTrainingEffect', 0) or 0
        strain += anaerobic * 1.5
        duration = activity.get('duration', 0) or 0
        strain += (duration / 3600) * 0.5
        return min(21.0, round(strain, 1))