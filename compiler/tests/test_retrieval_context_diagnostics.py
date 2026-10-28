"""Value-free 2.3 planning failure observations; no production model calls."""

import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_retrieval_context_contract import request23
from test_retrieval_providers import _response, _sdk
from test_retrieval_service import _search_request

from compiler_service.config import GPT_MAX_ATTEMPTS
from compiler_service.errors import (
    ModelAttemptDiagnostic,
    ModelJobBudgetExceeded,
    ModelOutputError,
    ModelRequestError,
    ModelRequestErrorKind,
    ModelUnavailable,
)
from compiler_service.model_client import ModelJsonClient
from compiler_service.retrieval_contract import RetrievalContextState
from compiler_service.retrieval_providers import (
    ModelQueryPlanner,
    QueryPlanningInputTooLarge,
)
from compiler_service.retrieval_service import RetrievalPlanningError, RetrievalService

PRIVATE = "PRIVATE_query_model_token_exception"
LOG_PREFIX = "Context retrieval planning failed: "


def _invoke(error):
    def fail(*args, **kwargs):
        raise error

    service = SimpleNamespace(
        query_planner=SimpleNamespace(
            plan_context=fail, plan_interactive_context=fail
        ),
        remote_query_planner_enabled=True,
    )
    request = _search_request(
        PRIVATE, schema_version="evidence-retrieval/2.3", stage="preview"
    )
    return RetrievalService._context_decision(
        service,
        request,
        context=RetrievalContextState(supplements=[PRIVATE]),
    )


def _observation(caplog):
    records = [
        record for record in caplog.records if record.message.startswith(LOG_PREFIX)
    ]
    assert len(records) == 1
    assert records[0].exc_info is None
    assert PRIVATE not in caplog.text
    assert "Traceback" not in caplog.text
    return json.loads(records[0].message.removeprefix(LOG_PREFIX))


def test_invalid_output_logs_only_last_sanitized_schema_diagnostic(caplog):
    caplog.set_level(logging.WARNING, logger="compiler_service.retrieval_service")
    wire = {
        "action": "needs_context",
        "needs_context": {"question": PRIVATE},
        PRIVATE: PRIVATE,
    }
    create = Mock(return_value=_response(json.dumps(wire)))
    planner = ModelQueryPlanner(ModelJsonClient(_sdk(create), sleep=lambda _: None))
    with pytest.raises(ModelOutputError) as captured:
        planner.plan_context(request23(), context=RetrievalContextState())
    error = captured.value
    last = error.diagnostics[-1]
    error.diagnostics = (
        ModelAttemptDiagnostic(attempt=1, classification="invalid_json"),
        last,
    )
    error.args = (PRIVATE,)
    with pytest.raises(RetrievalPlanningError) as public:
        _invoke(error)
    assert public.value.code == "RETRIEVAL_PLANNING_OUTPUT_INVALID"
    observation = _observation(caplog)
    assert observation == {
        "failure_kind": "OUTPUT_INVALID",
        "classification": "schema_validation_failed",
        "finish_reason": "stop",
        "schema_errors": [
            {"loc": list(item.loc), "type": item.type} for item in last.schema_errors
        ],
    }
    assert "<key>" in caplog.text
    assert last.content_sha256 not in caplog.text
    assert "invalid_json" not in caplog.text
    assert create.call_count == GPT_MAX_ATTEMPTS


@pytest.mark.parametrize(
    "error,kind",
    [
        (ModelOutputError(PRIVATE), "OUTPUT_INVALID"),
        (RuntimeError(PRIVATE), "INTERNAL_ERROR"),
        (TimeoutError(PRIVATE), "TIMEOUT"),
        (ModelUnavailable(PRIVATE), "UPSTREAM_FAILED"),
        (QueryPlanningInputTooLarge(PRIVATE), "INPUT_TOO_LARGE"),
        (ModelJobBudgetExceeded(PRIVATE), "BUDGET_EXCEEDED"),
        (RetrievalPlanningError("PROVIDER_INVALID"), "PROVIDER_INVALID"),
        (RetrievalPlanningError("UNAVAILABLE"), "UNAVAILABLE"),
    ],
)
def test_missing_diagnostic_still_logs_fixed_failure_category(caplog, error, kind):
    caplog.set_level(logging.WARNING, logger="compiler_service.retrieval_service")
    with pytest.raises(RetrievalPlanningError) as captured:
        _invoke(error)
    assert captured.value.code == f"RETRIEVAL_PLANNING_{kind}"
    assert _observation(caplog) == {"failure_kind": kind}


def test_request_failure_logs_only_enum_and_http_status(caplog):
    caplog.set_level(logging.WARNING, logger="compiler_service.retrieval_service")
    error = ModelRequestError(
        ModelRequestErrorKind.RATE_LIMITED,
        http_status=429,
        diagnostics=(ModelAttemptDiagnostic(attempt=1, classification="invalid_json"),),
    )
    error.args = (PRIVATE,)
    with pytest.raises(RetrievalPlanningError):
        _invoke(error)
    assert _observation(caplog) == {
        "failure_kind": "UPSTREAM_FAILED",
        "request_error_kind": "rate_limited",
        "request_http_status": 429,
    }
