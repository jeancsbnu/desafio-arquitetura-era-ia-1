#!/bin/sh
# Sobe o LiteLLM e, em paralelo, aplica as chaves virtuais de keys.yaml (bootstrap_keys.py).
rm -f /tmp/keys-ready
python /gateway/bootstrap_keys.py &
exec litellm --config /gateway/config.yaml --port 4000
