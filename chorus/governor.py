"""Shared authenticated request headers for the remote vision governor."""
import os


def headers(user_agent):
    values = {"Content-Type": "application/json", "User-Agent": user_agent}
    token = os.environ.get("CHORUS_", "").strip()
    if token:
        values["Authorization"] = " " + token
    return values
