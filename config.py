import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
INSTANCE_PATH = BASE_DIR / 'instance'

os.makedirs(INSTANCE_PATH, exist_ok=True)

SECRET_KEY = os.urandom(24)
SQLALCHEMY_DATABASE_URI = f'sqlite:///{INSTANCE_PATH}/database.db'
SQLALCHEMY_TRACK_MODIFICATIONS = False
