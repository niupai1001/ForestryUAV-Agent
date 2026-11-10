"""One chat-template calibration after the frozen raw-token calibration."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import urllib.request

from forestry_agent.config import ModelProfile
from forestry_agent.resources import native_path
from forestry_agent.runtime.tokenization import local_tokenizer


def main():
    output = Path('workspace/template-calibration-20261025')
    output.mkdir(parents=True, exist_ok=False)
    report = {'trial': 'template-calibration-20261025', 'new_requests_dispatched': 0,
        'calibration_family_request_limit': 2, 'scientific_jobs': 0, 'scope': 'text-chat-template-only'}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(endpoint):
        with opener.open('http://127.0.0.1:11434/api/' + endpoint, timeout=5) as response:
            return json.load(response)

    try:
        prior = json.loads(Path('workspace/tokenizer-calibration-20261025/report.json').read_text())
        if not prior.get('exact_raw_match') or prior['requests_dispatched'] != 1:
            raise RuntimeError('requires the frozen successful raw calibration, not a reset budget')
        report['prior_family_requests'] = 1
        report['before'] = get('ps')
        if report['before'].get('models'):
            raise RuntimeError('model resource already occupied')
        identity = prior['model_identity']
        observed = next(model for model in get('tags')['models'] if model['name'] == 'qwen3.5:4b')
        if observed['digest'] != identity['digest']:
            raise RuntimeError('model digest changed')
        counter, source = local_tokenizer(ModelProfile(repository='Qwen/Qwen3.5-4B', served_name='qwen3.5:4b'), identity)
        if counter is None:
            raise RuntimeError(source)
        sources = list(Path('workspace/rebuild-envelope-sdk-fixed-20261025').rglob('*.request.json'))
        if len(sources) != 1:
            raise RuntimeError('exact SDK source request missing or ambiguous')
        original = native_path(sources[0]).read_bytes()
        (output / 'source-sdk.request.json').write_bytes(original)
        wire = json.loads(original)
        wire['think'] = False
        measurement = counter.measure_wire(wire)
        if measurement['status'] != 'estimated':
            raise RuntimeError('text-only template must render before calibration')
        (output / 'rendered.txt').write_text(counter.render_wire(wire), encoding='utf-8')
        body = {'model': wire['model'], 'messages': deepcopy(wire['messages']), 'tools': wire.get('tools'),
            'think': False, 'stream': False, 'keep_alive': 0,
            'options': {'num_ctx': 32768, 'num_predict': 1, 'temperature': 0}}
        for message in body['messages']:
            for call in message.get('tool_calls', []):
                function = call.get('function', call)
                if isinstance(function.get('arguments'), str):
                    function['arguments'] = json.loads(function['arguments'])
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        (output / 'request.json').write_bytes(data)
        report.update(model_identity=identity, tokenizer_identity=source, measurement=measurement,
            source_sdk_sha256=hashlib.sha256(original).hexdigest(), body_sha256=hashlib.sha256(data).hexdigest(),
            tool_count=len(body.get('tools') or []), new_requests_dispatched=1)
        (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        request = urllib.request.Request('http://127.0.0.1:11434/api/chat', data=data,
            headers={'Content-Type': 'application/json'}, method='POST')
        with opener.open(request, timeout=300) as response:
            result = json.load(response)
        (output / 'response.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        actual = result.get('prompt_eval_count')
        report.update(provider_prompt_eval_count=actual, exact_template_match=actual == measurement['text_tokens'],
            estimate_covers_usage=isinstance(actual, int) and actual <= measurement['total_tokens'], after=get('ps'))
        if not report['estimate_covers_usage']:
            raise RuntimeError('template estimate undercounted; preserve trial and investigate without retry')
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report))


if __name__ == '__main__':
    main()
