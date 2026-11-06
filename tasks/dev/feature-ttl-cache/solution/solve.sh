#!/bin/bash
set -euo pipefail
case "${SOLUTION:-1}" in
  1|2) cp "/solution/answer-${SOLUTION:-1}.py" "/app/cache.py" ;;
  *) echo "Unknown solution: $SOLUTION" >&2; exit 2 ;;
esac
