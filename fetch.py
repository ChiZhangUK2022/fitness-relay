#!/usr/bin/env python3
"""Pull real numbers from Garmin Connect and push them to the relay Worker.

Runs hourly on GitHub Actions. Reads a cached session token from the Worker so
it almost never has to do a full email/password login (which is what Garmin's
bot-protection dislikes). Needs these env vars (set as GitHub secrets):
    GARMIN_EMAIL, GARMIN_PASSWORD, WORKER_URL, PUSH_KEY
"""
import json
import os
import time
from datetime import date, timedelta

import requests
from garminconnect import Garmin

WORKER = os.environ["WORKER_URL"].rstrip("/")
PUSH_KEY = os.environ["PUSH_KEY"]
WATCH_KEY = os.environ.get("WATCH_KEY")
EMAIL = os.environ.get("GARMIN_EMAIL")
PASSWORD = os.environ.get("GARMIN_PASSWORD")


def current_metrics():
    # Read what the relay already holds, so we can preserve last-known values.
    if not WATCH_KEY:
        return {}
    try:
        r = requests.get(f"{WORKER}/data", params={"key": WATCH_KEY}, timeout=15)
        if r.ok:
            return r.json()
    except Exception as e:
        print("read current failed:", e)
    return {}


def get_session():
    try:
        r = requests.get(f"{WORKER}/session", params={"key": PUSH_KEY}, timeout=30)
        if r.ok and r.text.strip():
            return r.text.strip()
    except Exception as e:
        print("session fetch failed:", e)
    return None


def put_session(token):
    try:
        requests.put(f"{WORKER}/session", params={"key": PUSH_KEY},
                     data=token, timeout=30)
    except Exception as e:
        print("session save failed:", e)


def put_metrics(obj):
    r = requests.put(f"{WORKER}/metrics", params={"key": PUSH_KEY},
                     data=json.dumps(obj), timeout=30)
    print("push metrics:", r.status_code, r.text[:200])


def login():
    # garminconnect.login() accepts a base64 token (>512 chars) and resumes it;
    # if the token is missing or stale it falls back to email/password itself.
    g = Garmin(EMAIL, PASSWORD)
    g.login(get_session())
    return g


def main():
    # When triggered right after an activity/wake event, give Garmin's cloud a
    # moment to sync + process before we read (set by the workflow for dispatch).
    delay = int(os.environ.get("REFRESH_DELAY", "0"))
    if delay > 0:
        print(f"event-triggered: waiting {delay}s for Garmin to process...")
        time.sleep(delay)

    g = login()
    # Persist the (possibly refreshed) session so next run skips the full login.
    try:
        put_session(g.client.dumps())
    except Exception as e:
        print("dump session failed:", e)

    today = date.today()
    iso = today.isoformat()
    out = {"ts": iso}

    # Sleep score (today's record = last night)
    try:
        sd = g.get_sleep_data(iso) or {}
        dto = sd.get("dailySleepDTO") or {}
        scores = dto.get("sleepScores") or {}
        overall = (scores.get("overall") or {}).get("value")
        out["sleep"] = int(overall) if overall is not None else None
    except Exception as e:
        print("sleep err:", e)
        out["sleep"] = None

    # Training readiness (today) -- LATEST snapshot. Garmin logs readiness at
    # "reset" moments (wake-up, after a workout); take the most recent by
    # timestamp. (The value continuously drifting on the watch between resets
    # is computed on-device and never uploaded, so this is the freshest the
    # cloud offers.)
    try:
        tr = g.get_training_readiness(iso)
        entry = None
        if isinstance(tr, list) and tr:
            entry = max(tr, key=lambda e: e.get("timestamp") or "")
        elif isinstance(tr, dict):
            entry = tr
        score = entry.get("score") if entry else None
        out["ready"] = int(score) if score is not None else None
    except Exception as e:
        print("ready err:", e)
        out["ready"] = None

    # Weekly swim distance (Monday..today), summed across swim activity types.
    try:
        monday = today - timedelta(days=today.weekday())  # weekday(): Mon=0
        acts = g.get_activities_by_date(monday.isoformat(), iso) or []
        meters = 0.0
        for a in acts:
            tk = ((a.get("activityType") or {}).get("typeKey") or "").lower()
            d = a.get("distance")
            if "swim" in tk and isinstance(d, (int, float)):
                meters += d
        out["swim_mi"] = round(meters / 1609.344, 2)
        out["swim_km"] = round(meters / 1000.0, 2)
    except Exception as e:
        print("swim err:", e)
        out["swim_mi"] = None

    # Garmin computes sleep/readiness a while AFTER you wake, so an early run
    # returns None. Never wipe a good value with null -- keep the last-known one
    # until a later run gets the real number.
    cur = current_metrics()
    for k in ("sleep", "ready", "swim_mi", "swim_km"):
        if out.get(k) is None and cur.get(k) is not None:
            out[k] = cur[k]
            print(f"kept last-known {k}={cur[k]}")

    print("metrics:", out)
    put_metrics(out)


if __name__ == "__main__":
    main()
