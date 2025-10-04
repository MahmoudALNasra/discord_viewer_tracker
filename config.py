# config.py
import os
from dotenv import load_dotenv

# load .env when present (local dev)
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "voice_tracker.db")
