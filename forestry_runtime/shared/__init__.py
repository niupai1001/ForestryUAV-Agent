"""Code shared between the Runtime process and the Host Bridge process.

Both are separate processes with different dependency sets, so anything here must
stay dependency-free: no FastAPI, no sqlite3, no Docker, no pathlib side effects.
"""
