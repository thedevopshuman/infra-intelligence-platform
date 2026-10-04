"""Generate exact protected metadata admission before the persistent queue.

The serving receiver remains the authority for tenant, channel, economic and
timing validation. These additional Collector checks prevent inadmissible free
text from ever entering its durable buffer. The input is the reviewed receiver
configuration, not values learned from telemetry.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Mapping


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "deploy/community/collector.yaml"
MARKER = "        - 'true' # INSTALLER_ADMISSION_FILTERS"
SCOPE = "opentelemetry.instrumentation.botocore.bedrock-runtime"


def _literal(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 256
        or "${" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("community.collector.configuration.invalid")
    return json.dumps(value, ensure_ascii=True)


def _values(value: object) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 256:
        raise ValueError("community.collector.configuration.invalid")
    values = [_literal(item) for item in value]
    if len(set(values)) != len(values):
        raise ValueError("community.collector.configuration.invalid")
    return sorted(values)


def _outside(path: str, values: list[str], *, optional: bool = False) -> str:
    condition = " and ".join(f"{path} != {value}" for value in values)
    return f"{path} != nil and ({condition})" if optional else f"{path} == nil or ({condition})"


def admission_filters(channel_document: Mapping[str, object]) -> tuple[str, ...]:
    """Return closed OTTL drop conditions without importing server internals."""
    try:
        if set(channel_document) != {"channels"}:
            raise ValueError
        entries = channel_document["channels"]
        if not isinstance(entries, list) or len(entries) != 1:
            raise ValueError
        channel = entries[0]
        if not isinstance(channel, dict) or channel.get("provider") != "aws.bedrock" or channel.get("instrumentationScopes") != [SCOPE]:
            raise ValueError
        models = _values(channel["models"])
        operations = _values(channel["operations"])
        regions = _values(channel["regions"])
        services = channel["services"]
        if not isinstance(services, list) or not 1 <= len(services) <= 128:
            raise ValueError
        service_matches = []
        for service in services:
            if not isinstance(service, dict):
                raise ValueError
            terms = [f'resource.attributes["service.name"] == {_literal(service["otlpName"])}']
            for attribute, field in (("service.namespace", "serviceNamespace"), ("deployment.environment.name", "deploymentEnvironment")):
                path = f"resource.attributes[{json.dumps(attribute)}]"
                if service.get(field) is None:
                    terms.append(f"{path} == nil")
                else:
                    terms.append(f"({path} == nil or {path} == {_literal(service[field])})")
            service_matches.append("(" + " and ".join(terms) + ")")
        filters = [
            "not (" + " or ".join(service_matches) + ")",
            'attributes["gen_ai.provider.name"] == nil and attributes["gen_ai.system"] == nil',
            _outside('attributes["gen_ai.provider.name"]', ['"aws.bedrock"'], optional=True),
            _outside('attributes["gen_ai.system"]', ['"aws.bedrock"'], optional=True),
            _outside('attributes["gen_ai.operation.name"]', operations),
            _outside('attributes["gen_ai.request.model"]', models),
            _outside('attributes["gen_ai.response.model"]', models, optional=True),
        ]
        region_paths = [f'{layer}attributes["cloud.region"]' for layer in ("", "resource.", "instrumentation_scope.")]
        filters.append(" and ".join(f"{path} == nil" for path in region_paths))
        for path in region_paths:
            filters.append(_outside(path, regions, optional=True))
        for index, left in enumerate(region_paths):
            for right in region_paths[index + 1:]:
                filters.append(f"{left} != nil and {right} != nil and {left} != {right}")
        for attribute in (
            "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens",
            "gen_ai.usage.cache_read.input_tokens", "gen_ai.usage.cache_creation.input_tokens",
            "gen_ai.usage.reasoning.output_tokens", "aws.retry_count",
        ):
            path = f"attributes[{json.dumps(attribute)}]"
            maximum = 100 if attribute == "aws.retry_count" else 9_007_199_254_740_991
            filters.append(f"{path} != nil and ({path} < 0 or {path} > {maximum})")
        # A provider request identifier is pseudonymized by the transform before
        # queueing. The receiver subsequently hashes that digest again, so this
        # path preserves local equality but not a direct-intake hash identity.
        filters.append('attributes["aws.request_id"] != nil and (Len(attributes["aws.request_id"]) == 0 or Len(attributes["aws.request_id"]) > 256)')
        return tuple(filters)
    except (KeyError, TypeError, ValueError):
        raise ValueError("community.collector.configuration.invalid") from None


def render_collector(channel_document: Mapping[str, object]) -> str:
    """Return deterministic YAML with no value interpolation or code authority."""
    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count(MARKER) != 1:
        raise ValueError("community.collector.template.invalid")
    generated = "\n".join("        - " + json.dumps(item, ensure_ascii=True) for item in admission_filters(channel_document))
    return template.replace(MARKER, generated)


def write_collector(directory: Path, channel_document: Mapping[str, object]) -> Path:
    """Write a new protected generation; existing files are never replaced."""
    info = directory.lstat()
    if not directory.is_absolute() or not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.getuid():
        raise ValueError("community.collector.directory.invalid")
    content = render_collector(channel_document)
    destination = directory / "collector.yaml"
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)
    return destination
