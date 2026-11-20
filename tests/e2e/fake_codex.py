#!/usr/bin/env python3
"""Stands in for `codex exec`: answers the dashboard's two prompts with fixed JSON.

Clustering groups the image-proxy and search-indexer incidents under one title;
pattern suggestions return a literal match for the first sample line.
"""

import json
import re
import sys

args = sys.argv[1:]
output_path = args[args.index("--output-last-message") + 1]
prompt = sys.stdin.read()

if "Incidents:" in prompt:
    blocks = re.findall(r"fingerprint: (\S+)\nservice: (\S+)", prompt)
    storage = [fp for fp, service in blocks if service in ("image-proxy", "search-indexer")]
    answer = {"groups": [{
        "title": "Media pipeline storage pressure",
        "summary": "Image proxy and search indexer both fail when shared volumes fill up.",
        "fingerprints": storage,
    }]}
else:
    examples = prompt.split("Example log lines:", 1)[-1]
    sample = re.search(r"^- (.+)$", examples, re.MULTILINE)
    literal = re.escape(sample.group(1)) if sample else "unknown"
    answer = {"pattern": literal, "explanation": "Matches this exact message only."}

with open(output_path, "w", encoding="utf-8") as handle:
    json.dump(answer, handle)
