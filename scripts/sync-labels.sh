#!/usr/bin/env bash
# Apply .github/labels.yml to the repository. Idempotent: creates what is
# missing, updates colour and description on what exists, and leaves everything
# else alone. It deliberately does NOT delete labels it does not know about.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 - <<'PY' | while IFS=$'\t' read -r name color desc; do
import re, pathlib
text = pathlib.Path(".github/labels.yml").read_text()
blocks = re.findall(
    r'- name: (.+?)\n\s+color: "(.+?)"\n\s+description: (.+?)\n', text + "\n"
)
for name, color, desc in blocks:
    print(f"{name.strip()}\t{color.strip()}\t{desc.strip()}")
PY
  if gh label edit "$name" --color "$color" --description "$desc" >/dev/null 2>&1; then
    echo "  updated  $name"
  else
    gh label create "$name" --color "$color" --description "$desc" >/dev/null
    echo "  created  $name"
  fi
done
