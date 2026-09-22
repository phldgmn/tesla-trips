#!/usr/bin/env bash
# Fails when a module `x.py` sits next to a package `x/`: Python always
# imports the package, so the `.py` file is silently dead code.
set -euo pipefail
status=0
while IFS= read -r f; do
  if [[ -d "${f%.py}" ]]; then
    echo "shadowed: $f"
    status=1
  fi
done < <(find src -name '*.py')
exit "$status"
