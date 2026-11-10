"""One local CPU embedding run over frozen public tool metadata/query text."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from forestry_agent.runtime.discovery import ESSENTIAL  # noqa: E402


def file_sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


def pool_hidden(hidden,mask):
    import numpy as np
    counts = mask.sum(axis=1,keepdims=True)
    if np.any(counts == 0):
        raise ValueError('Empty token mask')
    pooled = (hidden*mask[:,:,None]).sum(axis=1)/counts
    norms = np.linalg.norm(pooled,axis=1,keepdims=True)
    if not np.isfinite(pooled).all() or not np.isfinite(norms).all() or np.any(norms == 0):
        raise ValueError('Nonfinite/zero model embedding')
    return pooled/norms


def encode(args):
    sys.path.insert(0,str(args.packages.resolve()))
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer

    manifest = json.loads((args.assets/'manifest.json').read_text(encoding='utf-8'))
    if manifest['status'] != 'ready' or manifest['license'] != 'mit':
        raise ValueError('Assets are not verified ready')
    hashes = {}
    for item in manifest['files']:
        path = args.assets/item['path']
        if not path.resolve().is_relative_to(args.assets.resolve()) or file_sha(path) != item['sha256']:
            raise ValueError('Embedding asset bytes changed')
        hashes[item['path']] = item['sha256']
    catalog_bytes,query_bytes = args.catalog.read_bytes(),args.queries.read_bytes()
    catalog,queries = json.loads(catalog_bytes),json.loads(query_bytes)
    docs = {d['name']:'passage: '+' '.join([d['name'],d['capability'],d['summary_zh'],d['summary_en'],*d['accepts']])
            for d in catalog['descriptors'] if d['name'] not in ESSENTIAL}
    query_texts = {c['id']:'query: '+c['query'] for c in queries['cases']}
    texts = [*docs.values(),*query_texts.values()]
    tokenizer = Tokenizer.from_file(str(args.assets/'onnx/tokenizer.json'))
    config = json.loads((args.assets/'config.json').read_text())
    pad_id = config['pad_token_id']
    tokenizer.enable_padding(pad_id=pad_id,pad_token=tokenizer.id_to_token(pad_id))
    tokenizer.enable_truncation(max_length=512)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    started = time.monotonic()
    marker = args.output.with_suffix('.started.json')
    if args.output.exists() or marker.exists():
        raise ValueError('Embedding trial already exists; inspect original attempt, do not reset budget')
    with marker.open('x',encoding='utf-8') as stream:
        json.dump({'catalog_sha256':hashlib.sha256(catalog_bytes).hexdigest(),
            'queries_sha256':hashlib.sha256(query_bytes).hexdigest(),'revision':manifest['revision'],
            'model_sha256':hashes['onnx/model_qint8_avx512_vnni.onnx'],
            'source_sha256':file_sha(Path(__file__)),'texts':len(texts),
            'limits':{'seconds':300,'cpu_threads':2,'batch_size':8}},stream,indent=2)
    session = ort.InferenceSession(str(args.assets/'onnx/model_qint8_avx512_vnni.onnx'),
        sess_options=options,providers=['CPUExecutionProvider'])
    if session.get_providers() != ['CPUExecutionProvider']:
        raise ValueError('Unexpected inference provider')
    load_seconds = time.monotonic()-started
    outputs,at_limit = [],0
    for offset in range(0,len(texts),8):
        if time.monotonic()-started > 300:
            raise ValueError('Frozen embedding inference wall budget exhausted')
        encoded = tokenizer.encode_batch(texts[offset:offset+8])
        masks = np.asarray([e.attention_mask for e in encoded],dtype=np.int64)
        feed = {'input_ids':np.asarray([e.ids for e in encoded],dtype=np.int64),
                'attention_mask':masks,'token_type_ids':np.asarray([e.type_ids for e in encoded],dtype=np.int64)}
        names = {i.name for i in session.get_inputs()}
        if not names <= set(feed):
            raise ValueError('Unsupported ONNX model inputs')
        hidden = session.run(['last_hidden_state'],{name:feed[name] for name in names})[0]
        vectors = pool_hidden(hidden,masks)
        if vectors.shape[1] != config['hidden_size']:
            raise ValueError('Embedding output dimension differs from frozen model')
        outputs.extend(vectors.tolist())
        at_limit += sum(sum(e.attention_mask)==512 for e in encoded)
        print(json.dumps({'encoded':min(offset+8,len(texts)),'total':len(texts)}),flush=True)
    result = {'catalog_sha256':hashlib.sha256(catalog_bytes).hexdigest(),
        'queries_sha256':hashlib.sha256(query_bytes).hexdigest(),
        'model':{'repository':manifest['repository'],'revision':manifest['revision'],
            'dimension':config['hidden_size'],'quantization':'qint8_avx512_vnni_export',
            'model_sha256':hashes['onnx/model_qint8_avx512_vnni.onnx'],
            'tokenizer_sha256':hashes['onnx/tokenizer.json'],'provider':'CPUExecutionProvider',
            'pooling':'attention_mask_mean_l2','query_prefix':'query: ','document_prefix':'passage: '},
        'document_vectors':dict(zip(docs,outputs[:len(docs)],strict=True)),
        'query_vectors':dict(zip(query_texts,outputs[len(docs):],strict=True)),
        'packages':{name:importlib.metadata.version(name) for name in ('onnxruntime','tokenizers','numpy')},
        'package_lock_sha256':file_sha(args.packages.parent/'requirements.lock'),
        'input_texts_sha256':hashlib.sha256(json.dumps(texts,ensure_ascii=False).encode()).hexdigest(),
        'source_sha256':file_sha(Path(__file__)),'load_seconds':load_seconds,
        'total_seconds':time.monotonic()-started,'texts_at_truncation_limit':at_limit,
        'note':'Single CPU retrieval experiment; expected labels are not model inputs; no dialogue/scientific jobs.'}
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('packages','assets','catalog','queries','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.with_suffix('.started.json').exists():
        raise SystemExit('Frozen trial/output exists; no second inference under the same identity')
    result = encode(args)
    print(json.dumps({'vectors':len(result['document_vectors'])+len(result['query_vectors']),
        'seconds':result['total_seconds'],'revision':result['model']['revision']}))
