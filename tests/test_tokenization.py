import hashlib
import json
import struct

import pytest

from forestry_agent.config import ModelProfile
from forestry_agent.runtime.tokenization import local_tokenizer, read_vocabulary


def fixture_model(tmp_path, pre='qwen35', template=None):
    # Tiny, fully declared byte-BPE model: all bytes plus the merge a+a -> aa.
    visible = list(range(33, 127)) + list(range(161, 173)) + list(range(174, 256))
    mapping = {byte: chr(byte) for byte in visible}
    for index, byte in enumerate(byte for byte in range(256) if byte not in mapping):
        mapping[byte] = chr(256 + index)
    tokens = [mapping[byte] for byte in range(256)] + ['aa']

    def string(text):
        data = text.encode('utf-8')
        return struct.pack('<Q', len(data)) + data

    entries = []
    for key, value in [('tokenizer.ggml.model', 'gpt2'), ('tokenizer.ggml.pre', pre)]:
        entries.append(string(key) + struct.pack('<I', 8) + string(value))
    if template is not None:
        entries.append(string('tokenizer.chat_template') + struct.pack('<I', 8) + string(template))
    for key, values, kind in [('tokenizer.ggml.tokens', tokens, 8),
                              ('tokenizer.ggml.token_type', [1] * 257, 5),
                              ('tokenizer.ggml.merges', ['a a'], 8)]:
        data = b''.join(string(v) if kind == 8 else struct.pack('<i', v) for v in values)
        entries.append(string(key) + struct.pack('<IIQ', 9, kind, len(values)) + data)
    blob = b'GGUF' + struct.pack('<IQQ', 3, 0, len(entries)) + b''.join(entries)
    root = tmp_path / 'models'
    (root / 'blobs').mkdir(parents=True)
    digest = 'sha256:' + hashlib.sha256(blob).hexdigest()
    (root / 'blobs' / digest.replace(':', '-')).write_bytes(blob)
    manifest = json.dumps({'layers': [{'mediaType': 'application/vnd.ollama.image.model', 'digest': digest}]}).encode()
    path = root / 'manifests/registry.ollama.ai/library/fixture/latest'
    path.parent.mkdir(parents=True)
    path.write_bytes(manifest)
    return root, {'backend': 'ollama', 'digest': hashlib.sha256(manifest).hexdigest()}


def test_identity_bound_counter_uses_bpe_and_declares_margin(tmp_path):
    root, identity = fixture_model(tmp_path)
    profile = ModelProfile(repository='software-fixture', served_name='fixture')
    counter, source = local_tokenizer(profile, identity, root)
    assert counter is not None and source.startswith('qwen35-bpe:')
    assert source.endswith('+10pct-text-margin')
    assert counter('aaaa') == 3  # two aa tokens and one margin token
    assert counter('abcd') == 5 and counter('') == 0
    assert counter('林') == 4  # three UTF-8 byte tokens in this fixture, plus margin


@pytest.mark.parametrize('fault', ['digest', 'pre', 'path'])
def test_unverified_or_unsupported_assets_fall_back_explicitly(tmp_path, fault):
    root, identity = fixture_model(tmp_path, 'unsupported' if fault == 'pre' else 'qwen35')
    if fault == 'digest':
        identity['digest'] = '0' * 64
    profile = ModelProfile(repository='software-fixture', served_name='../escape' if fault == 'path' else 'fixture')
    counter, reason = local_tokenizer(profile, identity, root)
    assert counter is None and reason


def test_corrupt_metadata_is_rejected_without_loading_tensors(tmp_path):
    path = tmp_path / 'bad.gguf'
    path.write_bytes(b'GGUF' + struct.pack('<IQQ', 3, 0, 1))
    with pytest.raises(ValueError, match='truncated'):
        read_vocabulary(path)
