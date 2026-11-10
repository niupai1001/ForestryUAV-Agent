"""One identity-frozen raw-token calibration; refuses to overwrite an old trial."""
import hashlib
import json
from pathlib import Path
import urllib.request

from forestry_agent.config import ModelProfile
from forestry_agent.runtime.tokenization import local_tokenizer, _load


def main():
    output = Path('workspace/tokenizer-calibration-20261025')
    output.mkdir(parents=True, exist_ok=False)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(endpoint):
        with opener.open('http://127.0.0.1:11434/api/' + endpoint, timeout=5) as response:
            return json.load(response)

    report = {'trial': 'tokenizer-calibration-20261025', 'request_limit': 1,
        'requests_dispatched': 0, 'scientific_jobs': 0, 'scope': 'raw-text-tokenizer-only'}
    try:
        report['before'] = get('ps')
        if report['before'].get('models'):
            raise RuntimeError('model resource is already occupied')
        profile = ModelProfile(repository='Qwen/Qwen3.5-4B', served_name='qwen3.5:4b')
        observed = next(model for model in get('tags')['models'] if model['name'] == profile.served_name)
        expected = 'd8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04'
        if observed['digest'] != expected:
            raise RuntimeError('model digest changed')
        identity = {'backend': 'ollama', 'digest': expected}
        count, source = local_tokenizer(profile, identity)
        if count is None:
            raise RuntimeError(source)
        root = Path.home() / '.ollama/models'
        manifest = json.loads((root / 'manifests/registry.ollama.ai/library/qwen3.5/4b').read_bytes())
        layer = next(layer for layer in manifest['layers'] if layer['mediaType'] == 'application/vnd.ollama.image.model')
        blob = root / 'blobs' / layer['digest'].replace(':', '-')
        stat = blob.stat()
        encoding, _, _ = _load(str(blob), stat.st_size, stat.st_mtime_ns)
        sample = ('林业无人机正射影像，多光谱LAI反演；这是文本分词校准，不执行科学计算。'
                  ' English contractions: we\'re testing café and e\u0301; numbers 1234567890.\n'
                  '{"name":"process_imagery","operation":"invert","input_refs":{"raster":"fixture"}}\n')
        prompt = sample * 24 + '\n只需输出OK。'
        body = {'model': profile.served_name, 'prompt': prompt, 'raw': True, 'stream': False,
            'think': False, 'keep_alive': 0, 'options': {'num_ctx': 32768, 'num_predict': 1, 'temperature': 0}}
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        (output / 'request.json').write_bytes(data)
        report.update(model_identity=identity, tokenizer_identity=source,
            prompt_utf8_bytes=len(prompt.encode()), lexical_tokens=len(encoding.encode_ordinary(prompt)),
            estimated_with_margin=count(prompt), body_sha256=hashlib.sha256(data).hexdigest())
        # Persist the budget charge before dispatch, including failed transport attempts.
        report['requests_dispatched'] = 1
        (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        request = urllib.request.Request('http://127.0.0.1:11434/api/generate', data=data,
            headers={'Content-Type': 'application/json'}, method='POST')
        with opener.open(request, timeout=300) as response:
            result = json.load(response)
        (output / 'response.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        actual = result.get('prompt_eval_count')
        report.update(provider_prompt_eval_count=actual, exact_raw_match=actual == report['lexical_tokens'],
            covered_by_text_margin=isinstance(actual, int) and actual <= report['estimated_with_margin'],
            after=get('ps'))
        if not report['exact_raw_match']:
            raise RuntimeError('raw provider tokenizer count disagrees; preserve this trial and investigate')
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report))


if __name__ == '__main__':
    main()
