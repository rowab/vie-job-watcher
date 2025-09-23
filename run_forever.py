import os, subprocess, time, sys, datetime, traceback

INTERVAL_SEC = 60 
PYTHON = sys.executable

while True:
    print(f"[{datetime.datetime.now()}] Run watcher…", flush=True)
    try:
        subprocess.run([PYTHON, "watcher.py"], check=False)
    except Exception:
        traceback.print_exc()
    time.sleep(INTERVAL_SEC)
