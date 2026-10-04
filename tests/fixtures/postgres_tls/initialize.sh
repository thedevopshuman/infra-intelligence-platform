#!/bin/sh
set -eu

# The official image invokes initialization hooks as the postgres user. Copy
# the bind-mounted ephemeral identity into PGDATA so the private key has the
# ownership and mode required by PostgreSQL before the final server starts.
umask 077
cp /fixture/server.key "$PGDATA/server.key"
cp /fixture/server.crt "$PGDATA/server.crt"
cp /fixture/ca.crt "$PGDATA/ca.crt"
chmod 0600 "$PGDATA/server.key"
chmod 0644 "$PGDATA/server.crt" "$PGDATA/ca.crt"

# The entrypoint's temporary bootstrap server runs before this hook, so enable
# TLS in the persisted configuration for the final server rather than through
# container command arguments that would require the certificate too early.
{
    printf '%s\n' "ssl = on"
    printf '%s\n' "ssl_cert_file = 'server.crt'"
    printf '%s\n' "ssl_key_file = 'server.key'"
} >>"$PGDATA/postgresql.conf"

{
    printf '%s\n' 'local all all trust'
    printf '%s\n' 'hostssl all all 0.0.0.0/0 trust'
    printf '%s\n' 'hostssl all all ::/0 trust'
    printf '%s\n' 'hostnossl all all 0.0.0.0/0 reject'
    printf '%s\n' 'hostnossl all all ::/0 reject'
} >"$PGDATA/pg_hba.conf"
