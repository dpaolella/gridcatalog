#!/bin/sh
# Start the catalog's API and MCP endpoint.
#
# Exists for one reason: `Settings` refuses to start when `environment` is
# anything but development and `secret_key` is still the published default, and
# this image sets `DATAHUB_ENVIRONMENT=production`. So the image built cleanly
# and then died on its first line — a container that Fly would have restarted
# forever, and `.github/workflows/image.yml` caught on its first run.
#
# That guard is right and is not being weakened here. It exists so that nobody
# runs in production on the key printed in the repository, because a keyed hash
# whose key is public defends nothing. A key generated here is neither
# published nor guessable, so it satisfies the guard rather than evading it.
#
# **Why generating one is safe for this image, specifically.** `secret_key` is
# the HMAC key over API tokens and submitter IPs. This deployment is anonymous
# — it issues no tokens — and its operational database is *baked into the image*
# and replaced on every deploy, so there is nothing whose hash has to stay
# stable between restarts. A deployment that does issue tokens, or that mounts
# a volume, must set the key itself; the line below says so where an operator
# will see it, because the consequence of not reading it is tokens that stop
# resolving after a restart with nothing to explain why.
set -e

if [ -z "${DATAHUB_SECRET_KEY:-}" ]; then
  DATAHUB_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
  export DATAHUB_SECRET_KEY
  echo "datahub: no DATAHUB_SECRET_KEY set; generated one for this container." >&2
  echo "datahub: fine while this deployment issues no tokens and keeps no volume." >&2
  echo "datahub: set DATAHUB_SECRET_KEY if it ever does — otherwise a restart" >&2
  echo "datahub: invalidates every issued token." >&2
fi

exec uvicorn datahub.mcp.asgi:app --host 0.0.0.0 --port "${PORT}"
