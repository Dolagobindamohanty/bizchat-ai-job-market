"""Verify user tokens against the configured Supabase project, never token URLs."""

from functools import lru_cache
import os
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Header, HTTPException, status
import jwt


@lru_cache(maxsize=8)
def jwks_client(issuer: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{issuer}/.well-known/jwks.json", timeout=5)


def current_user(authorization: Annotated[str | None, Header()] = None) -> str:
    project_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    parsed = urlsplit(project_url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Supabase authentication is not configured")
    issuer = f"{project_url}/auth/v1"
    parts = authorization.split() if authorization else []
    unauthorized = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                 detail="Invalid or missing bearer token",
                                 headers={"WWW-Authenticate": "Bearer"})
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise unauthorized
    token = parts[1]
    try:
        algorithm = jwt.get_unverified_header(token).get("alg")
        if algorithm == "HS256":
            key = os.getenv("SUPABASE_JWT_SECRET")
            if not key:
                raise unauthorized
        elif algorithm in {"ES256", "RS256"}:
            key = jwks_client(issuer).get_signing_key_from_jwt(token).key
        else:
            raise unauthorized
        claims = jwt.decode(
            token, key, algorithms=[algorithm], issuer=issuer,
            audience=os.getenv("SUPABASE_JWT_AUDIENCE", "authenticated"),
            options={"require": ["exp", "aud", "sub", "iss"]},
        )
        subject = claims["sub"]
        if not isinstance(subject, str) or not subject.strip():
            raise unauthorized
        return subject
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        raise unauthorized from exc
