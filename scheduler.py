"""
Scheduler per sync automatico
Può essere eseguito come servizio separato o integrato nell'app
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app import create_app
from app.garmin_sync import sync_all_users
from config import Config
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_sync():
    """Esegue sync di tutti gli utenti"""
    logger.info("Starting scheduled sync...")
    app = create_app()
    results = sync_all_users(app, Config.ENCRYPTION_KEY)
    logger.info(f"Sync completed: {results}")
    return results


def start_scheduler():
    """Avvia lo scheduler"""
    scheduler = BackgroundScheduler()
    
    # Sync ogni giorno alle 6:00 e alle 12:00
    scheduler.add_job(
        run_sync,
        CronTrigger(hour='6,12', minute='0'),
        id='garmin_sync',
        name='Garmin Data Sync'
    )
    
    scheduler.start()
    logger.info("Scheduler started")
    return scheduler


if __name__ == '__main__':
    # Per test manuale
    run_sync()
