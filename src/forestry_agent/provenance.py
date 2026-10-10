"""Startup code identity, shared by runtime diagnostics and evaluation."""
from __future__ import annotations

import hashlib
import importlib.metadata
from pathlib import Path

from .store import encode


def source_manifest(root: Path | None = None):
    root = root or Path(__file__).parent
    files = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(root.rglob('*')) if path.is_file() and path.suffix in
             {'.py', '.yaml', '.html', '.js', '.css'} and '__pycache__' not in path.parts}
    return {'files': files, 'sha256': hashlib.sha256(encode(files).encode()).hexdigest()}


def runtime_manifest(config):
    return {'schema_version': 1, 'source': source_manifest(),
            'config_sha256': hashlib.sha256(encode(config.model_dump(mode='json')).encode()).hexdigest(),
            'roles_sha256': hashlib.sha256((config.roles_path or Path(__file__).with_name('roles.yaml')).read_bytes()).hexdigest(),
            'packages': {distribution.metadata['Name']: distribution.version
                         for distribution in importlib.metadata.distributions()}}
