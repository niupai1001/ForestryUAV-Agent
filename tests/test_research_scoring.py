"""Saved automatic checks cannot prove report semantics or scientific validity."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def research(monkeypatch,tmp_path):
    path = Path(__file__).resolve().parents[1]/'scripts/research_run.py'
    spec = importlib.util.spec_from_file_location('screening_under_test',path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module,'RECORDS',tmp_path/'records')
    return module


def sample():
    check = {'type':'status_in','values':['waiting_user']}
    spec = {'id':'fixture','title':'Fixture','suite_version':'fixture','checks':[check], 'manual':['scientific claims supported']}
    record = {'task':'fixture','status':'waiting_user','blockers':[],
        'verdict':'completed','checks':[{'check':check,'passed':True}],
        'jobs':[{'status':'succeeded'}]}
    return spec,record


def test_automatic_pass_and_successful_job_are_not_semantic_or_scientific_pass(research):
    spec,record = sample()
    score = research.screening_score(spec,record)
    assert score['automatic_checks_passed'] and score['job_execution'] == 'terminal_succeeded'
    assert not score['full_task_passed'] and score['report_semantics'] == 'not_reviewed'
    assert score['scientific_evidence']['field_accuracy'] == 'not_assessed'
    assert record['verdict'] == 'completed'  # Frozen original is not rewritten.
    record['checks'] = []
    assert not research.screening_score(spec,record)['automatic_checks_passed']


def test_blockers_preserve_layers_without_guessing_executor_root_cause(research):
    spec,record = sample()
    record.update(status='paused', jobs=[],blockers=[{'code':'MODEL_PROTOCOL_ERROR'}])
    score = research.screening_score(spec,record)
    assert score['job_execution'] == 'no_jobs'
    assert score['observed_failure_layers'][0]['layer'] == 'runtime_protocol_or_persistence_unresolved'
    assert not score['full_task_passed']


def test_record_writes_are_exclusive_and_rescoring_cannot_overwrite_original(research,tmp_path):
    _,record = sample()
    research.save_record(record)
    original = research.record_path(record['task'])
    raw = original.read_bytes()
    with pytest.raises(FileExistsError):
        research.save_record(record)
    with pytest.raises(research.Failure):
        research.save_record(record,original)
    output = tmp_path/'new-observation.json'
    research.save_record(record,output)
    assert original.read_bytes() == raw and json.loads(output.read_text()) == record


def test_existing_trial_run_refused_before_connecting_service(research,monkeypatch):
    monkeypatch.setattr(research,'load_record',lambda _: {'task_id':'existing'})
    monkeypatch.setattr(research.sys,'argv',['research','run','--task','05'])
    def no_client(*args,**kwargs):
        raise AssertionError('Must refuse before any API contact')
    monkeypatch.setattr(research,'LocalClient',no_client)
    with pytest.raises(SystemExit,match='原试次记录已存在'):
        research.main()


def test_read_only_reobservation_preserves_prior_wall_time_and_semantic_gate(research,tmp_path,monkeypatch):
    spec,record = sample()
    spec['frozen'] = {'budget':{}}
    state = {'task_id':'task','status':'waiting_user','blockers':[],'messages':[]}
    class Client:
        def __init__(self,*args):
            pass
        def request(self,method,path):
            return [] if path.endswith('/artifacts') else {'jobs':[]} if path.endswith('/jobs') else state
        def close(self):
            pass
        http = None
    monkeypatch.setattr(research,'LocalClient',Client)
    monkeypatch.setattr(research,'load_config',lambda _: SimpleNamespace(workspace_root=tmp_path))
    monkeypatch.setattr(research,'collect_events',lambda *args: [])
    monkeypatch.setattr(research,'run_checks',lambda *args:record['checks'])
    result = research.build_record(spec,'task','large','H2',{},
        {'wall_seconds':159.0,'stop_reason':None},'frozen-start', output=tmp_path/'observed.json')
    assert result['metrics']['wall_seconds'] == 159.0
    assert result['verdict'] == 'partial' and not result['score']['full_task_passed']


def test_table_keeps_missing_wall_unknown_and_does_not_label_auto_checks_success(monkeypatch,capsys):
    path = Path(__file__).resolve().parents[1]/'scripts/research_table.py'
    spec = importlib.util.spec_from_file_location('screening_table_under_test',path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    task_spec,record = sample()
    record['metrics'] = {'wall_seconds':None,'requests':1,'tool_actions':0}
    monkeypatch.setattr(module,'load_suite',lambda:[task_spec])
    monkeypatch.setattr(module,'load_record',lambda _:record)
    assert module.main() == 0
    output = capsys.readouterr().out
    assert '未知' in output and '自动检查通过/语义未审' in output
    assert '完整契约通过 0' in output and '成功' not in output
