"""Minimal dependency-free example for calling the local Qwen model."""

import json
import sys
from urllib.request import Request, urlopen


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


payload = {
    "model": "qwen3.5:4b",
    "messages": [
        {
            "role": "user",
            "content": "列出无人机林业遥感工作流中的三个关键步骤。",
        }
    ],
    "stream": False,
    "think": False,
    "options": {"num_ctx": 32768, "temperature": 0.2},
}

request = Request(
    "http://127.0.0.1:11434/api/chat",
    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    headers={"Content-Type": "application/json; charset=utf-8"},
    method="POST",
)

with urlopen(request, timeout=120) as response:
    result = json.load(response)

print(result["message"]["content"])
