"""License gating via Lemon Squeezy's public License API (no secret key in the client).

Honest note: this is a convenience/honor-system gate. It runs on the user's
machine in readable Python, so it can be bypassed by anyone who edits it.
"""
from __future__ import annotations
import json, os, socket, time, urllib.parse, urllib.request, urllib.error

API = "https://api.lemonsqueezy.com/v1/licenses"
PRODUCT_ID = None      # set to the numeric Lemon Squeezy product id before release
GRACE_DAYS = 7
PAID_CHECKS = ("source", "citation", "consistency")
FREE_CHECKS = ("overclaim",)


def _home():
    d = os.environ.get("CLAIMAUDIT_HOME") or os.path.join(os.path.expanduser("~"), ".claimaudit")
    os.makedirs(d, exist_ok=True)
    return d


def _file():
    return os.path.join(_home(), "license.json")


def default_post(endpoint, fields, timeout=15):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(f"{API}/{endpoint}", data=data,
                                 headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except ValueError:
            return e.code, {}
    except Exception:
        return 0, {}


def _ok(resp):
    lk = resp.get("license_key") or {}
    if lk.get("status") in ("expired", "disabled"):
        return False, f"license is {lk.get('status')}"
    if not (resp.get("valid") or resp.get("activated")):
        return False, resp.get("error") or "license not valid"
    if PRODUCT_ID is not None:
        pid = (resp.get("meta") or {}).get("product_id")
        if pid != PRODUCT_ID:
            return False, "license key is for a different product"
    return True, ""


def _load():
    try:
        return json.load(open(_file()))
    except (OSError, ValueError):
        return {}


def _save(d):
    with open(_file(), "w") as fh:
        json.dump(d, fh)


def activate(key, post=default_post, now=time.time):
    st, resp = post("activate", {"license_key": key, "instance_name": socket.gethostname() or "claimaudit"})
    if st == 0:
        return False, "could not reach the license server"
    ok, why = _ok(resp)
    if not ok:
        return False, why
    _save({"key": key, "instance_id": (resp.get("instance") or {}).get("id"), "validated_at": now()})
    return True, "activated"


def tier(post=default_post, now=time.time):
    """-> ('paid'|'free', message)."""
    d = _load()
    key = os.environ.get("CLAIMAUDIT_LICENSE_KEY") or d.get("key")
    if not key:
        return "free", "no license key (run: claimaudit activate <KEY>)"
    fresh = now() - d.get("validated_at", 0) < 86400 and d.get("key") == key
    if fresh:
        return "paid", "licensed"
    fields = {"license_key": key}
    if d.get("instance_id") and d.get("key") == key:
        fields["instance_id"] = d["instance_id"]
    st, resp = post("validate", fields)
    if st == 0:
        if d.get("key") == key and now() - d.get("validated_at", 0) < GRACE_DAYS * 86400:
            return "paid", "licensed (offline grace period)"
        return "free", "could not reach the license server and the grace period has ended"
    ok, why = _ok(resp)
    if not ok:
        return "free", why
    d.update({"key": key, "validated_at": now()})
    _save(d)
    return "paid", "licensed"
