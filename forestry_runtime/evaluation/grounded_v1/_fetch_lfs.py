"""Fetch Git LFS payloads for the two retained GABench tasks (ID 9, ID 12).

The LFS pointer files are already in the pinned clone; this resolves each pointer
through the LFS batch API and writes the real payload only when its SHA-256
matches the oid recorded in the pointer. Nothing is written on a mismatch.
"""

import hashlib
import json
import pathlib
import sys
import urllib.error
import urllib.request

PROXY = "https://gh-proxy.com/"
BATCH = "https://github.com/GeoX-Lab/GABench.git/info/lfs/objects/batch"

NO_PROXY = urllib.request.ProxyHandler({})
DIRECT = urllib.request.build_opener(NO_PROXY)


def parse_pointer(path: pathlib.Path) -> tuple[str, int]:
    text = path.read_text(encoding="utf-8")
    if "git-lfs" not in text:
        raise ValueError(f"{path} is not an LFS pointer")
    oid = size = None
    for line in text.splitlines():
        if line.startswith("oid sha256:"):
            oid = line.split(":", 1)[1].strip()
        elif line.startswith("size "):
            size = int(line.split(" ", 1)[1].strip())
    if not oid or not size:
        raise ValueError(f"{path} is missing oid or size")
    return oid, size


def request_href(oid: str, size: int) -> str:
    body = json.dumps(
        {"operation": "download", "transfers": ["basic"], "objects": [{"oid": oid, "size": size}]}
    ).encode()
    request = urllib.request.Request(
        PROXY + BATCH,
        data=body,
        headers={
            "Content-Type": "application/vnd.git-lfs+json",
            "Accept": "application/vnd.git-lfs+json",
        },
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = json.loads(response.read())
    obj = payload["objects"][0]
    if "actions" not in obj or "download" not in obj.get("actions", {}):
        raise ValueError(f"no download action for {oid}: {obj.get('error')}")
    return obj["actions"]["download"]["href"]


def download(href: str) -> bytes:
    last_error = None
    for candidate in (href, PROXY + href):
        try:
            with DIRECT.open(candidate, timeout=900) as response:
                return response.read()
        except urllib.error.URLError as error:
            last_error = error
    raise RuntimeError(f"download failed: {last_error}")


def main(paths: list[str]) -> int:
    root = pathlib.Path("data/gabench/repo")
    for relative in paths:
        target = root / relative
        oid, size = parse_pointer(target)
        print(f"{relative}: oid={oid[:12]}… size={size}", flush=True)
        href = request_href(oid, size)
        payload = download(href)
        digest = hashlib.sha256(payload).hexdigest()
        if digest != oid:
            print(f"  MISMATCH: expected {oid}, got {digest}; file left untouched", flush=True)
            return 1
        if len(payload) != size:
            print(f"  SIZE MISMATCH: expected {size}, got {len(payload)}", flush=True)
            return 1
        target.write_bytes(payload)
        print(f"  ok: {len(payload)} bytes written", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
