#!/usr/bin/env bash
# Run DevWorkbench from source.
#
# The package lives in src/ (src layout) and the venv is not required to have
# an editable install, so PYTHONPATH must point at src for `-m devworkbench`.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}src"
exec .venv/bin/python -m devworkbench
