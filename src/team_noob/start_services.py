import os
from team_noob.agent import run_service

host = os.getenv("HOST", "0.0.0.0")
port = int(os.getenv("PORT", "8000"))

run_service(host=host, port=port)
