import os
from dotenv import load_dotenv

load_dotenv()

WTF_CSRF_ENABLED = True
SECRET_KEY = os.getenv("SECRET_KEY", "a-very-secret-secret")

SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")

SQLALCHEMY_TRACK_MODIFICATIONS = False
SQLALCHEMY_ENGINE_OPTIONS = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}