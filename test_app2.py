import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.join(BASE_DIR, "server")

sys.path.append(SERVER_DIR)

import app

with app.app.app_context():
    try:
        print(app.get_ps_agent())
    except Exception as e:
        print("ERROR:", e)