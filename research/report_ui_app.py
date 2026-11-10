"""Serve the completed isolated report task without enabling model dispatch."""
import json
from pathlib import Path

from forestry_agent.app import create_app
from forestry_agent.config import Config


def make_app():
    root = Path(__file__).resolve().parents[1]
    trial = root / 'workspace/report-acceptance-20261026/static'
    report = json.loads((trial / 'report.json').read_text(encoding='utf-8'))
    assert report['task_id'] == 'efcbe703-3234-459e-a720-caea008eff03'
    assert report['state']['status'] == 'waiting_user'
    config = Config.model_validate(json.loads((trial / 'config.json').read_text(encoding='utf-8')))
    assert config.workspace_root.resolve() == (trial / 'runtime').resolve()
    config.port = 8796
    return create_app(config, enable_agent=False)


def make_jobs_app():
    root = Path(__file__).resolve().parents[1]
    trial = root / 'workspace/executor-seam-20261026'
    report = json.loads((trial / 'report.json').read_text(encoding='utf-8'))
    assert report['completed'] and report['task_id'] == 'ec6f8c6d-12ab-4205-9298-899c2da53f06'
    config = Config.model_validate(json.loads((trial / 'config.json').read_text(encoding='utf-8')))
    assert config.workspace_root.resolve() == (trial / 'runtime').resolve()
    config.port = 8797
    return create_app(config, enable_agent=False)
