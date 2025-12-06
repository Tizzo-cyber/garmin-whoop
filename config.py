import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-change-in-prod')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///garmin_whoop.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Chiave per criptare le password Garmin
    ENCRYPTION_KEY = os.environ.get('ENCRYPTION_KEY', 'dev-encryption-key-32bytes!')
