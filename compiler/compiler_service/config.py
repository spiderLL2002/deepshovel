"""Environment configuration with immutable model/backend choices."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

GPT_MAX_ATTEMPTS = 6  # Initial request plus five retries.
MODEL_RETRY_DELAY_SECONDS = 5.0
GPT_BASE_URL = "https://api.iiot-shxt.xyz/v1"
GPT_MODEL = "gpt-6-luna"
GPT_REASONING_EFFORT = "max"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-flash"
DEEPSEEK_MAX_ATTEMPTS = 3
MODEL_MAX_ATTEMPTS = GPT_MAX_ATTEMPTS + DEEPSEEK_MAX_ATTEMPTS
MINERU_BACKEND = "pipeline"
MINERU_PARSE_METHOD = "auto"
MINERU_API_URL = "https://mineru.acemap.cn/file_parse"
MINERU_API_HOST = "mineru.acemap.cn"
MINERU_TUNNEL_URL = "http://127.0.0.1:18006/file_parse"
# The operator's public jobs API behind Cloudflare: submit, poll, then fetch the
# result, because Cloudflare ends a request whose response has not begun in 100 s.
MINERU_JOBS_URL = "https://parse.iiot-shxt.xyz/jobs"
DEFAULT_MINERU_CONCURRENCY = 1
MAX_MINERU_CONCURRENCY = 4
MINERU_PROTOCOL_VERSION = 1
MINERU_MAX_ATTEMPTS = 3
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_MINERU_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_DOCUMENT_JSON_BYTES = 16 * 1024 * 1024
DEFAULT_JOB_TTL_SECONDS = 24 * 60 * 60
DEFAULT_MINERU_TIMEOUT_SECONDS = 15 * 60
MAX_MINERU_TIMEOUT_SECONDS = 60 * 60
DEFAULT_GPT_CONCURRENCY = 64
MAX_GPT_CONCURRENCY = 64
DEFAULT_RETRIEVAL_DENSE_MODEL = (
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)
DEFAULT_RETRIEVAL_DENSE_MODEL_REVISION = "onnx-default-v1"
DEFAULT_RETRIEVAL_RRF_K = 60
DEFAULT_RETRIEVAL_CONCURRENCY = 64
MAX_RETRIEVAL_CONCURRENCY = 64
OPENALEX_API_URL = "https://api.openalex.org"
OPENALEX_TOPIC_MODEL = (
    "OpenAlex/bert-base-multilingual-cased-finetuned-"
    "openalex-topic-classification-title-abstract"
)


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    environment: Literal["development", "production"] = "development"
    mineru_url: str = MINERU_API_URL
    mineru_timeout_seconds: int = DEFAULT_MINERU_TIMEOUT_SECONDS
    mineru_concurrency: int = DEFAULT_MINERU_CONCURRENCY
    mineru_api_token: str | None = None
    gpt_concurrency: int = DEFAULT_GPT_CONCURRENCY
    gpt_api_key: str | None = None
    deepseek_api_key: str | None = None
    shared_secret: str | None = None
    job_ttl_seconds: int = DEFAULT_JOB_TTL_SECONDS
    retrieval_dense_model: str = DEFAULT_RETRIEVAL_DENSE_MODEL
    retrieval_dense_model_revision: str = DEFAULT_RETRIEVAL_DENSE_MODEL_REVISION
    retrieval_remote_models: bool = True
    retrieval_rrf_k: int = DEFAULT_RETRIEVAL_RRF_K
    retrieval_concurrency: int = DEFAULT_RETRIEVAL_CONCURRENCY
    retrieval_materialized_ranking_features: bool = False
    retrieval_qdrant_url: str | None = None
    retrieval_qdrant_api_key: str | None = None
    # "qdrant" (embedded, or the server at RETRIEVAL_QDRANT_URL) or "numpy",
    # the disk-backed float16 store (see compiler_service.retrieval_vectors).
    retrieval_vector_backend: str = "qdrant"
    openalex_api_key: str | None = None

    def __post_init__(self) -> None:
        validate_mineru_url(self.mineru_url)
        validate_mineru_token(self.mineru_url, self.mineru_api_token)
        if (
            type(self.mineru_concurrency) is not int
            or not 1 <= self.mineru_concurrency <= MAX_MINERU_CONCURRENCY
        ):
            raise ValueError(
                "COMPILER_MINERU_CONCURRENCY must be an integer from 1 to 4"
            )
        if (
            type(self.mineru_timeout_seconds) is not int
            or not 1 <= self.mineru_timeout_seconds <= MAX_MINERU_TIMEOUT_SECONDS
        ):
            raise ValueError(
                "COMPILER_MINERU_TIMEOUT_SECONDS must be an integer from 1 to "
                f"{MAX_MINERU_TIMEOUT_SECONDS}"
            )
        if (
            type(self.gpt_concurrency) is not int
            or not 1 <= self.gpt_concurrency <= MAX_GPT_CONCURRENCY
        ):
            raise ValueError(
                "COMPILER_GPT_CONCURRENCY must be an integer from 1 to "
                f"{MAX_GPT_CONCURRENCY}"
            )
        if self.shared_secret is not None and (
            not 32 <= len(self.shared_secret) <= 512
            or any(not 33 <= ord(character) <= 126 for character in self.shared_secret)
        ):
            raise ValueError(
                "COMPILER_SHARED_SECRET must be 32-512 non-whitespace ASCII characters"
            )
        if self.environment == "production" and self.shared_secret is None:
            raise ValueError(
                "COMPILER_SHARED_SECRET is required when COMPILER_ENV=production"
            )
        for name, value in (
            ("RETRIEVAL_DENSE_MODEL", self.retrieval_dense_model),
            ("RETRIEVAL_DENSE_MODEL_REVISION", self.retrieval_dense_model_revision),
        ):
            if (
                not value.strip()
                or len(value) > 200
                or any(ord(item) < 32 for item in value)
            ):
                raise ValueError(f"{name} must be a nonempty bounded model identity")
        if (
            type(self.retrieval_rrf_k) is not int
            or not 1 <= self.retrieval_rrf_k <= 1_000
        ):
            raise ValueError("RETRIEVAL_RRF_K must be an integer from 1 to 1000")
        if (
            type(self.retrieval_concurrency) is not int
            or not 1 <= self.retrieval_concurrency <= MAX_RETRIEVAL_CONCURRENCY
        ):
            raise ValueError(
                "RETRIEVAL_CONCURRENCY must be an integer from 1 to "
                f"{MAX_RETRIEVAL_CONCURRENCY}"
            )
        if type(self.retrieval_materialized_ranking_features) is not bool:
            raise ValueError("RETRIEVAL_MATERIALIZED_RANKING_FEATURES must be Boolean")
        if self.retrieval_qdrant_url is not None:
            validate_qdrant_url(self.retrieval_qdrant_url)
        elif self.retrieval_qdrant_api_key is not None:
            raise ValueError("RETRIEVAL_QDRANT_API_KEY requires RETRIEVAL_QDRANT_URL")
        if self.retrieval_vector_backend not in {"qdrant", "numpy"}:
            raise ValueError("RETRIEVAL_VECTOR_BACKEND must be qdrant or numpy")
        if self.retrieval_vector_backend == "numpy" and self.retrieval_qdrant_url:
            raise ValueError(
                "RETRIEVAL_VECTOR_BACKEND=numpy excludes RETRIEVAL_QDRANT_URL"
            )

    @classmethod
    def from_environment(cls) -> Settings:
        data_dir = Path(
            os.environ.get("COMPILER_DATA_DIR", "./.compiler-data")
        ).expanduser()
        api_key = os.environ.get("GPT_API_KEY", "").strip() or None
        shared_secret = os.environ.get("COMPILER_SHARED_SECRET", "").strip() or None
        mineru_url = (
            os.environ.get("COMPILER_MINERU_URL", MINERU_API_URL).strip()
            or MINERU_API_URL
        )
        environment = os.environ.get("COMPILER_ENV", "development").strip().lower()
        if environment not in {"development", "production"}:
            raise ValueError("COMPILER_ENV must be development or production")
        timeout = _bounded_int(
            "COMPILER_MINERU_TIMEOUT_SECONDS",
            DEFAULT_MINERU_TIMEOUT_SECONDS,
            MAX_MINERU_TIMEOUT_SECONDS,
        )
        ttl = _positive_int("COMPILER_JOB_TTL_SECONDS", DEFAULT_JOB_TTL_SECONDS)
        gpt_concurrency = _bounded_int(
            "COMPILER_GPT_CONCURRENCY",
            DEFAULT_GPT_CONCURRENCY,
            MAX_GPT_CONCURRENCY,
        )
        retrieval_concurrency = _bounded_int(
            "RETRIEVAL_CONCURRENCY",
            DEFAULT_RETRIEVAL_CONCURRENCY,
            MAX_RETRIEVAL_CONCURRENCY,
        )
        retrieval_rrf_k = _bounded_int(
            "RETRIEVAL_RRF_K",
            DEFAULT_RETRIEVAL_RRF_K,
            1_000,
        )
        retrieval_remote_models = _strict_bool("RETRIEVAL_REMOTE_MODELS", default=True)
        retrieval_materialized_ranking_features = _strict_bool(
            "RETRIEVAL_MATERIALIZED_RANKING_FEATURES", default=False
        )
        qdrant_url = os.environ.get("RETRIEVAL_QDRANT_URL", "").strip() or None
        qdrant_api_key = os.environ.get("RETRIEVAL_QDRANT_API_KEY", "").strip() or None
        vector_backend = (
            os.environ.get("RETRIEVAL_VECTOR_BACKEND", "qdrant").strip().lower()
            or "qdrant"
        )
        openalex_api_key = os.environ.get("OPENALEX_API_KEY", "").strip() or None
        return cls(
            data_dir=data_dir,
            environment=environment,
            mineru_url=mineru_url,
            mineru_timeout_seconds=timeout,
            mineru_concurrency=_bounded_int(
                "COMPILER_MINERU_CONCURRENCY",
                DEFAULT_MINERU_CONCURRENCY,
                MAX_MINERU_CONCURRENCY,
            ),
            mineru_api_token=os.environ.get("COMPILER_MINERU_API_TOKEN", "").strip()
            or None,
            gpt_concurrency=gpt_concurrency,
            gpt_api_key=api_key,
            deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY", "").strip() or None,
            shared_secret=shared_secret,
            job_ttl_seconds=ttl,
            retrieval_dense_model=os.environ.get(
                "RETRIEVAL_DENSE_MODEL", DEFAULT_RETRIEVAL_DENSE_MODEL
            ).strip(),
            retrieval_dense_model_revision=os.environ.get(
                "RETRIEVAL_DENSE_MODEL_REVISION",
                DEFAULT_RETRIEVAL_DENSE_MODEL_REVISION,
            ).strip(),
            retrieval_remote_models=retrieval_remote_models,
            retrieval_rrf_k=retrieval_rrf_k,
            retrieval_concurrency=retrieval_concurrency,
            retrieval_materialized_ranking_features=(
                retrieval_materialized_ranking_features
            ),
            retrieval_qdrant_url=qdrant_url,
            retrieval_qdrant_api_key=qdrant_api_key,
            retrieval_vector_backend=vector_backend,
            openalex_api_key=openalex_api_key,
        )


def _strict_bool(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true"}:
        return True
    if normalized in {"0", "false"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _bounded_int(name: str, default: int, maximum: int) -> int:
    value = _positive_int(name, default)
    if value > maximum:
        raise ValueError(f"{name} must not exceed {maximum}")
    return value


def validate_mineru_url(value: str) -> None:
    """Allow the lab HTTPS route, the operator's jobs API and the SSH tunnel."""

    if value in {MINERU_TUNNEL_URL, MINERU_JOBS_URL}:
        return

    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        port = -1
    if (
        parsed.scheme != "https"
        or parsed.hostname != MINERU_API_HOST
        or port not in {None, 443}
        or parsed.path != "/file_parse"
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(
            "COMPILER_MINERU_URL must be the allowlisted HTTPS "
            "mineru.acemap.cn/file_parse endpoint, the parse.iiot-shxt.xyz/jobs "
            "endpoint or the exact loopback tunnel endpoint"
        )


def validate_mineru_token(url: str, token: str | None) -> None:
    if token is None:
        return
    if url != MINERU_TUNNEL_URL:
        raise ValueError(
            "COMPILER_MINERU_API_TOKEN requires the approved loopback tunnel endpoint"
        )
    if not 1 <= len(token) <= 512 or any(not 33 <= ord(char) <= 126 for char in token):
        raise ValueError(
            "COMPILER_MINERU_API_TOKEN must be 1-512 non-whitespace ASCII characters"
        )


def validate_qdrant_url(value: str) -> None:
    parsed = urlsplit(value)
    loopback_hosts = {"127.0.0.1", "localhost", "::1"}
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or (parsed.scheme == "http" and parsed.hostname not in loopback_hosts)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "RETRIEVAL_QDRANT_URL must be an HTTPS origin without credentials; "
            "HTTP is allowed only for a loopback host"
        )
