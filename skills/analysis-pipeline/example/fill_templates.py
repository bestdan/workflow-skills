"""
Fill data placeholders in memo.template.md from model_output.json.
Replaces {{key}} with values from the JSON. Leaves {{narrative:*}} untouched.
Writes memo.filled.md.
"""

import json
import re
from pathlib import Path

here = Path(__file__).parent

data = json.loads((here / "model_output.json").read_text())
template = (here / "memo.template.md").read_text()


def resolve(key, d):
    """Walk dot-separated key path into nested dict."""
    for part in key.split("."):
        if not isinstance(d, dict) or part not in d:
            return None
        d = d[part]
    return d


def fill(template, data):
    def replace(match):
        key = match.group(1)
        if key.startswith("narrative:"):
            return match.group(0)  # leave narrative placeholders untouched
        value = resolve(key, data)
        if value is None:
            return match.group(0)  # leave unresolved placeholders as-is
        return str(value)

    return re.sub(r"\{\{([^}]+)\}\}", replace, template)


filled = fill(template, data)
out_path = here / "memo.filled.md"
out_path.write_text(filled)
print(f"Wrote {out_path}")
