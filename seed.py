#!/usr/bin/env python3
"""One-time interactive Garmin login that handles the MFA code and saves the
session token into the relay Worker. Run this locally from your home IP.

After it succeeds, fetch.py and the hourly GitHub Action resume the saved
session and refresh it silently -- no MFA needed again (token lasts ~1 year).
Reuses the env vars you already exported: WORKER_URL, PUSH_KEY,
GARMIN_EMAIL, GARMIN_PASSWORD.
"""
import os

import requests
from garminconnect import Garmin

WORKER = os.environ["WORKER_URL"].rstrip("/")
PUSH_KEY = os.environ["PUSH_KEY"]
EMAIL = os.environ["GARMIN_EMAIL"]
PASSWORD = os.environ["GARMIN_PASSWORD"]


def ask_mfa():
    return input("\n>>> Enter the Garmin verification code they just sent you: ").strip()


def main():
    g = Garmin(EMAIL, PASSWORD, prompt_mfa=ask_mfa)
    g.login()
    token = g.client.dumps()
    r = requests.put(f"{WORKER}/session", params={"key": PUSH_KEY},
                     data=token, timeout=30)
    print("session saved to worker:", r.status_code, r.text[:120])
    print("login OK as:", getattr(g, "display_name", "?"))


if __name__ == "__main__":
    main()
