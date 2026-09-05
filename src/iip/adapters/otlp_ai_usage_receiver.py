"""Protected OTLP/HTTP trace adapter for metadata-only AI usage intake."""

from __future__ import annotations

import gzip
import hashlib
import hmac
import io
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping

from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import Span, Status

from iip.application.ingest_ai_usage import (
    AiUsageBatch,
    AiUsageChannel,
    AiUsageChannelLimits,
    AiUsagePayloadTooLargeError,
    AiUsageSpan,
    InvalidAiUsageRequestError,
    validate_ai_usage_channel,
)
from iip.application.ingest_otlp_metrics import (
    OtlpReceiverAuthenticationError,
    OtlpReceiverConfigurationError,
)
from iip.application.ports import ActorContext


_SHA256 = re.compile(r"sha256:[a-f0-9]{64}")
_CHANNEL_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_TENANT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_INTEGRATION_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_PROVIDER = re.compile(r"[a-z0-9][a-z0-9._-]{1,63}")
_OPERATION = re.compile(r"[a-z][a-z0-9._-]{1,63}")
_REGION = re.compile(r"[a-z0-9][a-z0-9-]{1,63}")
_RESOURCE_UID = re.compile(r"res_[a-f0-9]{32}")
_ATTRIBUTE = re.compile(r"[A-Za-z_][A-Za-z0-9_.:/-]{0,127}")
_SAFE_VALUE = re.compile(r"[^\x00-\x1f\x7f]{1,256}")
_SENSITIVE_NAME = re.compile(
    r"(?:authorization|password|passwd|secret|apikey|accesskey|privatekey)",
    re.IGNORECASE,
)
_CONTENT_NAME_PARTS = frozenset(
    {
        "body",
        "completion",
        "document",
        "documents",
        "embedding",
        "embeddings",
        "inputmessage",
        "inputmessages",
        "instruction",
        "instructions",
        "message",
        "messages",
        "outputmessage",
        "outputmessages",
        "prompt",
        "prompts",
        "requestbody",
        "responsebody",
        "retrieveddocument",
        "retrieveddocuments",
        "systemmessage",
        "systemmessages",
        "toolargument",
        "toolarguments",
        "toolcall",
        "toolcalls",
    }
)
_USAGE_FIELDS = (
    "inputTokens",
    "outputTokens",
    "cacheReadInputTokens",
    "cacheWriteInputTokens",
    "reasoningOutputTokens",
)
_BREAKDOWN_FIELDS = frozenset(
    {"cacheReadInputTokens", "cacheWriteInputTokens", "reasoningOutputTokens"}
)
_SERVICE_REQUIRED = frozenset({"otlpName", "serviceName", "resourceRefs"})
_SERVICE_ALLOWED = _SERVICE_REQUIRED | {
    "serviceNamespace",
    "deploymentEnvironment",
}


@dataclass(frozen=True)
class _ServiceProfile:
    otlp_name: str
    service_name: str
    service_namespace: str | None
    deployment_environment: str | None
    resource_uids: tuple[str, ...]


@dataclass(frozen=True)
class _UsageProfile:
    attributes: Mapping[str, str]
    zero_when_absent: frozenset[str]
    reported_by: str


@dataclass(frozen=True)
class _InvocationProfile:
    attributes: Mapping[str, str]
    zero_when_absent: frozenset[str]


@dataclass(frozen=True)
class _ChannelProfile:
    channel: AiUsageChannel
    services: Mapping[str, _ServiceProfile]
    usage: _UsageProfile
    invocation: _InvocationProfile


class ConfiguredAiUsageReceiver:
    """Authenticate an AI telemetry channel and discard all non-allowlisted data."""

    def __init__(
        self,
        verifiers: Mapping[str, str],
        profiles: Mapping[str, _ChannelProfile],
    ) -> None:
        self._verifiers = dict(verifiers)
        self._profiles = dict(profiles)

    @classmethod
    def from_json(cls, raw: str) -> "ConfiguredAiUsageReceiver":
        try:
            if not isinstance(raw, str) or not 2 <= len(raw) <= 1_048_576:
                raise ValueError
            document = json.loads(raw)
            if not isinstance(document, dict) or set(document) != {"channels"}:
                raise ValueError
            entries = document["channels"]
            if not isinstance(entries, list) or not 1 <= len(entries) <= 64:
                raise ValueError
            verifiers: dict[str, str] = {}
            profiles: dict[str, _ChannelProfile] = {}
            for entry in entries:
                profile, verifier = cls._parse_channel(entry)
                channel_id = profile.channel.channel_id
                if verifier in verifiers or channel_id in profiles:
                    raise ValueError
                verifiers[verifier] = channel_id
                profiles[channel_id] = profile
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise OtlpReceiverConfigurationError("otlp.configuration.invalid") from None
        return cls(verifiers, profiles)

    @staticmethod
    def token_sha256(token: str) -> str:
        return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()

    def authenticate_bearer(self, token: str) -> AiUsageChannel:
        if (
            not isinstance(token, str)
            or not 32 <= len(token) <= 4096
            or any(character.isspace() or ord(character) < 33 for character in token)
        ):
            raise OtlpReceiverAuthenticationError("otlp.authentication.invalid")
        presented = self.token_sha256(token)
        channel_id = None
        for verifier, candidate in self._verifiers.items():
            if hmac.compare_digest(presented, verifier):
                channel_id = candidate
        if channel_id is None:
            raise OtlpReceiverAuthenticationError("otlp.authentication.invalid")
        return self._profiles[channel_id].channel

    def decode_traces(
        self,
        channel: AiUsageChannel,
        payload: bytes,
        *,
        content_encoding: str,
    ) -> AiUsageBatch:
        profile = self._profiles.get(channel.channel_id)
        if profile is None or profile.channel != channel:
            raise OtlpReceiverConfigurationError("otlp.configuration.invalid")
        decoded = self._decode_content(
            payload,
            content_encoding,
            channel.limits.max_request_bytes,
        )
        request = ExportTraceServiceRequest()
        try:
            request.ParseFromString(decoded)
        except DecodeError:
            raise InvalidAiUsageRequestError("otlp.protobuf.invalid") from None

        normalized: list[AiUsageSpan] = []
        for resource_spans in request.resource_spans:
            resource = resource_spans.resource
            if resource.dropped_attributes_count:
                raise InvalidAiUsageRequestError("otlp.attributes.dropped")
            resource_attributes = self._attribute_values(
                resource.attributes,
                channel.limits.max_attributes_per_span,
            )
            service_name = self._required_string(resource_attributes, "service.name")
            service = profile.services.get(service_name)
            if service is None:
                raise InvalidAiUsageRequestError("otlp.service.not-allowlisted")
            self._matches_optional_resource_identity(resource_attributes, service)

            for scope_spans in resource_spans.scope_spans:
                scope = scope_spans.scope
                if scope.dropped_attributes_count:
                    raise InvalidAiUsageRequestError("otlp.attributes.dropped")
                scope_attributes = self._attribute_values(
                    scope.attributes,
                    channel.limits.max_attributes_per_span,
                )
                if scope.name not in channel.instrumentation_scopes:
                    raise InvalidAiUsageRequestError("otlp.scope.not-allowlisted")
                self._safe_value(scope.name)
                if scope.version:
                    self._safe_value(scope.version, maximum=64)
                for source in scope_spans.spans:
                    if len(normalized) >= channel.limits.max_spans:
                        raise InvalidAiUsageRequestError("otlp.span.limit")
                    normalized.append(
                        self._normalize_span(
                            profile,
                            service,
                            resource_attributes,
                            scope_attributes,
                            scope.name,
                            scope.version or None,
                            source,
                        )
                    )

        normalized.sort(
            key=lambda item: (item.started_at, item.trace_id, item.span_id)
        )
        return AiUsageBatch(tuple(normalized))

    @classmethod
    def _normalize_span(
        cls,
        profile: _ChannelProfile,
        service: _ServiceProfile,
        resource_attributes: Mapping[str, AnyValue],
        scope_attributes: Mapping[str, AnyValue],
        scope_name: str,
        scope_version: str | None,
        source: Span,
    ) -> AiUsageSpan:
        channel = profile.channel
        if source.kind != Span.SPAN_KIND_CLIENT:
            raise InvalidAiUsageRequestError("otlp.span.kind.unsupported")
        if (
            source.dropped_attributes_count
            or source.dropped_events_count
            or source.dropped_links_count
        ):
            raise InvalidAiUsageRequestError("otlp.attributes.dropped")
        if source.events or source.links:
            raise InvalidAiUsageRequestError("otlp.span.content-prohibited")
        if (
            len(source.trace_id) != 16
            or len(source.span_id) != 8
            or not any(source.trace_id)
            or not any(source.span_id)
        ):
            raise InvalidAiUsageRequestError("otlp.trace-context.invalid")
        if source.parent_span_id and (
            len(source.parent_span_id) != 8 or not any(source.parent_span_id)
        ):
            raise InvalidAiUsageRequestError("otlp.trace-context.invalid")

        span_attributes = cls._attribute_values(
            source.attributes,
            channel.limits.max_attributes_per_span,
        )
        all_layers = (resource_attributes, scope_attributes, span_attributes)
        for attributes in all_layers:
            cls._reject_prohibited_names(attributes)

        provider = cls._provider(span_attributes)
        operation = cls._required_string(span_attributes, "gen_ai.operation.name")
        request_model = cls._required_string(span_attributes, "gen_ai.request.model")
        response_model = cls._optional_string(span_attributes, "gen_ai.response.model")
        region = cls._unique_string(all_layers, "cloud.region")
        if (
            provider != channel.provider
            or operation not in channel.operations
            or request_model not in channel.model_ids
            or response_model is not None and response_model not in channel.model_ids
            or region not in channel.regions
        ):
            raise InvalidAiUsageRequestError("otlp.span.not-allowlisted")

        started_at, duration_millis = cls._timing(
            source.start_time_unix_nano,
            source.end_time_unix_nano,
        )
        error_type = cls._optional_string(span_attributes, "error.type", maximum=128)
        outcome, normalized_error = cls._outcome(source.status, error_type)
        request_id_attribute = profile.invocation.attributes.get("requestId")
        request_id = (
            cls._optional_string(span_attributes, request_id_attribute)
            if request_id_attribute is not None
            else None
        )
        request_id_hash = (
            "sha256:" + hashlib.sha256(request_id.encode("utf-8")).hexdigest()
            if request_id is not None
            else None
        )
        retry_count_attribute = profile.invocation.attributes.get("retryCount")
        retry_count = (
            cls._optional_integer(
                span_attributes,
                retry_count_attribute,
                maximum=100,
            )
            if retry_count_attribute is not None
            else None
        )
        if (
            retry_count is None
            and "retryCount" in profile.invocation.zero_when_absent
        ):
            retry_count = 0

        usage_values: dict[str, int | None] = {}
        for field, attribute in profile.usage.attributes.items():
            usage_values[field] = cls._optional_integer(
                span_attributes,
                attribute,
                maximum=9_007_199_254_740_991,
            )
        for field in profile.usage.zero_when_absent:
            if usage_values[field] is None:
                usage_values[field] = 0
        missing = tuple(
            field for field in _USAGE_FIELDS if usage_values[field] is None
        )
        complete = (
            usage_values["inputTokens"] is not None
            and usage_values["outputTokens"] is not None
            and not missing
        )
        if usage_values["inputTokens"] is None and usage_values["outputTokens"] is None:
            raise InvalidAiUsageRequestError("otlp.usage.missing")

        consumed = {
            "service.name",
            "service.namespace",
            "deployment.environment.name",
            "deployment.environment",
            "cloud.region",
            "gen_ai.provider.name",
            "gen_ai.system",
            "gen_ai.operation.name",
            "gen_ai.request.model",
            "gen_ai.response.model",
            "error.type",
            *profile.invocation.attributes.values(),
            *profile.usage.attributes.values(),
        }
        dropped_count = sum(
            1 for attributes in all_layers for key in attributes if key not in consumed
        )

        return AiUsageSpan(
            provider=provider,
            operation_name=operation,
            request_model=request_model,
            response_model=response_model,
            region=region,
            service_tier=channel.service_tier,
            routing_mode=channel.routing_mode,
            purchase_mode=channel.purchase_mode,
            started_at=started_at,
            duration_millis=duration_millis,
            outcome=outcome,
            error_type=normalized_error,
            trace_id=source.trace_id.hex(),
            span_id=source.span_id.hex(),
            request_id_hash=request_id_hash,
            retry_count=retry_count,
            service_name=service.service_name,
            service_namespace=service.service_namespace,
            deployment_environment=service.deployment_environment,
            resource_uids=service.resource_uids,
            input_tokens=usage_values["inputTokens"],
            output_tokens=usage_values["outputTokens"],
            cache_read_input_tokens=usage_values["cacheReadInputTokens"],
            cache_write_input_tokens=usage_values["cacheWriteInputTokens"],
            reasoning_output_tokens=usage_values["reasoningOutputTokens"],
            reported_by=profile.usage.reported_by,
            completeness="complete" if complete else "partial",
            missing_fields=() if complete else missing,
            instrumentation_scope_name=scope_name,
            instrumentation_scope_version=scope_version,
            semantic_convention_version=channel.semantic_convention_version,
            dropped_attribute_count=dropped_count,
        )

    @classmethod
    def _provider(cls, attributes: Mapping[str, AnyValue]) -> str:
        """Normalize the stable provider key and its shipped legacy alias.

        Current OpenTelemetry Python botocore releases still emit
        ``gen_ai.system`` while the newer GenAI conventions use
        ``gen_ai.provider.name``. Accept either exact string, but never let a
        conflicting pair choose the tenant channel's provider implicitly.
        """

        provider = cls._optional_string(attributes, "gen_ai.provider.name")
        legacy_system = cls._optional_string(attributes, "gen_ai.system")
        if provider is None and legacy_system is None:
            raise InvalidAiUsageRequestError("otlp.attribute.required")
        if (
            provider is not None
            and legacy_system is not None
            and provider != legacy_system
        ):
            raise InvalidAiUsageRequestError("otlp.span.not-allowlisted")
        assert provider is not None or legacy_system is not None
        return provider if provider is not None else legacy_system

    @classmethod
    def _parse_channel(cls, entry: object) -> tuple[_ChannelProfile, str]:
        required = {
            "channelId",
            "tokenSha256",
            "tenantId",
            "integrationId",
            "provider",
            "semanticConventionVersion",
            "services",
            "models",
            "operations",
            "regions",
            "instrumentationScopes",
            "invocationAttributes",
            "usageAttributes",
            "commercial",
            "limits",
        }
        if not isinstance(entry, dict) or set(entry) != required:
            raise ValueError
        channel_id = entry["channelId"]
        verifier = entry["tokenSha256"]
        tenant_id = entry["tenantId"]
        integration_id = entry["integrationId"]
        provider = entry["provider"]
        semantic_version = entry["semanticConventionVersion"]
        if (
            not isinstance(channel_id, str)
            or not _CHANNEL_ID.fullmatch(channel_id)
            or not isinstance(verifier, str)
            or not _SHA256.fullmatch(verifier)
            or not isinstance(tenant_id, str)
            or not _TENANT_ID.fullmatch(tenant_id)
            or not isinstance(integration_id, str)
            or not _INTEGRATION_ID.fullmatch(integration_id)
            or not isinstance(provider, str)
            or not _PROVIDER.fullmatch(provider)
            or not isinstance(semantic_version, str)
            or not 1 <= len(semantic_version) <= 64
        ):
            raise ValueError
        services = cls._parse_services(entry["services"])
        models = cls._string_list(entry["models"], 256)
        operations = cls._string_list(entry["operations"], 64, _OPERATION)
        regions = cls._string_list(entry["regions"], 64, _REGION)
        scopes = cls._string_list(entry["instrumentationScopes"], 64)
        invocation = cls._parse_invocation(entry["invocationAttributes"])
        usage = cls._parse_usage(entry["usageAttributes"])
        service_tier, routing_mode, purchase_mode = cls._parse_commercial(
            entry["commercial"]
        )
        limits = cls._parse_limits(entry["limits"])
        channel = AiUsageChannel(
            actor=ActorContext(
                actor_id=f"ai-usage-channel:{channel_id}",
                tenant_id=tenant_id,
                roles=("telemetry-ingest",),
            ),
            channel_id=channel_id,
            integration_id=integration_id,
            provider=provider,
            semantic_convention_version=semantic_version,
            service_names=tuple(
                sorted(item.service_name for item in services.values())
            ),
            model_ids=models,
            operations=operations,
            regions=regions,
            instrumentation_scopes=scopes,
            service_tier=service_tier,
            routing_mode=routing_mode,
            purchase_mode=purchase_mode,
            limits=limits,
        )
        validate_ai_usage_channel(channel)
        return _ChannelProfile(channel, services, usage, invocation), verifier

    @staticmethod
    def _parse_invocation(value: object) -> _InvocationProfile:
        if not isinstance(value, dict) or set(value) != {
            "attributes",
            "zeroWhenAbsent",
        }:
            raise ValueError
        attributes = value["attributes"]
        zero_when_absent = value["zeroWhenAbsent"]
        allowed = {"requestId", "retryCount"}
        if (
            not isinstance(attributes, dict)
            or not set(attributes).issubset(allowed)
            or any(
                not isinstance(name, str)
                or not _ATTRIBUTE.fullmatch(name)
                or ConfiguredAiUsageReceiver._prohibited_name(name)
                for name in attributes.values()
            )
            or len(set(attributes.values())) != len(attributes)
            or not isinstance(zero_when_absent, list)
            or zero_when_absent != sorted(zero_when_absent)
            or len(set(zero_when_absent)) != len(zero_when_absent)
            or not set(zero_when_absent).issubset({"retryCount"})
            or not set(zero_when_absent).issubset(attributes)
        ):
            raise ValueError
        return _InvocationProfile(
            dict(attributes),
            frozenset(zero_when_absent),
        )

    @classmethod
    def _parse_services(cls, value: object) -> Mapping[str, _ServiceProfile]:
        if not isinstance(value, list) or not 1 <= len(value) <= 128:
            raise ValueError
        services: dict[str, _ServiceProfile] = {}
        logical_names: set[str] = set()
        for item in value:
            if (
                not isinstance(item, dict)
                or not _SERVICE_REQUIRED.issubset(item)
                or not set(item).issubset(_SERVICE_ALLOWED)
            ):
                raise ValueError
            otlp_name = item["otlpName"]
            service_name = item["serviceName"]
            namespace = item.get("serviceNamespace")
            environment = item.get("deploymentEnvironment")
            resource_refs = item["resourceRefs"]
            if (
                not isinstance(otlp_name, str)
                or not isinstance(service_name, str)
                or not _SAFE_VALUE.fullmatch(otlp_name)
                or not _SAFE_VALUE.fullmatch(service_name)
                or namespace is not None
                and (
                    not isinstance(namespace, str)
                    or not _SAFE_VALUE.fullmatch(namespace)
                )
                or environment is not None
                and (
                    not isinstance(environment, str)
                    or not _SAFE_VALUE.fullmatch(environment)
                )
                or not isinstance(resource_refs, list)
                or len(resource_refs) > 64
                or any(
                    not isinstance(uid, str) or not _RESOURCE_UID.fullmatch(uid)
                    for uid in resource_refs
                )
                or resource_refs != sorted(resource_refs)
                or len(set(resource_refs)) != len(resource_refs)
                or otlp_name in services
                or service_name in logical_names
            ):
                raise ValueError
            services[otlp_name] = _ServiceProfile(
                otlp_name,
                service_name,
                namespace,
                environment,
                tuple(resource_refs),
            )
            logical_names.add(service_name)
        return services

    @staticmethod
    def _parse_usage(value: object) -> _UsageProfile:
        if not isinstance(value, dict) or set(value) != {
            *_USAGE_FIELDS,
            "zeroWhenAbsent",
            "reportedBy",
        }:
            raise ValueError
        attributes = {field: value[field] for field in _USAGE_FIELDS}
        zero_when_absent = value["zeroWhenAbsent"]
        if (
            any(
                not isinstance(name, str)
                or not _ATTRIBUTE.fullmatch(name)
                or ConfiguredAiUsageReceiver._prohibited_name(name)
                for name in attributes.values()
            )
            or len(set(attributes.values())) != len(attributes)
            or not isinstance(zero_when_absent, list)
            or zero_when_absent != sorted(zero_when_absent)
            or len(set(zero_when_absent)) != len(zero_when_absent)
            or not set(zero_when_absent).issubset(_BREAKDOWN_FIELDS)
            or value["reportedBy"] not in ("provider", "instrumentation", "derived")
        ):
            raise ValueError
        return _UsageProfile(
            attributes,
            frozenset(zero_when_absent),
            value["reportedBy"],
        )

    @staticmethod
    def _parse_commercial(value: object) -> tuple[str, str, str]:
        if not isinstance(value, dict) or set(value) != {
            "serviceTier",
            "routingMode",
            "purchaseMode",
        }:
            raise ValueError
        service_tier = value["serviceTier"]
        routing_mode = value["routingMode"]
        purchase_mode = value["purchaseMode"]
        if (
            service_tier
            not in {"default", "standard", "flex", "priority", "reserved", "unknown"}
            or routing_mode
            not in {"in-region", "geographic", "global", "unknown"}
            or purchase_mode
            not in {"on-demand", "batch", "provisioned-throughput", "unknown"}
        ):
            raise ValueError
        return service_tier, routing_mode, purchase_mode

    @staticmethod
    def _parse_limits(value: object) -> AiUsageChannelLimits:
        names = {
            "maxRequestBytes",
            "maxSpans",
            "maxAttributesPerSpan",
            "maxAgeSeconds",
            "maxClockSkewSeconds",
            "maxProcessingSeconds",
        }
        if (
            not isinstance(value, dict)
            or set(value) != names
            or any(
                isinstance(value[name], bool) or not isinstance(value[name], int)
                for name in names
            )
        ):
            raise ValueError
        limits = AiUsageChannelLimits(
            max_request_bytes=value["maxRequestBytes"],
            max_spans=value["maxSpans"],
            max_attributes_per_span=value["maxAttributesPerSpan"],
            max_age_seconds=value["maxAgeSeconds"],
            max_clock_skew_seconds=value["maxClockSkewSeconds"],
            max_processing_seconds=value["maxProcessingSeconds"],
        )
        return limits

    @staticmethod
    def _string_list(
        value: object,
        maximum: int,
        pattern: re.Pattern[str] | None = None,
    ) -> tuple[str, ...]:
        if (
            not isinstance(value, list)
            or not 1 <= len(value) <= maximum
            or any(
                not isinstance(item, str)
                or not _SAFE_VALUE.fullmatch(item)
                or pattern is not None and not pattern.fullmatch(item)
                for item in value
            )
            or value != sorted(value)
            or len(set(value)) != len(value)
        ):
            raise ValueError
        return tuple(value)

    @classmethod
    def _matches_optional_resource_identity(
        cls,
        attributes: Mapping[str, AnyValue],
        service: _ServiceProfile,
    ) -> None:
        namespace = cls._optional_string(attributes, "service.namespace")
        environment = cls._optional_string(attributes, "deployment.environment.name")
        legacy_environment = cls._optional_string(attributes, "deployment.environment")
        if (
            environment is not None
            and legacy_environment is not None
            and environment != legacy_environment
        ):
            raise InvalidAiUsageRequestError("otlp.attribute.ambiguous")
        observed_environment = environment or legacy_environment
        if (
            namespace is not None
            and namespace != service.service_namespace
            or observed_environment is not None
            and observed_environment != service.deployment_environment
        ):
            raise InvalidAiUsageRequestError("otlp.service.not-allowlisted")

    @staticmethod
    def _decode_content(payload: bytes, encoding: str, limit: int) -> bytes:
        if encoding in ("", "identity"):
            decoded = payload
        elif encoding == "gzip":
            try:
                with gzip.GzipFile(fileobj=io.BytesIO(payload), mode="rb") as stream:
                    decoded = stream.read(limit + 1)
            except (EOFError, OSError):
                raise InvalidAiUsageRequestError("otlp.compression.invalid") from None
        else:
            raise InvalidAiUsageRequestError("otlp.compression.unsupported")
        if len(decoded) > limit:
            raise AiUsagePayloadTooLargeError("otlp.request.too-large")
        return decoded

    @staticmethod
    def _attribute_values(
        attributes: Iterable[KeyValue],
        limit: int,
    ) -> Mapping[str, AnyValue]:
        if len(attributes) > limit:  # type: ignore[arg-type]
            raise InvalidAiUsageRequestError("otlp.attribute.limit")
        result: dict[str, AnyValue] = {}
        for attribute in attributes:
            if (
                not isinstance(attribute, KeyValue)
                or not _ATTRIBUTE.fullmatch(attribute.key)
            ):
                raise InvalidAiUsageRequestError("otlp.attribute.invalid")
            if attribute.key in result:
                raise InvalidAiUsageRequestError("otlp.attribute.duplicate")
            result[attribute.key] = attribute.value
        return result

    @classmethod
    def _reject_prohibited_names(cls, attributes: Mapping[str, AnyValue]) -> None:
        if any(cls._prohibited_name(name) for name in attributes):
            raise InvalidAiUsageRequestError("otlp.span.content-prohibited")

    @staticmethod
    def _prohibited_name(name: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]", "", name.lower())
        components = {
            component
            for component in re.split(r"[^a-z0-9]+", name.lower())
            if component
        }
        return (
            bool(_SENSITIVE_NAME.search(normalized))
            or bool(components.intersection({"token", "bearer"}))
            or any(part in normalized for part in _CONTENT_NAME_PARTS)
        )

    @classmethod
    def _required_string(
        cls,
        attributes: Mapping[str, AnyValue],
        name: str,
        *,
        maximum: int = 256,
    ) -> str:
        result = cls._optional_string(attributes, name, maximum=maximum)
        if result is None:
            raise InvalidAiUsageRequestError("otlp.attribute.required")
        return result

    @classmethod
    def _optional_string(
        cls,
        attributes: Mapping[str, AnyValue],
        name: str,
        *,
        maximum: int = 256,
    ) -> str | None:
        value = attributes.get(name)
        if value is None:
            return None
        if value.WhichOneof("value") != "string_value":
            raise InvalidAiUsageRequestError("otlp.attribute.type.unsupported")
        result = value.string_value
        cls._safe_value(result, maximum=maximum)
        return result

    @classmethod
    def _unique_string(
        cls,
        layers: Iterable[Mapping[str, AnyValue]],
        name: str,
    ) -> str:
        values = {
            value
            for layer in layers
            if (value := cls._optional_string(layer, name)) is not None
        }
        if len(values) != 1:
            raise InvalidAiUsageRequestError("otlp.attribute.ambiguous")
        return next(iter(values))

    @staticmethod
    def _optional_integer(
        attributes: Mapping[str, AnyValue],
        name: str,
        *,
        maximum: int,
    ) -> int | None:
        value = attributes.get(name)
        if value is None:
            return None
        if value.WhichOneof("value") != "int_value":
            raise InvalidAiUsageRequestError("otlp.attribute.type.unsupported")
        result = value.int_value
        if not 0 <= result <= maximum:
            raise InvalidAiUsageRequestError("otlp.attribute.invalid")
        return result

    @staticmethod
    def _timing(start_nanos: int, end_nanos: int) -> tuple[str, int]:
        if start_nanos <= 0 or end_nanos < start_nanos:
            raise InvalidAiUsageRequestError("otlp.span.time.invalid")
        duration_nanos = end_nanos - start_nanos
        duration_millis = duration_nanos // 1_000_000
        if duration_millis > 86_400_000:
            raise InvalidAiUsageRequestError("otlp.span.time.invalid")
        try:
            seconds, nanos = divmod(start_nanos, 1_000_000_000)
            started = datetime.fromtimestamp(seconds, timezone.utc).replace(
                microsecond=nanos // 1000
            )
        except (OverflowError, OSError, ValueError):
            raise InvalidAiUsageRequestError("otlp.span.time.invalid") from None
        return started.isoformat().replace("+00:00", "Z"), duration_millis

    @staticmethod
    def _outcome(status: Status, error_type: str | None) -> tuple[str, str | None]:
        if status.code == Status.STATUS_CODE_ERROR:
            normalized = error_type or "unknown"
            if normalized.lower() in {"cancelled", "canceled", "cancellation"}:
                return "cancelled", None
            return "error", normalized
        if error_type is not None:
            raise InvalidAiUsageRequestError("otlp.span.status.invalid")
        if status.code not in (Status.STATUS_CODE_UNSET, Status.STATUS_CODE_OK):
            raise InvalidAiUsageRequestError("otlp.span.status.invalid")
        return "success", None

    @staticmethod
    def _safe_value(value: object, *, maximum: int = 256) -> None:
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= maximum
            or not _SAFE_VALUE.fullmatch(value)
        ):
            raise InvalidAiUsageRequestError("otlp.attribute.invalid")


__all__ = ["ConfiguredAiUsageReceiver"]
