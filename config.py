import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    ENCRYPTION_KEY = os.environ.get('ENCRYPTION_KEY', 'dev-encryption-key-32chars!')
    
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///garmin_whoop.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Fix per Railway PostgreSQL: SQLAlchemy 2.1+ usa psycopg3 di default,
    # ma installiamo psycopg2-binary, quindi forziamo il driver psycopg2.
    _uri = SQLALCHEMY_DATABASE_URI or ''
    if _uri.startswith('postgres://'):
        _uri = _uri.replace('postgres://', 'postgresql://', 1)
    if _uri.startswith('postgresql://'):
        _uri = _uri.replace('postgresql://', 'postgresql+psycopg2://', 1)
    SQLALCHEMY_DATABASE_URI = _uri
