from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient

import api.auth as auth
from api.main import app, repository
from database.repository import DatabaseRepository


SECRET = "test-only-secret-at-least-32-bytes-long"
URL = "https://project.supabase.co"
USER = "11111111-1111-4111-8111-111111111111"
client = TestClient(app)


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.delenv("SUPABASE_JWT_AUDIENCE", raising=False)
    repo = DatabaseRepository()
    repo.database_url = None
    repo._connection_factory = None
    app.dependency_overrides[repository] = lambda: repo
    yield
    app.dependency_overrides.pop(repository, None)


def token(**overrides):
    claims = {"sub": USER, "aud": "authenticated", "iss": f"{URL}/auth/v1",
              "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}
    claims.update(overrides)
    return jwt.encode(claims, SECRET, algorithm="HS256")


def headers(value):
    return {"Authorization": f"Bearer {value}"}


@pytest.mark.parametrize("path", ["profile", "searches", "learning-plans", "search", "learning-plan"])
def test_saved_routes_require_auth_for_reads_and_writes(path):
    for response in (client.get(f"/api/saved/{path}"),
                     client.post(f"/api/saved/{path}", json={})):
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("value", ["", "Basic secret", "Bearer", "Bearer bad", "Bearer a b"])
def test_invalid_authorization(value):
    assert client.get("/api/saved/profile", headers={"Authorization": value}).status_code == 401


@pytest.mark.parametrize("overrides", [
    {"exp": datetime.now(timezone.utc) - timedelta(seconds=30)},
    {"aud": "another-service"}, {"iss": "https://attacker.example/auth/v1"},
    {"sub": ""}, {"sub": 123},
])
def test_rejects_invalid_claims(overrides):
    assert client.get("/api/saved/profile", headers=headers(token(**overrides))).status_code == 401


@pytest.mark.parametrize("missing", ["exp", "aud", "iss", "sub"])
def test_requires_claims(missing):
    claims = jwt.decode(token(), SECRET, algorithms=["HS256"], audience="authenticated")
    claims.pop(missing)
    encoded = jwt.encode(claims, SECRET, algorithm="HS256")
    assert client.get("/api/saved/profile", headers=headers(encoded)).status_code == 401


def test_identity_is_verified_subject_not_client_user_id():
    response = client.post("/api/saved/profile?user_id=victim", headers=headers(token()),
                           json={"user_id": "victim", "target_role": "Engineer"})
    assert response.status_code == 200
    assert response.json()["user_id"] == USER
    assert len(client.get("/api/saved/profile", headers=headers(token())).json()) == 1
    other_user = token(sub="22222222-2222-4222-8222-222222222222")
    assert client.get("/api/saved/profile", headers=headers(other_user)).json() == []


@pytest.mark.parametrize("url", ["", "http://project.supabase.co", "https://project.supabase.co/path"])
def test_auth_unconfigured_is_503_even_with_token(monkeypatch, url):
    monkeypatch.setenv("SUPABASE_URL", url)
    assert client.get("/api/saved/profile", headers=headers(token())).status_code == 503


def test_legacy_secret_is_optional_but_hs256_requires_it(monkeypatch):
    monkeypatch.delenv("SUPABASE_JWT_SECRET")
    assert client.get("/api/saved/profile").status_code == 401
    assert client.get("/api/saved/profile", headers=headers(token())).status_code == 401


@pytest.mark.parametrize("algorithm", ["ES256", "RS256"])
def test_asymmetric_jwks_verification_uses_fixed_project_issuer(monkeypatch, algorithm):
    monkeypatch.delenv("SUPABASE_JWT_SECRET")
    private_key = (ec.generate_private_key(ec.SECP256R1()) if algorithm == "ES256"
                   else rsa.generate_private_key(public_exponent=65537, key_size=2048))
    claims = jwt.decode(token(), SECRET, algorithms=["HS256"], audience="authenticated")
    encoded = jwt.encode(claims, private_key, algorithm=algorithm,
                         headers={"kid": "rotating-key", "jku": "https://attacker.example/jwks"})
    requests = []

    def signing_key(value):
        requests.append(value)
        return SimpleNamespace(key=private_key.public_key())

    def client_for_issuer(issuer):
        assert issuer == f"{URL}/auth/v1"
        return SimpleNamespace(get_signing_key_from_jwt=signing_key)

    monkeypatch.setattr(auth, "jwks_client", client_for_issuer)
    assert client.get("/api/saved/profile", headers=headers(encoded)).status_code == 200
    assert requests == [encoded]
    # Signature verification is real; only the JWKS network lookup is mocked.
    claims["aud"] = "wrong-audience"
    invalid = jwt.encode(claims, private_key, algorithm=algorithm)
    assert client.get("/api/saved/profile", headers=headers(invalid)).status_code == 401
    claims["aud"] = "authenticated"
    claims["exp"] = 1
    expired = jwt.encode(claims, private_key, algorithm=algorithm)
    assert client.get("/api/saved/profile", headers=headers(expired)).status_code == 401


def test_rejects_bad_signature_and_unsupported_algorithm():
    claims = jwt.decode(token(), SECRET, algorithms=["HS256"], audience="authenticated")
    for encoded in [jwt.encode(claims, "different-secret-at-least-32-bytes!", algorithm="HS256"),
                    jwt.encode(claims, SECRET, algorithm="HS384"),
                    jwt.encode(claims, "", algorithm="none")]:
        assert client.get("/api/saved/profile", headers=headers(encoded)).status_code == 401


def test_jwks_lookup_failure_is_unauthorized(monkeypatch):
    def fail(_):
        raise jwt.PyJWKClientError("lookup failed")
    monkeypatch.setattr(auth, "jwks_client", fail)
    key = ec.generate_private_key(ec.SECP256R1())
    encoded = jwt.encode({"sub": USER}, key, algorithm="ES256")
    assert client.get("/api/saved/profile", headers=headers(encoded)).status_code == 401
