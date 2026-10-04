"""Fail-closed PostgreSQL transport configuration for packaged runtimes."""

from __future__ import annotations

import os
import re
import ssl
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Mapping

from psycopg.conninfo import conninfo_to_dict, make_conninfo


DATABASE_TRANSPORT_CONFIGURATION_ERROR: Final = (
    "database.transport.configuration.invalid"
)
DATABASE_TRANSPORT_MODES: Final = frozenset({"verify-full", "insecure-local"})

_TRANSPORT_CONTROL_PREFIXES: Final = ("ssl",)
_TRANSPORT_CONTROL_KEYS: Final = frozenset(
    {
        "channelbinding",
        "gssencmode",
        "hostaddr",
        "requirepeer",
        "requiressl",
        "service",
        "servicefile",
    }
)
_NORMALIZED_CONNINFO_KEY = re.compile(r"[^0-9a-z]+")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\\\/]|^\\\\")
_TRANSPORT_CONTROL_ENV_VARS: Final = (
    "PGCHANNELBINDING",
    "PGGSSENCMODE",
    "PGHOSTADDR",
    "PGREQUIREPEER",
    "PGREQUIRESSL",
    "PGSERVICE",
    "PGSERVICEFILE",
    "PGSSLCERT",
    "PGSSLCERTMODE",
    "PGSSLCOMPRESSION",
    "PGSSLCRL",
    "PGSSLCRLDIR",
    "PGSSLKEY",
    "PGSSLKEYLOGFILE",
    "PGSSLMAXPROTOCOLVERSION",
    "PGSSLMINPROTOCOLVERSION",
    "PGSSLMODE",
    "PGSSLNEGOTIATION",
    "PGSSLROOTCERT",
    "PGSSLSNI",
)


class PostgresConnectionConfigurationError(RuntimeError):
    """Reject unsafe database transport configuration without echoing inputs."""


def _normalized_conninfo_key(raw_name: object) -> str:
    if not isinstance(raw_name, str):
        raise _invalid_configuration()
    return _NORMALIZED_CONNINFO_KEY.sub("", raw_name.lower())


def _is_transport_control_parameter(raw_name: str) -> bool:
    normalized_name = _normalized_conninfo_key(raw_name)
    if normalized_name in _TRANSPORT_CONTROL_KEYS:
        return True
    if normalized_name.startswith(_TRANSPORT_CONTROL_PREFIXES):
        return True
    return False


def _reject_transport_controls(
    parameters: Mapping[str, object],
    transport_mode: str,
    transport_ca_path: str | None,
) -> None:
    for name, value in parameters.items():
        if not _is_transport_control_parameter(name):
            continue
        normalized_name = _normalized_conninfo_key(name)
        if normalized_name == "sslmode":
            if transport_mode == "verify-full" and value == "verify-full":
                continue
            raise _invalid_configuration()
        if normalized_name == "gssencmode":
            if transport_mode == "verify-full" and value == "disable":
                continue
            raise _invalid_configuration()
        if normalized_name == "sslrootcert":
            if transport_mode == "verify-full" and _is_matching_ca(value, transport_ca_path):
                continue
            raise _invalid_configuration()
        raise _invalid_configuration()


def _is_matching_ca(value: object, transport_ca_path: str | None) -> bool:
    if not isinstance(value, str) or not isinstance(transport_ca_path, str):
        return False
    try:
        return str(Path(value).absolute()) == transport_ca_path
    except (OSError, RuntimeError, ValueError):
        return False


@dataclass(frozen=True, slots=True)
class PostgresConnectionConfiguration:
    """Immutable, validated libpq connection policy and effective conninfo."""

    transport_mode: str
    ca_path: str | None = field(repr=False)
    _connection_string: str = field(repr=False)

    def __post_init__(self) -> None:
        try:
            parameters = conninfo_to_dict(self._connection_string)
        except Exception:
            raise _invalid_configuration() from None

        if self.transport_mode not in DATABASE_TRANSPORT_MODES:
            raise _invalid_configuration()
        if self.transport_mode == "verify-full":
            _reject_transport_controls(parameters, self.transport_mode, self.ca_path)
            if parameters.get("sslmode") != "verify-full":
                raise _invalid_configuration()
            if parameters.get("gssencmode") != "disable":
                raise _invalid_configuration()
            if not isinstance(self.ca_path, str):
                raise _invalid_configuration()
            if parameters.get("sslrootcert") != self.ca_path:
                raise _invalid_configuration()
            _require_hostname_targets(parameters)
            _validate_ca_file(self.ca_path)
            return
        if self.transport_mode == "insecure-local":
            if self.ca_path is not None:
                raise _invalid_configuration()
            if parameters.get("sslmode") != "disable":
                raise _invalid_configuration()
            if parameters.get("gssencmode") != "disable":
                raise _invalid_configuration()
            for name, value in parameters.items():
                if not _is_transport_control_parameter(name):
                    continue
                normalized_name = _normalized_conninfo_key(name)
                if normalized_name in {"sslmode", "gssencmode"}:
                    continue
                raise _invalid_configuration()
            return

    @property
    def connection_string(self) -> str:
        """Return validated conninfo for Psycopg; callers must never log it."""

        return self._connection_string

    @classmethod
    def from_environment(
        cls,
        database_url: str,
        environment: Mapping[str, str] | None = None,
    ) -> PostgresConnectionConfiguration:
        """Build the one permitted database transport policy from process input."""

        source = os.environ if environment is None else environment
        _reject_ambient_transport_controls(source)
        if not isinstance(database_url, str) or not database_url.strip():
            raise _invalid_configuration()
        mode = source.get("IIP_DATABASE_TRANSPORT_MODE", "verify-full")
        if mode not in DATABASE_TRANSPORT_MODES:
            raise _invalid_configuration()
        try:
            parameters = conninfo_to_dict(database_url)
        except Exception:
            raise _invalid_configuration() from None

        if mode == "verify-full":
            ca_path = _normalized_ca_path(source.get("IIP_DATABASE_CA_PATH"))
            if any(_is_transport_control_parameter(name) for name in parameters):
                raise _invalid_configuration()
            _require_hostname_targets(parameters)
            try:
                connection_string = make_conninfo(
                    "",
                    **parameters,
                    gssencmode="disable",
                    sslmode="verify-full",
                    sslrootcert=ca_path,
                )
            except Exception:
                raise _invalid_configuration() from None
            return cls(
                transport_mode=mode,
                ca_path=ca_path,
                _connection_string=connection_string,
            )

        sanitized = {
            name: value
            for name, value in parameters.items()
            if not _is_transport_control_parameter(name)
        }
        try:
            connection_string = make_conninfo(
                "",
                **sanitized,
                gssencmode="disable",
                sslmode="disable",
            )
        except Exception:
            raise _invalid_configuration() from None
        return cls(
            transport_mode=mode,
            ca_path=None,
            _connection_string=connection_string,
        )


def connection_string_for(
    database: str | PostgresConnectionConfiguration,
) -> str:
    """Keep direct adapter construction compatible while honoring configurations."""

    if isinstance(database, PostgresConnectionConfiguration):
        return database.connection_string
    if not isinstance(database, str) or not database.strip():
        raise ValueError("database_url must be a non-empty PostgreSQL connection string")
    return database


def validate_connection_environment(
    database: str | PostgresConnectionConfiguration,
) -> None:
    """Recheck packaged policy without mutating process-wide libpq settings.

    Direct adapter callers retain responsibility for their explicit conninfo.
    Packaged clients must not inherit service files, alternate addresses, TLS
    keys, or other ambient transport policy. Checking at each connection also
    catches accidental process-environment changes after composition.
    """

    if isinstance(database, PostgresConnectionConfiguration):
        _reject_ambient_transport_controls(os.environ)


def _reject_ambient_transport_controls(environment: Mapping[str, str]) -> None:
    if any(
        name in _TRANSPORT_CONTROL_ENV_VARS or name.startswith("PGSSL")
        for name in environment
    ):
        raise _invalid_configuration()


def _invalid_configuration() -> PostgresConnectionConfigurationError:
    return PostgresConnectionConfigurationError(
        DATABASE_TRANSPORT_CONFIGURATION_ERROR
    )


def _is_unix_socket_target(target: str) -> bool:
    return (
        target.startswith(("/", "\\"))
        or target.startswith("@")
        or _WINDOWS_ABSOLUTE_PATH.match(target) is not None
    )


def _is_suspicious_host_name(target: str) -> bool:
    if _is_unix_socket_target(target):
        return True
    if "/" in target or "\\" in target:
        return True
    if target == "." or target == "..":
        return True
    return False


def _require_hostname_targets(parameters: Mapping[str, str]) -> None:
    host = parameters.get("host")
    if not isinstance(host, str) or not host:
        raise _invalid_configuration()
    targets = host.split(",")
    if not targets:
        raise _invalid_configuration()
    for raw_target in targets:
        target = raw_target.strip()
        if not target:
            raise _invalid_configuration()
        if _is_suspicious_host_name(target):
            raise _invalid_configuration()
        if raw_target != target:
            raise _invalid_configuration()
        if len(target) > 253:
            raise _invalid_configuration()


def _normalized_ca_path(raw_path: str | None) -> str:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise _invalid_configuration()
    try:
        path = Path(raw_path)
        normalized = str(path.absolute())
    except (OSError, RuntimeError, ValueError):
        raise _invalid_configuration() from None
    _validate_ca_file(normalized)
    return normalized


def _validate_ca_file(path_value: str) -> None:
    try:
        path = Path(path_value)
        if not path.is_file() or not os.access(path, os.R_OK):
            raise _invalid_configuration()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cafile=path_value)
    except PostgresConnectionConfigurationError:
        raise
    except (OSError, ssl.SSLError, ValueError):
        raise _invalid_configuration() from None


__all__ = [
    "DATABASE_TRANSPORT_CONFIGURATION_ERROR",
    "DATABASE_TRANSPORT_MODES",
    "PostgresConnectionConfiguration",
    "PostgresConnectionConfigurationError",
]
