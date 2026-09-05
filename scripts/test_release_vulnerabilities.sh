#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${IIP_TEST_PYTHON:-python3}"

cd "$repo_root"
IIP_TEST_TRIVY=1 PYTHONPATH="src:sdks/python/src" \
  "$python_bin" -m unittest tests.test_release_vulnerability_qualification -v
