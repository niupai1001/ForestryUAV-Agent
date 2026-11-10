"""Freeze official E5 ONNX/tokenizer assets; no remote Python/model execution."""
import argparse
import hashlib
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen

REPOSITORY = 'intfloat/multilingual-e5-small'
ROOT = Path(__file__).resolve().parents[1]
FILES = ['onnx/model_qint8_avx512_vnni.onnx','onnx/tokenizer.json',
         'onnx/tokenizer_config.json','onnx/special_tokens_map.json','onnx/config.json','config.json']
LIMIT = 160*1024*1024


def fetch_json(url):
    with urlopen(Request(url,headers={'User-Agent':'ForestryUAV-research/1.0'}),timeout=45) as response:
        raw = response.read(8*1024*1024+1)
    if len(raw) > 8*1024*1024:
        raise ValueError('Metadata exceeds budget')
    return json.loads(raw)


def fetch_assets(directory):
    if directory.exists() or not directory.resolve().is_relative_to((ROOT/'workspace').resolve()):
        raise ValueError('Use a new isolated asset directory under project/workspace; existing attempts are retained')
    directory.mkdir(parents=True)
    started = time.monotonic()
    report = {'repository':REPOSITORY,'status':'preparing','limit_bytes':LIMIT,'files':[],
              'source':'https://huggingface.co/'+REPOSITORY,'seconds_limit':300}
    try:
        metadata = fetch_json('https://huggingface.co/api/models/'+REPOSITORY)
        revision = metadata['sha']
        if len(revision) != 40 or metadata.get('cardData',{}).get('license') != 'mit':
            raise ValueError('Model revision/license not verified')
        report.update(revision=revision,license='mit')
        (directory/'model-info.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
        entries = fetch_json(f'https://huggingface.co/api/models/{REPOSITORY}/tree/{revision}/onnx?recursive=false')
        entries += fetch_json(f'https://huggingface.co/api/models/{REPOSITORY}/tree/{revision}?recursive=false')
        indexed = {entry['path']:entry for entry in entries if entry['type']=='file'}
        if any(name not in indexed for name in FILES) or sum(indexed[name]['size'] for name in FILES) > LIMIT:
            raise ValueError('Official asset list/size exceeds the frozen budget')
        (directory/'asset-index.json').write_text(json.dumps([indexed[n] for n in FILES],indent=2),encoding='utf-8')
        for name in FILES:
            entry = indexed[name]
            target = directory/name
            if not target.resolve().is_relative_to(directory.resolve()):
                raise ValueError('Asset path escaped research directory')
            target.parent.mkdir(parents=True,exist_ok=True)
            temporary = target.with_suffix(target.suffix+'.partial')
            total, digest = 0,hashlib.sha256()
            url = f'https://huggingface.co/{REPOSITORY}/resolve/{revision}/{name}?download=true'
            with urlopen(Request(url,headers={'User-Agent':'ForestryUAV-research/1.0'}),timeout=45) as response:
                with temporary.open('xb') as stream:
                    while chunk := response.read(1024*1024):
                        total += len(chunk)
                        if total > entry['size'] or time.monotonic()-started > 300:
                            raise ValueError('Asset download exceeded frozen size/time budget')
                        digest.update(chunk)
                        stream.write(chunk)
            if total != entry['size']:
                raise ValueError('Asset size mismatch')
            if entry.get('lfs'):
                verified = digest.hexdigest() == entry['lfs']['oid']
            else:
                verified = hashlib.sha1(f'blob {total}\0'.encode()+temporary.read_bytes()).hexdigest() == entry['oid']
            if not verified:
                raise ValueError('Official asset content hash mismatch')
            temporary.replace(target)
            report['files'].append({'path':name,'size':total,'sha256':digest.hexdigest(),'official_hash_verified':True})
            print(json.dumps({'downloaded':name,'bytes':total}),flush=True)
        report.update(status='ready',elapsed_seconds=time.monotonic()-started)
    except Exception as error:
        report.update(status='failed',error=f'{type(error).__name__}: {error}',elapsed_seconds=time.monotonic()-started)
        raise
    finally:
        with (directory/'manifest.json').open('x',encoding='utf-8') as stream:
            json.dump(report,stream,ensure_ascii=False,indent=2)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory',type=Path,required=True)
    args = parser.parse_args()
    report = fetch_assets(args.directory)
    print(json.dumps({'status':report['status'],'revision':report['revision'],'files':len(report['files'])}))
