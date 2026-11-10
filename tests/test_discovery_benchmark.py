"""Retrieval screen evidence must bind inputs and keep misses visible."""
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path

import pytest

from forestry_agent.runtime.interfaces import ToolDescriptor


@pytest.fixture
def benchmark():
    path = Path(__file__).resolve().parents[1]/'research/discovery_benchmark.py'
    spec = importlib.util.spec_from_file_location('discovery_benchmark_test',path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inputs():
    rows = [('read_file','读取文本','file_tools'), ('code_execute','运行Python','job_tools'),
            ('read_user_memory','用户记忆','memory_tools')]
    catalog = {'descriptors':[asdict(ToolDescriptor(name,text,name,'fixture',domain,name)) for name,text,domain in rows],
               'schemas':{name:{'name':name,'description':text,'schema':{'type':'object'}} for name,text,_ in rows}}
    queries = {'cases':[{'id':'q1','query':'Python','category':'compute','required':['code_execute']},
                        {'id':'q2','query':'unmatched','category':'knowledge','required':['read_user_memory']}]}
    return json.dumps(catalog).encode(),json.dumps(queries).encode()


def test_screen_keeps_lexical_misses_and_cost_scope_explicit(benchmark):
    catalog,queries = inputs()
    result = benchmark.benchmark(catalog,queries)
    assert result['summary']['all_registered']['all_targets_found'] == 2
    assert result['summary']['bm25']['all_targets_found'] == 1
    assert result['embedding_status'] == 'not_run_no_verified_embedding_asset'
    assert 'embedding' not in result['summary']
    missed = result['rows'][1]['methods']['bm25']
    assert not missed['all_targets_found']
    assert missed['registered_schema_utf8_bytes'] < result['rows'][1]['methods']['all_registered']['registered_schema_utf8_bytes']


def test_embedding_assets_must_match_frozen_catalog_and_queries(benchmark):
    catalog,queries = inputs()
    vectors = {'catalog_sha256':benchmark.sha(catalog),'queries_sha256':benchmark.sha(queries),
        'model':{'repository':'unit-vectors-not-a-model','revision':'fixture','dimension':2},
        'document_vectors':{'code_execute':[1.,0.],'read_user_memory':[0.,1.]},
        'query_vectors':{'q1':[1.,0.],'q2':[0.,1.]}}
    result = benchmark.benchmark(catalog,queries,json.dumps(vectors).encode())
    assert result['embedding_identity'] == vectors['model'] and result['embedding_file_sha256']
    assert 'hybrid_rrf' in result['summary']
    vectors['queries_sha256'] = 'changed'
    with pytest.raises(ValueError,match='bind exact'):
        benchmark.benchmark(catalog,queries,json.dumps(vectors).encode())


@pytest.mark.parametrize('vector', [[0,0],[float('nan'),0],[1], [1e308,1e308]])
def test_invalid_vectors_are_not_silent_zero_similarity(benchmark,vector):
    with pytest.raises(ValueError):
        benchmark.vector_rank({'tool':[1,0]},vector,2)


def test_rrf_uses_both_rankings_without_query_labels(benchmark):
    assert benchmark.fused_rank(['a','b'],['b','c'])[0] == 'b'


def test_combined_report_reuses_saved_lexical_results_without_recomputing(benchmark,monkeypatch):
    catalog,queries = inputs()
    baseline = benchmark.benchmark(catalog,queries)
    vectors = {'catalog_sha256':benchmark.sha(catalog),'queries_sha256':benchmark.sha(queries),
        'model':{'repository':'unit-vectors-not-a-model','revision':'fixture','dimension':2},
        'document_vectors':{'code_execute':[1.,0.],'read_user_memory':[0.,1.]},
        'query_vectors':{'q1':[1.,0.],'q2':[0.,1.]}}
    def no_lexical_recalculation(*args,**kwargs):
        raise AssertionError('Saved lexical evidence must be reused')
    monkeypatch.setattr(benchmark,'search',no_lexical_recalculation)
    saved = json.dumps(baseline).encode()
    result = benchmark.compare_saved_baseline(saved,catalog,queries,json.dumps(vectors).encode())
    assert result['baseline_sha256'] == benchmark.sha(saved)
    assert result['summary']['bm25'] == baseline['summary']['bm25']
    assert result['summary']['embedding']['all_targets_found'] == 2
    assert result['settings']['lexical_candidates'] == 8
    vectors['catalog_sha256'] = 'changed'
    with pytest.raises(ValueError,match='must match'):
        benchmark.compare_saved_baseline(saved,catalog,queries,json.dumps(vectors).encode())
