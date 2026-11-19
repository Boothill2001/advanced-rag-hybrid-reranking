"""Shared HS256 principal contract. Identity is supplied by a trusted caller, not the LLM."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import time


def secret():
    path = Path(os.getenv("COPILOT_SECRET_FILE", str(Path(__file__).resolve().parents[3] / "AI_AGENT_LANGGHAPH/var/secret.key")))
    value = path.read_bytes()
    if len(value) < 32:
        raise ValueError("Invalid shared signing secret")
    return value


def verify(token):
    head, body, signature = token.split(".")
    decode = lambda x: base64.urlsafe_b64decode(x + "=" * (-len(x) % 4))
    if json.loads(decode(head)) != {"alg": "HS256", "typ": "JWT"}:
        raise ValueError("Invalid token algorithm")
    expected = hmac.new(secret(), (head + "." + body).encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, decode(signature)):
        raise ValueError("Invalid token signature")
    claims = json.loads(decode(body))
    if claims.get("aud") != "research-copilot" or claims.get("exp", 0) <= time.time():
        raise ValueError("Expired token or wrong audience")
    if not all(isinstance(claims.get(k), str) and claims[k] for k in ("sub", "role", "workspace")):
        raise ValueError("Missing identity")
    return claims


def allowed(document, principal):
    return document.get("workspace", "public") == "public" or (
        document.get("workspace") == principal["workspace"] and principal["role"] in ("researcher", "reviewer"))
