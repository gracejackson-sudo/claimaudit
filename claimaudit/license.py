"""License gating: an honor system, checked locally, with no network call.

A license key is a token shown on docs/success.html, where the Stripe Payment
Link below redirects after payment; for now it is one key shared by every
buyer. Any non-empty key unlocks the paid checks. Nothing checks
it against a server, it is not a cryptographic license, and anyone who reads
this code can bypass it.
"""
from __future__ import annotations
import json, os, time

# Replace this with the Stripe Payment Link. One line. (Same string in README.md and docs/index.html.)
PURCHASE_URL = "https://buy.stripe.com/8x2dRb5ND1oP75V1DWgEg00"
PAID_CHECKS = ("source", "citation", "consistency", "registry", "benchmark", "seeds")
FREE_CHECKS = ("overclaim",)


def _home():
    d = os.environ.get("CLAIMAUDIT_HOME") or os.path.join(os.path.expanduser("~"), ".claimaudit")
    os.makedirs(d, exist_ok=True)
    return d


def _file():
    return os.path.join(_home(), "license.json")


def _clean(key):
    """A usable key, or '' -- whitespace alone must not unlock anything."""
    return key.strip() if isinstance(key, str) else ""


def _load():
    try:
        with open(_file()) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d):
    with open(_file(), "w") as fh:
        json.dump(d, fh)


def activate(key, now=time.time):
    """Store the key locally. Nothing is sent anywhere."""
    key = _clean(key)
    if not key:
        return False, "empty license key"
    _save({"key": key, "stored_at": now()})
    return True, "stored locally (no server check)"


def tier():
    """-> ('paid'|'free', message)."""
    key = _clean(os.environ.get("CLAIMAUDIT_LICENSE_KEY")) or _clean(_load().get("key"))
    if not key:
        return "free", "no license key (run: claimaudit activate <KEY>)"
    return "paid", "license key present (honor system, not checked against any server)"
