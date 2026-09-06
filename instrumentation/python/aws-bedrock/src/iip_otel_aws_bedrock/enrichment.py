"""Enrich pinned botocore Bedrock spans without entering the request path."""

from __future__ import annotations

import logging
from importlib.metadata import PackageNotFoundError, version
from threading import Lock
from timeit import default_timer
from typing import Any, Mapping

from botocore.eventstream import EventStream
from opentelemetry.instrumentation.botocore import _BOTOCORE_EXTENSIONS
from opentelemetry.instrumentation.botocore.extensions.bedrock import (
    _BedrockRuntimeExtension,
)
from opentelemetry.instrumentation.botocore.extensions.bedrock_utils import (
    ConverseStreamWrapper,
)
from opentelemetry.instrumentation.botocore.extensions.types import (
    _BotocoreInstrumentorContext,
)
from opentelemetry.trace.span import Span


_LOGGER = logging.getLogger(__name__)
_EXPECTED_INSTRUMENTATION_VERSION = "0.65b0"
_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_INPUT_TOKENS = "gen_ai.usage.input_tokens"
_CACHE_READ_INPUT_TOKENS = "gen_ai.usage.cache_read.input_tokens"
_CACHE_CREATION_INPUT_TOKENS = "gen_ai.usage.cache_creation.input_tokens"
_USAGE_FIELDS = (
    "inputTokens",
    "outputTokens",
    "cacheReadInputTokens",
    "cacheWriteInputTokens",
)
_LOCK = Lock()
_INSTALLED = False


def _quantity(value: object) -> int | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _MAX_SAFE_INTEGER
    ):
        return None
    return value


def _enrich_usage(span: Span, response: Mapping[str, object]) -> None:
    """Set only validated usage counters; malformed metadata loses enrichment."""

    try:
        usage = response.get("usage")
        if not isinstance(usage, Mapping) or not span.is_recording():
            return
        provider_input = _quantity(usage.get("inputTokens"))
        cache_read = _quantity(usage.get("cacheReadInputTokens"))
        cache_write = _quantity(usage.get("cacheWriteInputTokens"))
        if provider_input is not None:
            total_input = provider_input
            for value in (cache_read, cache_write):
                if value is not None:
                    total_input += value
            if total_input <= _MAX_SAFE_INTEGER:
                span.set_attribute(_INPUT_TOKENS, total_input)
        if cache_read is not None:
            span.set_attribute(_CACHE_READ_INPUT_TOKENS, cache_read)
        if cache_write is not None:
            span.set_attribute(_CACHE_CREATION_INPUT_TOKENS, cache_write)
    except Exception:  # telemetry must never affect a provider result
        _LOGGER.exception("AWS Bedrock usage enrichment failed")


class _UsageAwareConverseStreamWrapper(ConverseStreamWrapper):
    """Retain provider cache counters until the official span callback runs."""

    def _process_event(self, event: object) -> None:
        try:
            if isinstance(event, Mapping) and "metadata" in event:
                metadata = event.get("metadata")
                usage = metadata.get("usage") if isinstance(metadata, Mapping) else None
                if isinstance(usage, Mapping):
                    retained = {
                        field: value
                        for field in _USAGE_FIELDS
                        if (value := _quantity(usage.get(field))) is not None
                    }
                    if retained:
                        self._response["usage"] = retained
                self._complete_stream(self._response)
                return
        except Exception:  # fall through to the upstream behavior on any drift
            _LOGGER.exception("AWS Bedrock stream usage enrichment failed")
        super()._process_event(event)


class _UsageAwareBedrockRuntimeExtension(_BedrockRuntimeExtension):
    """Pinned upstream extension with provider-to-OTel usage normalization."""

    def before_service_call(
        self,
        span: Span,
        instrumentor_context: _BotocoreInstrumentorContext,
    ) -> None:
        del instrumentor_context
        if self._call_context.operation not in self._HANDLED_OPERATIONS:
            return
        if span.is_recording():
            operation_name = span.attributes.get("gen_ai.operation.name", "")
            request_model = span.attributes.get("gen_ai.request.model", "")
            if operation_name and request_model:
                span.update_name(f"{operation_name} {request_model}")
        self._operation_start = default_timer()

    def _converse_on_success(
        self,
        span: Span,
        result: dict[str, Any],
        instrumentor_context: _BotocoreInstrumentorContext,
        capture_content: bool,
    ) -> None:
        del capture_content
        super()._converse_on_success(
            span,
            result,
            instrumentor_context,
            False,
        )
        _enrich_usage(span, result)

    def on_success(
        self,
        span: Span,
        result: dict[str, Any],
        instrumentor_context: _BotocoreInstrumentorContext,
    ) -> None:
        if self._call_context.operation == "ConverseStream":
            stream = result.get("stream")
            if isinstance(stream, EventStream):

                def stream_done_callback(
                    response: dict[str, Any], span_ended: bool
                ) -> None:
                    self._converse_on_success(
                        span,
                        response,
                        instrumentor_context,
                        False,
                    )
                    if not span_ended:
                        span.end()

                def stream_error_callback(exception: Exception, span_ended: bool) -> None:
                    self._on_stream_error_callback(
                        span,
                        exception,
                        instrumentor_context,
                        span_ended,
                    )

                result["stream"] = _UsageAwareConverseStreamWrapper(
                    stream,
                    stream_done_callback,
                    stream_error_callback,
                )
                return
        super().on_success(span, result, instrumentor_context)


def _usage_extension_loader() -> type[_UsageAwareBedrockRuntimeExtension]:
    return _UsageAwareBedrockRuntimeExtension


def install() -> bool:
    """Install the enrichment before official botocore instrumentation loads."""

    global _INSTALLED  # pylint: disable=global-statement
    with _LOCK:
        if _INSTALLED:
            return True
        try:
            installed_version = version("opentelemetry-instrumentation-botocore")
        except PackageNotFoundError:
            _LOGGER.error("OpenTelemetry botocore instrumentation is not installed")
            return False
        if installed_version != _EXPECTED_INSTRUMENTATION_VERSION:
            _LOGGER.error(
                "Unsupported OpenTelemetry botocore instrumentation version %s",
                installed_version,
            )
            return False
        _BOTOCORE_EXTENSIONS["bedrock-runtime"] = _usage_extension_loader
        _INSTALLED = True
        return True
