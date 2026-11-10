"""Frozen registered-tool retrieval screen; no task/model/science execution.

Category baseline uses an annotated category (an upper bound, not an autonomous
classifier). Schema bytes exclude native/search/system overhead, never tokens.
Optional vectors must bind exactly to both frozen inputs and a model revision.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

from forestry_agent.runtime.discovery import ESSENTIAL, search  # noqa: E402
from forestry_agent.runtime.interfaces import ToolDescriptor  # noqa: E402
from forestry_agent.store import encode  # noqa: E402

CATEGORIES = {'compute':{'job_tools','environment_tools'}, 'environment':{'environment_tools','job_tools'},
              'files':{'file_tools','workspace_tools','document_tools'},
              'knowledge':{'document_tools','memory_tools'}}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def freeze_catalog(workspace):
    from pydantic_ai.tools import Tool
    from forestry_agent.app import create_app
    from forestry_agent.config import Config, ModelProfile
    from forestry_agent.runtime.tools import application_tools

    if workspace.exists() or not workspace.resolve().is_relative_to((ROOT/'workspace').resolve()):
        raise ValueError('Use a new isolated catalog workspace under this project/workspace')
    config = Config(workspace_root=workspace,search_enabled=False,
        models={'small':ModelProfile(repository='catalog-fixture',served_name='catalog-fixture')},
        science_image='sha256:'+'1'*64,imagery_image='sha256:'+'2'*64,orthomosaic_image='sha256:'+'3'*64)
    engine = create_app(config,enable_agent=False).state.engine
    registry = application_tools(engine,lambda *args:None)
    definitions = [Tool(spec.func).tool_def for spec in registry.specs() if spec.name != 'apply_lidar_roi']
    return {'schema_version':1,'scope':'all registered private capabilities, web/experimental ROI excluded',
        'limitations':'Descriptor-only fake image identities; no worker/model or task starts. Native/search tools excluded.',
        'descriptors':[asdict(d) for d in registry.descriptors(definitions)],
        'schemas':{t.name:{'name':t.name,'description':t.description,'schema':t.parameters_json_schema} for t in definitions}}


def vector_rank(vectors,query,dimension):
    def normalized(vector):
        if len(vector) != dimension or not all(isinstance(v,(float,int)) and math.isfinite(v) for v in vector):
            raise ValueError('Invalid or inconsistent embedding vector')
        norm = math.sqrt(sum(v*v for v in vector))
        if norm == 0 or not math.isfinite(norm):
            raise ValueError('Zero embedding vector')
        return [v/norm for v in vector]
    q = normalized(query)
    scored = [(name,sum(a*b for a,b in zip(normalized(v),q,strict=True))) for name,v in vectors.items()]
    return [name for name,_ in sorted(scored,key=lambda pair:(-pair[1],pair[0]))]


def fused_rank(lexical,dense):
    scores = {}
    for ranking in (lexical,dense):
        for rank,name in enumerate(ranking[:16],1):
            scores[name] = scores.get(name,0) + 1/(60+rank)
    return sorted(scores,key=lambda name:(-scores[name],name))


def benchmark(catalog_bytes,query_bytes,embedding_bytes=None):
    catalog,queries = json.loads(catalog_bytes),json.loads(query_bytes)
    descriptors = [ToolDescriptor(**d) for d in catalog['descriptors']]
    by_name = {d.name:d for d in descriptors}
    if set(by_name) != set(catalog['schemas']):
        raise ValueError('Descriptor/schema catalog mismatch')
    cases = queries['cases']
    if (not cases or len({c['id'] for c in cases}) != len(cases)
        or any(not c['required'] or len(set(c['required'])) != len(c['required']) for c in cases)):
        raise ValueError('Queries require unique IDs and annotated preferred tools')
    if any(not set(c['required']) <= set(by_name) or c['category'] not in CATEGORIES for c in cases):
        raise ValueError('Query annotation references unavailable tools/category')
    core = set(by_name) & ESSENTIAL
    corpus = [d for d in descriptors if d.name not in core]
    embeddings = json.loads(embedding_bytes) if embedding_bytes else None
    if embeddings is not None:
        if (embeddings.get('catalog_sha256') != sha(catalog_bytes)
            or embeddings.get('queries_sha256') != sha(query_bytes)
            or not embeddings.get('model',{}).get('repository') or not embeddings['model'].get('revision')
            or set(embeddings['document_vectors']) != {d.name for d in corpus}
            or set(embeddings['query_vectors']) != {c['id'] for c in cases}
            or not isinstance(embeddings['model'].get('dimension'),int) or embeddings['model']['dimension'] < 1):
            raise ValueError('Embeddings must bind exact corpus, queries and model revision/dimension')
    rows = []
    for case in cases:
        lexical = [item['name'] for item in search(corpus,case['query'],16)]
        methods = {'all_registered':list(by_name),
            'category_upper_bound':[d.name for d in descriptors if d.domain in CATEGORIES[case['category']]],
            'bm25':lexical[:8]}
        if embeddings is not None:
            dense = vector_rank(embeddings['document_vectors'],embeddings['query_vectors'][case['id']],
                embeddings['model']['dimension'])
            methods.update(embedding=dense[:8],hybrid_rrf=fused_rank(lexical,dense)[:8])
        results = {}
        for method,names in methods.items():
            selected = set(names) | core
            found = set(case['required']) & selected
            results[method] = {'ranked':names,'target_recall':len(found)/len(case['required']),
                'all_targets_found':found == set(case['required']),
                'unlabelled_candidates':len(set(names)-set(case['required'])-core),
                'registered_schema_utf8_bytes':len(encode([catalog['schemas'][n] for n in by_name if n in selected]).encode())}
        rows.append({'id':case['id'],'query':case['query'],'required':case['required'],'methods':results})
    summary = {}
    for method in rows[0]['methods']:
        scores = [row['methods'][method] for row in rows]
        summary[method] = {'all_targets_found':sum(s['all_targets_found'] for s in scores),
            'cases':len(scores),'mean_target_recall':sum(s['target_recall'] for s in scores)/len(scores),
            'mean_registered_schema_utf8_bytes':sum(s['registered_schema_utf8_bytes'] for s in scores)/len(scores)}
    return {'catalog_sha256':sha(catalog_bytes),'queries_sha256':sha(query_bytes),
        'retriever_source_sha256':sha((ROOT/'src/forestry_agent/runtime/discovery.py').read_bytes()),
        'benchmark_source_sha256':sha(Path(__file__).read_bytes()),'rows':rows,'summary':summary,
        'embedding_status':'evaluated' if embeddings else 'not_run_no_verified_embedding_asset',
        'embedding_identity':embeddings['model'] if embeddings else None,
        'embedding_file_sha256':sha(embedding_bytes) if embedding_bytes else None,
        'settings':{'top_k':8,'lexical_candidates':16,'dense_candidates':16,'rrf_constant':60},
        'limitations':['Category labels are an annotated upper bound, not model routing performance.',
            'Unlabelled candidates are not automatically wrong tool calls or authorization breaches.',
            'Schema bytes exclude native/search/system overhead; they are not tokens or provider usage.',
            'Development screen only; no autonomous model, statistical or scientific accuracy conclusion.']}


def compare_saved_baseline(baseline_bytes,catalog_bytes,query_bytes,embedding_bytes):
    """Reuse saved lexical/classification results; only evaluate new vector ranks."""
    baseline,catalog,queries,embeddings = map(json.loads,
        (baseline_bytes,catalog_bytes,query_bytes,embedding_bytes))
    if (baseline['catalog_sha256'] != sha(catalog_bytes) or baseline['queries_sha256'] != sha(query_bytes)
        or baseline['retriever_source_sha256'] != sha((ROOT/'src/forestry_agent/runtime/discovery.py').read_bytes())
        or embeddings['catalog_sha256'] != sha(catalog_bytes) or embeddings['queries_sha256'] != sha(query_bytes)):
        raise ValueError('Saved baseline and vectors must match frozen inputs/retriever')
    descriptors = {d['name']:d for d in catalog['descriptors']}
    core = set(descriptors) & ESSENTIAL
    corpus = set(descriptors)-core
    model = embeddings['model']
    if (not model.get('repository') or not model.get('revision') or not isinstance(model.get('dimension'),int)
        or model['dimension'] < 1 or set(embeddings['document_vectors']) != corpus
        or set(embeddings['query_vectors']) != {c['id'] for c in queries['cases']}
        or [r['id'] for r in baseline['rows']] != [c['id'] for c in queries['cases']]):
        raise ValueError('Saved vector model/corpus/query identities are incomplete')
    for row,case in zip(baseline['rows'],queries['cases'],strict=True):
        if row['required'] != case['required'] or row['query'] != case['query']:
            raise ValueError('Saved query annotation changed')
        dense = vector_rank(embeddings['document_vectors'],embeddings['query_vectors'][row['id']],model['dimension'])
        lexical = row['methods']['bm25']['ranked']
        for method,names in {'embedding':dense[:8],'hybrid_rrf':fused_rank(lexical,dense)[:8]}.items():
            selected = set(names) | core
            found = set(row['required']) & selected
            row['methods'][method] = {'ranked':names,'target_recall':len(found)/len(row['required']),
                'all_targets_found':found == set(row['required']),
                'unlabelled_candidates':len(set(names)-set(row['required'])-core),
                'registered_schema_utf8_bytes':len(encode([catalog['schemas'][n] for n in descriptors if n in selected]).encode())}
    for method in ('embedding','hybrid_rrf'):
        scores = [row['methods'][method] for row in baseline['rows']]
        baseline['summary'][method] = {'all_targets_found':sum(s['all_targets_found'] for s in scores),
            'cases':len(scores),'mean_target_recall':sum(s['target_recall'] for s in scores)/len(scores),
            'mean_registered_schema_utf8_bytes':sum(s['registered_schema_utf8_bytes'] for s in scores)/len(scores)}
    baseline.update(baseline_sha256=sha(baseline_bytes),embedding_status='evaluated_reused_lexical_baseline',
        embedding_identity=model,embedding_file_sha256=sha(embedding_bytes),
        comparison_source_sha256=sha(Path(__file__).read_bytes()),
        settings={'top_k':8,'lexical_candidates':8,'dense_candidates':16,'rrf_constant':60})
    baseline['limitations'].append('Hybrid reuses saved lexical top8; no lexical recalculation. Dense candidates limited to16.')
    return baseline


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation',choices=['freeze','benchmark'])
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--workspace',type=Path)
    parser.add_argument('--catalog',type=Path)
    parser.add_argument('--queries',type=Path,default=ROOT/'research/discovery-queries-v1.json')
    parser.add_argument('--embeddings',type=Path)
    parser.add_argument('--baseline',type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Output exists; reuse it rather than rerunning unchanged inputs')
    if args.operation == 'freeze':
        if args.workspace is None:
            parser.error('freeze requires --workspace')
        result = freeze_catalog(args.workspace)
    else:
        if args.catalog is None:
            parser.error('benchmark requires --catalog')
        if args.baseline:
            if args.embeddings is None:
                parser.error('--baseline requires --embeddings')
            result = compare_saved_baseline(args.baseline.read_bytes(),args.catalog.read_bytes(),
                args.queries.read_bytes(),args.embeddings.read_bytes())
        else:
            result = benchmark(args.catalog.read_bytes(),args.queries.read_bytes(),
                args.embeddings.read_bytes() if args.embeddings else None)
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'output':str(args.output),'summary':result.get('summary'),
        'embedding_status':result.get('embedding_status')}))
