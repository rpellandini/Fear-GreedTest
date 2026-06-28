import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

CNN_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
LATEST = DATA_DIR / "fear_greed_latest.json"
HISTORY = DATA_DIR / "fear_greed_history.json"
TMP = DATA_DIR / "fear_greed_latest.tmp.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.cnn.com/markets/fear-and-greed",
    "Origin": "https://www.cnn.com",
}

def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default

def validate_payload(data):
    fg = data.get("fear_and_greed", {})
    score = fg.get("score")
    rating = fg.get("rating")
    if score is None:
        raise ValueError("Missing fear_and_greed.score")
    if not isinstance(score, (int, float)):
        raise ValueError("Score is not numeric")
    if score < 0 or score > 100:
        raise ValueError(f"Score out of range: {score}")
    if rating is None:
        raise ValueError("Missing fear_and_greed.rating")
    return {
        "score": float(score),
        "rating": str(rating),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "source": "CNN",
        "raw": data,
    }

def fetch_cnn():
    r = requests.get(CNN_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()

def update_history(snapshot):
    history = load_json(HISTORY, [])
    row = {
        "timestamp_utc": snapshot["timestamp_utc"],
        "score": snapshot["score"],
        "rating": snapshot["rating"],
    }
    if not history or history[-1].get("timestamp_utc") != row["timestamp_utc"]:
        history.append(row)
    HISTORY.write_text(json.dumps(history, indent=2), encoding="utf-8")

def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        raw = fetch_cnn()
        snapshot = validate_payload(raw)
        TMP.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        TMP.replace(LATEST)
        update_history(snapshot)
        print(f"Updated snapshot: {snapshot['score']} {snapshot['rating']}")
    except Exception as e:
        last_good = load_json(LATEST, None)
        if last_good:
            print(f"Fetch failed, keeping last good snapshot: {e}")
            sys.exit(0)
        print(f"Fetch failed and no last good snapshot exists: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()