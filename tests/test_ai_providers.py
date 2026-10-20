import json
import sqlite3

import httpx
import pytest

from bizchat import ai_providers as ai


@pytest.fixture(autouse=True)
def clean_config(monkeypatch, tmp_path):
    ai._job_vectors.clear()
    monkeypatch.setattr(ai, "_local_model", None)
    for name in ("JOBMATCH_PROVIDER", "OPENAI_API_KEY", "JOBMATCH_AI_DAILY_CALL_LIMIT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JOBMATCH_USAGE_DB", str(tmp_path / "usage.sqlite3"))
    for kind in ("EMBEDDING", "LLM"):
        for name in ("BASE_URL", "API_KEY", "MODEL"):
            monkeypatch.delenv(f"JOBMATCH_{kind}_{name}", raising=False)


def configure(monkeypatch, kind):
    for name, value in (("BASE_URL", "https://provider.example/v1"), ("API_KEY", "server-secret"), ("MODEL", "small-model")):
        monkeypatch.setenv(f"JOBMATCH_{kind}_{name}", value)


def mock_http(monkeypatch, handler):
    original = httpx.Client
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(ai.httpx, "Client", lambda **kwargs: original(transport=transport, **kwargs))


def test_provider_requires_consent_and_configuration(monkeypatch):
    monkeypatch.setattr(ai, "_post", lambda *args: pytest.fail("unexpected external call"))
    for function, args in ((ai.semantic_scores, ({}, [])), (ai.generate_suggestions, ({}, []))):
        with pytest.raises(ai.ProviderError) as error:
            function(*args)
        assert error.value.status_code == 400
        with pytest.raises(ai.ProviderError) as error:
            function(*args, consent=True)
        assert error.value.status_code == 503


def test_embeddings_http_success_private_query_not_cached_and_contacts_removed(monkeypatch):
    configure(monkeypatch, "EMBEDDING")
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        assert request.url.path == "/v1/embeddings"
        assert request.headers["authorization"] == "Bearer server-secret"
        requests.append(payload)
        return httpx.Response(200, json={"data": [{"index": index, "embedding": [3, 4]} for index in range(len(payload["input"]))]})

    mock_http(monkeypatch, handler)
    private = {"skills": ["Python jane@example.com"], "titles": ["Engineer Jane Doe"],
               "projects": ["Built Python project https://private.example +91 9876543210"],
               "education": ["Jane University"], "redacted_text": "TRUST ME jane@example.com 9876543210"}
    jobs = [{"job_id": "one", "title": "Software Engineer", "skills": ["Python"]}]
    assert ai.semantic_scores(private, jobs, consent=True) == {"one": pytest.approx(1)}
    assert ai.semantic_scores(private, jobs, consent=True) == {"one": pytest.approx(1)}
    assert len(requests) == 3  # two private requests, one cached public job request
    outgoing = json.dumps(requests)
    for pii in ("Jane", "jane", "9876543210", "private.example", "University", "TRUST ME"):
        assert pii not in outgoing
    assert len(ai._job_vectors) == 1


def test_local_minilm_scores_are_lazy_and_never_use_provider(monkeypatch):
    class Encoder:
        def encode(self, texts, normalize_embeddings):
            assert normalize_embeddings is True
            return [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]][:len(texts)]

    monkeypatch.setattr(ai, "_local_encoder", lambda: Encoder())
    result = ai.local_semantic_scores({"skills": ["Python"]}, [
        {"job_id": "a", "title": "Python Engineer"}, {"job_id": "b", "title": "SQL Analyst"}])
    assert result == {"a": pytest.approx(1), "b": pytest.approx(0)}


def test_deterministic_suggestions_are_grounded_without_credentials():
    result = ai.deterministic_suggestions({"skills": ["Python"]}, [{"skills": ["Python", "SQL"]}])
    assert result["integration"].startswith("local deterministic")
    assert [item["skill"] for item in result["suggestions"]] == ["Python", "SQL"]
    assert result["suggestions"][0]["action"] == "document"
    assert result["suggestions"][1]["action"] == "learn"


@pytest.mark.parametrize("body", [
    {"data": []}, {"data": [{"index": 1, "embedding": [1]}]},
    {"data": [{"index": 0, "embedding": [0, 0]}]},
    {"data": [{"index": 0, "embedding": [float("nan")]}]},
    {"data": [{"index": 0, "embedding": [float("inf")]}]},
    {"data": [{"index": 0, "embedding": [True]}]},
    {"data": [{"index": 0, "embedding": "bad"}]},
])
def test_bad_embedding_shapes_and_nonfinite_are_rejected(monkeypatch, body):
    configure(monkeypatch, "EMBEDDING")
    mock_http(monkeypatch, lambda request: httpx.Response(200, content=json.dumps(body)))
    with pytest.raises(ai.ProviderError, match="invalid embeddings"):
        ai.semantic_scores({}, [], consent=True)


def test_dimension_mismatch_rejected_without_cache(monkeypatch):
    configure(monkeypatch, "EMBEDDING")
    responses = iter(([1, 0], [1, 0, 0]))
    mock_http(monkeypatch, lambda request: httpx.Response(200, json={"data": [{"index": 0, "embedding": next(responses)}]}))
    with pytest.raises(ai.ProviderError, match="inconsistent"):
        ai.semantic_scores({}, [{"job_id": "1", "skills": ["Python"]}], consent=True)
    assert not ai._job_vectors


def test_embedding_batch_cost_is_bounded(monkeypatch):
    configure(monkeypatch, "EMBEDDING")
    monkeypatch.setattr(ai, "_post", lambda *args: pytest.fail("oversized corpus should not make requests"))
    with pytest.raises(ai.ProviderError) as error:
        ai.semantic_scores({}, [{}] * 201, consent=True)
    assert error.value.status_code == 422


def test_chat_http_success_grounded_conditional_actions(monkeypatch):
    configure(monkeypatch, "LLM")
    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        payload = json.loads(request.content)
        facts = json.loads(payload["messages"][1]["content"])
        assert facts["learning_skills"] == ["SQL"]
        assert facts["documentable_skills"] == ["Python"]
        assert "secret@example.com" not in request.content.decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"suggestions": [
            {"skill": "Python", "action": "document"}, {"skill": "SQL", "action": "learn"}]})}}]})
    mock_http(monkeypatch, handler)
    result = ai.generate_suggestions({"skills": ["Python"], "redacted_text": "secret@example.com"},
                                     [{"skills": ["Python", "SQL"]}], consent=True)
    assert all(item["advice"].startswith("If ") for item in result["suggestions"])
    assert result["missing_skills"] == ["SQL"]


@pytest.mark.parametrize("content", ["not JSON", "```json\n{}\n```", '{}',
    '{"suggestions":[{"skill":"Rust","action":"document"}]}',
    '{"suggestions":[{"skill":"SQL","action":"document"}]}',
    '{"suggestions":[{"skill":"Python","action":"document","achievement":"Invented 90% gain"}]}',
    '{"suggestions":[{"skill":"Python","action":"document"}],"extra":true}',
])
def test_chat_rejects_malformed_or_ungrounded_output(monkeypatch, content):
    configure(monkeypatch, "LLM")
    mock_http(monkeypatch, lambda request: httpx.Response(200, json={"choices": [{"message": {"content": content}}]}))
    with pytest.raises(ai.ProviderError, match="invalid or ungrounded"):
        ai.generate_suggestions({"skills": ["Python"]}, [{"skills": ["Python", "SQL"]}], consent=True)


def test_provider_timeout_is_safe(monkeypatch):
    configure(monkeypatch, "EMBEDDING")
    def handler(request):
        raise httpx.ReadTimeout("secret internal provider message", request=request)
    mock_http(monkeypatch, handler)
    with pytest.raises(ai.ProviderError) as error:
        ai.semantic_scores({}, [], consent=True)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("base", ["not a URL", "https://user:password@example.com/v1", "https://[bad", "https://example.com/v1?secret=1", "http://provider.example/v1"])
def test_invalid_server_configuration_fails_closed(monkeypatch, base):
    configure(monkeypatch, "EMBEDDING")
    monkeypatch.setenv("JOBMATCH_EMBEDDING_BASE_URL", base)
    monkeypatch.setattr(ai, "_post", lambda *args: pytest.fail("invalid config must not make requests"))
    with pytest.raises(ai.ProviderError) as error:
        ai.semantic_scores({}, [], consent=True)
    assert error.value.status_code == 503


def test_batch_embeddings_validate_indices_dimensions_and_count(monkeypatch):
    configure(monkeypatch, "EMBEDDING")
    for body in (
        {"data": [{"index": 0, "embedding": [1, 0]}, {"index": 0, "embedding": [1, 0]}]},
        {"data": [{"index": 0, "embedding": [1, 0]}, {"index": 1, "embedding": [1]}]},
        {"data": [{"index": 0, "embedding": [1, 0]}]},
    ):
        monkeypatch.setattr(ai, "_post", lambda *args, body=body: body)
        with pytest.raises(ai.ProviderError, match="invalid embeddings"):
            ai._embeddings(("https://example.com/v1", "key", "model"), ["a", "b"])


def test_openai_is_explicit_opt_in_and_defaults_are_per_kind(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "shared-openai-test-key")
    with pytest.raises(ai.ProviderError) as error:
        ai._config("EMBEDDING", True)
    assert error.value.status_code == 503
    monkeypatch.setenv("JOBMATCH_PROVIDER", "openai")
    assert ai._config("EMBEDDING", True) == ("https://api.openai.com/v1", "shared-openai-test-key", "text-embedding-3-small")
    assert ai._config("LLM", True) == ("https://api.openai.com/v1", "shared-openai-test-key", "gpt-4.1-mini")
    with pytest.raises(ai.ProviderError) as error:
        ai._config("LLM", False)
    assert error.value.status_code == 400
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(ai.ProviderError) as error:
        ai._config("LLM", True)
    assert error.value.status_code == 503


@pytest.mark.parametrize("kind", ["EMBEDDING", "LLM"])
@pytest.mark.parametrize("base", ["https://gateway.example/v1", "https://api.openai.com.evil.example/v1", "https://api.openai.com:8443/v1", "http://localhost:8000/v1"])
def test_openai_shared_key_never_implicitly_sent_to_custom_base(monkeypatch, kind, base):
    monkeypatch.setenv("JOBMATCH_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "shared-openai-test-key")
    monkeypatch.setenv(f"JOBMATCH_{kind}_BASE_URL", base)
    with pytest.raises(ai.ProviderError) as error:
        ai._config(kind, True)
    assert error.value.status_code == 503
    monkeypatch.setenv(f"JOBMATCH_{kind}_API_KEY", "explicit-gateway-key")
    monkeypatch.setenv(f"JOBMATCH_{kind}_MODEL", "explicit-model")
    assert ai._config(kind, True) == (base, "explicit-gateway-key", "explicit-model")


def test_openai_default_embedding_and_chat_http(monkeypatch):
    monkeypatch.setenv("JOBMATCH_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "shared-openai-test-key")
    calls = []
    def handler(request):
        assert request.url.host == "api.openai.com"
        assert request.headers["authorization"] == "Bearer shared-openai-test-key"
        payload = json.loads(request.content)
        calls.append(payload["model"])
        if request.url.path == "/v1/embeddings":
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 0]}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"suggestions":[{"skill":"Python","action":"document"}]}'}}]})
    mock_http(monkeypatch, handler)
    ai.semantic_scores({}, [], consent=True)
    ai.generate_suggestions({"skills": ["Python"]}, [{"skills": ["Python"]}], consent=True)
    assert calls == ["text-embedding-3-small", "gpt-4.1-mini"]


def test_daily_cap_shared_between_operations_and_failed_calls_count(monkeypatch):
    configure(monkeypatch, "EMBEDDING")
    configure(monkeypatch, "LLM")
    monkeypatch.setenv("JOBMATCH_AI_DAILY_CALL_LIMIT", "1")
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(500, text="private upstream failure")
    mock_http(monkeypatch, handler)
    with pytest.raises(ai.ProviderError) as error:
        ai.semantic_scores({}, [], consent=True)
    assert error.value.status_code == 502
    with pytest.raises(ai.ProviderError) as error:
        ai.generate_suggestions({}, [{"skills": ["Python"]}], consent=True)
    assert error.value.status_code == 429
    assert "private" not in str(error.value)
    assert calls == ["/v1/embeddings"]


def test_matching_200_unique_jobs_makes_at_most_eight_calls(monkeypatch):
    configure(monkeypatch, "EMBEDDING")
    calls = []
    def handler(request):
        texts = json.loads(request.content)["input"]
        calls.append(len(texts))
        return httpx.Response(200, json={"data": [{"index": i, "embedding": [1, 0]} for i in range(len(texts))]})
    mock_http(monkeypatch, handler)
    scores = ai.semantic_scores({}, [{"job_id": str(i), "title": str(i)} for i in range(200)], consent=True)
    assert len(scores) == 200
    assert calls == [1, 32, 32, 32, 32, 32, 32, 8]
    assert len(calls) == ai.MAX_MATCH_HOSTED_CALLS == 8


def test_matching_stops_between_batches_when_global_allowance_exhausted(monkeypatch):
    configure(monkeypatch, "EMBEDDING")
    monkeypatch.setenv("JOBMATCH_AI_DAILY_CALL_LIMIT", "2")
    calls = []
    def handler(request):
        texts = json.loads(request.content)["input"]
        calls.append(len(texts))
        return httpx.Response(200, json={"data": [{"index": i, "embedding": [1, 0]} for i in range(len(texts))]})
    mock_http(monkeypatch, handler)
    with pytest.raises(ai.ProviderError) as error:
        ai.semantic_scores({}, [{"job_id": str(i), "title": str(i)} for i in range(33)], consent=True)
    assert error.value.status_code == 429
    assert calls == [1, 32]
    assert not ai._job_vectors


def test_validation_and_missing_config_do_not_create_ledger(monkeypatch, tmp_path):
    for consent, status in ((False, 400), (True, 503)):
        with pytest.raises(ai.ProviderError) as error:
            ai.semantic_scores({}, [], consent=consent)
        assert error.value.status_code == status
    configure(monkeypatch, "EMBEDDING")
    configure(monkeypatch, "LLM")
    for function, args in ((ai.semantic_scores, ({}, [{}] * 201)),
                           (ai.semantic_scores, ({}, [{}])),
                           (ai.generate_suggestions, ({}, [])),
                           (ai.generate_suggestions, ({}, [{}] * 201)),
                           (ai._embeddings, (ai._config("EMBEDDING", True), ["x" * (ai.MAX_EMBEDDING_TEXT + 1)]))):
        with pytest.raises(ai.ProviderError) as error:
            if function is ai._embeddings:
                function(*args)
            else:
                function(*args, consent=True)
        assert error.value.status_code == 422
    assert not (tmp_path / "usage.sqlite3").exists()


def test_oversized_response_is_rejected_while_streaming(monkeypatch):
    configure(monkeypatch, "EMBEDDING")
    monkeypatch.setattr(ai, "MAX_RESPONSE_BYTES", 100)
    consumed = []
    class LargeStream(httpx.SyncByteStream):
        def __iter__(self):
            for index in range(100):
                consumed.append(index)
                yield b"x" * 65536
    mock_http(monkeypatch, lambda request: httpx.Response(200, stream=LargeStream()))
    with pytest.raises(ai.ProviderError, match="request failed"):
        ai.semantic_scores({}, [], consent=True)
    assert consumed == [0]


def test_ledger_failure_blocks_http_with_safe_error(monkeypatch, tmp_path):
    configure(monkeypatch, "EMBEDDING")
    monkeypatch.setenv("JOBMATCH_USAGE_DB", str(tmp_path / "private-missing-directory" / "usage.sqlite3"))
    mock_http(monkeypatch, lambda request: pytest.fail("ledger failure must prevent HTTP"))
    with pytest.raises(ai.ProviderError) as error:
        ai.semantic_scores({}, [], consent=True)
    assert error.value.status_code == 503
    assert "private-missing-directory" not in str(error.value)


def test_ledger_contains_metadata_only(monkeypatch, tmp_path):
    configure(monkeypatch, "EMBEDDING")
    mock_http(monkeypatch, lambda request: httpx.Response(200, json={"data": [{"index": 0, "embedding": [1]}]}))
    ai.semantic_scores({"skills": ["Python"], "redacted_text": "private-resume"}, [], consent=True)
    path = tmp_path / "usage.sqlite3"
    with sqlite3.connect(path) as connection:
        assert [row[1] for row in connection.execute("PRAGMA table_info(daily_usage)")] == ["day", "category", "calls"]
        assert connection.execute("SELECT category, calls FROM daily_usage").fetchall() == [("global", 1)]
    for secret in (b"private-resume", b"server-secret", b"Python", b"provider.example"):
        assert secret not in path.read_bytes()
