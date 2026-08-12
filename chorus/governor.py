"""Shared authenticated request headers for the remote vision governor."""
import os


def headers(user_agent):
    values = {"Content-Type": "application/json", "User-Agent": user_agent}
    token = os.environ.get("CHORUS_GOVERNOR_TOKEN", "").strip()
    if token:
        values["Authorization"] = "Bearer " + token
    return values
