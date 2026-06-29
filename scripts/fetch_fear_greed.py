import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import requests

CNN_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

LATEST = DATA_DIR / "fear_greed_latest.json"
SNAPSHOT_HISTORY = DATA_DIR / "fear_greed_history.json"
MARKET_HISTORY = DATA_DIR / "fear_greed_market_history.json"
WEEKLY_HISTORY = DATA_DIR / "fear_greed_weekly.json"
FORTNIGHTLY_HISTORY = DATA_DIR / "fear_greed_fortnightly.json"
TMP = DATA_DIR / "fear_greed_latest.tmp.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.cnn.com/markets/fear-and-greed",
    "Origin": "https://www.cnn.com",
}


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def dump_json(path: Path, payload):
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def fetch_cnn():
    r = requests.get(CNN_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def validate_payload(data):
    fg = data.get("fear_and_greed", {})
    score = fg.get("score")
    rating = fg.get("rating")

    if score is None or not isinstance(score, (int, float)):
        raise ValueError("Missing or invalid fear_and_greed.score")
    if not (0 <= score <= 100):
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


def update_snapshot_history(snapshot):
    """Append to history only if the score changed from the last recorded entry."""
    history = load_json(SNAPSHOT_HISTORY, [])
    row = {
        "timestamp_utc": snapshot["timestamp_utc"],
        "score": snapshot["score"],
        "rating": snapshot["rating"],
    }
    # Deduplicate by score+rating, not just timestamp (avoids duplicate entries
    # when the workflow runs multiple times within the same minute)
    if not history or history[-1].get("score") != row["score"] or history[-1].get("rating") != row["rating"]:
        history.append(row)
    dump_json(SNAPSHOT_HISTORY, history)


def normalize_raw_point(point):
    x = point.get("x")
    y = point.get("y")
    rating = point.get("rating")

    if x is None or y is None:
        return None

    try:
        score = float(y)
    except Exception:
        return None

    if not (0 <= score <= 100):
        return None

    ts = datetime.fromtimestamp(float(x) / 1000, tz=timezone.utc)

    return {
        "date_utc": ts.date().isoformat(),
        "timestamp_utc": ts.isoformat(),
        "score": round(score, 6),
        "rating": str(rating or ""),
    }


def extract_daily_series(raw):
    arr = ((raw or {}).get("fear_and_greed_historical") or {}).get("data") or []
    by_date = {}

    for point in arr:
        row = normalize_raw_point(point)
        if not row:
            continue
        by_date[row["date_utc"]] = row

    return [by_date[k] for k in sorted(by_date.keys())]


def iso_week_key(date_text):
    d = date.fromisoformat(date_text)
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def fortnight_key(date_text):
    d = date.fromisoformat(date_text)
    iso_year, iso_week, _ = d.isocalendar()
    fortnight_num = ((iso_week - 1) // 2) + 1
    return f"{iso_year}-F{fortnight_num:02d}"


def aggregate_last_point(series, key_name, bucket_key_func):
    """Keep the last data point per bucket (week / fortnight)."""
    buckets = {}

    for row in series:
        key = bucket_key_func(row["date_utc"])
        item = dict(row)
        item[key_name] = key
        buckets[key] = item

    return [buckets[k] for k in sorted(buckets.keys())]


def update_market_files(raw):
    daily = extract_daily_series(raw)

    if not daily:
        raise ValueError("No daily data points extracted from CNN payload")

    weekly = aggregate_last_point(daily, "iso_week", iso_week_key)
    fortnightly = aggregate_last_point(daily, "fortnight", fortnight_key)

    dump_json(MARKET_HISTORY, daily)
    dump_json(WEEKLY_HISTORY, weekly)
    dump_json(FORTNIGHTLY_HISTORY, fortnightly)

    return daily, weekly, fortnightly


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    try:
        raw = fetch_cnn()
        snapshot = validate_payload(raw)

        # Atomic write: write to tmp then rename to avoid partial-file corruption
        TMP.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        TMP.replace(LATEST)

        update_snapshot_history(snapshot)
        daily, weekly, fortnightly = update_market_files(raw)

        print(
            f"OK  score={snapshot['score']:.1f}  rating={snapshot['rating']}  "
            f"daily={len(daily)}  weekly={len(weekly)}  fortnightly={len(fortnightly)}"
        )

    except Exception as e:
        last_good = load_json(LATEST, None)
        if last_good:
            print(f"WARNING: Fetch failed — keeping last good snapshot. Error: {e}", file=sys.stderr)
            sys.exit(0)

        print(f"ERROR: Fetch failed and no last good snapshot exists. Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()