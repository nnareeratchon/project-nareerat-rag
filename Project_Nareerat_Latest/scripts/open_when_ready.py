"""Wait until the local server answers, then open it in the default browser."""
import os, time, urllib.request, webbrowser

port = os.getenv("PORT") or "8000"
try:
    from dotenv import load_dotenv
    load_dotenv()
    port = os.getenv("PORT") or port
except Exception:
    pass
url = f"http://localhost:{port}"
for _ in range(600):  # up to ~10 minutes (first index build can be slow)
    try:
        urllib.request.urlopen(url + "/api/status", timeout=2)
        print(f"\n>>> READY: {url}\n")
        webbrowser.open(url)
        break
    except Exception:
        time.sleep(1)
