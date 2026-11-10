"""Use a verified local Ollama GGUF vocabulary, without loading model tensors.

Text BPE is model-specific. Message/template/image framing remains an estimate;
this module never calls that estimate provider usage or downloads model assets.
"""
from functools import lru_cache
from dataclasses import dataclass
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import struct

# Qwen35 pattern documented in llama.cpp/src/llama-vocab.cpp (MIT).
# Copyright (c) 2023-2026 The ggml authors; see licenses/llama-cpp-MIT.txt.
QWEN35_PATTERN = r"(?i:'s|'t|'re|'ve|'m|'ll|'d)|[^\r\n\p{L}\p{N}]?[\p{L}\p{M}]+|\p{N}| ?[^\s\p{L}\p{M}\p{N}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+"
_FORMATS = {0: 'B', 1: 'b', 2: 'H', 3: 'h', 4: 'I', 5: 'i', 6: 'f',
            7: '?', 10: 'Q', 11: 'q', 12: 'd'}


def read_vocabulary(path):
    """Read bounded GGUF v2/v3 metadata only; reject unsupported/malformed files."""
    wanted = {'tokenizer.ggml.model', 'tokenizer.ggml.pre', 'tokenizer.ggml.tokens',
              'tokenizer.ggml.token_type', 'tokenizer.ggml.merges', 'tokenizer.chat_template'}
    with Path(path).open('rb') as stream:
        def read(size):
            if size < 0 or stream.tell() + size > 64 * 1024**2:
                raise ValueError('GGUF metadata exceeds supported bound')
            data = stream.read(size)
            if len(data) != size:
                raise ValueError('truncated GGUF metadata')
            return data

        def number(fmt):
            return struct.unpack('<' + fmt, read(struct.calcsize(fmt)))[0]

        def value(kind, keep=True):
            if kind in _FORMATS:
                result = number(_FORMATS[kind])
                return result if keep else None
            if kind == 8:
                size = number('Q')
                if size > 1024**2:
                    raise ValueError('GGUF string too large')
                data = read(size)
                return data.decode('utf-8') if keep else None
            if kind == 9:
                subtype, count = number('I'), number('Q')
                if subtype == 9 or count > 1000000:
                    raise ValueError('unsupported GGUF array')
                if keep:
                    return [value(subtype) for _ in range(count)]
                for _ in range(count):
                    value(subtype, False)
                return None
            raise ValueError('unknown GGUF metadata type')

        if read(4) != b'GGUF' or number('I') not in {2, 3}:
            raise ValueError('unsupported GGUF format')
        number('Q')  # tensor count; tensors are never read
        count = number('Q')
        if count > 10000:
            raise ValueError('too many GGUF metadata keys')
        result = {}
        for _ in range(count):
            key = value(8)
            item = value(number('I'), key in wanted)
            if key in wanted:
                result[key] = item
        end = stream.tell()
        stream.seek(0)
        result['metadata_sha256'] = hashlib.sha256(stream.read(end)).hexdigest()
        return result


def _byte_decoder():
    values = list(range(33, 127)) + list(range(161, 173)) + list(range(174, 256))
    chars = list(values)
    extra = 0
    for byte in range(256):
        if byte not in values:
            values.append(byte)
            chars.append(256 + extra)
            extra += 1
    return {chr(char): byte for byte, char in zip(values, chars)}


@lru_cache(maxsize=4)
def _load(path, size, modified):
    import tiktoken
    metadata = read_vocabulary(path)
    if metadata.get('tokenizer.ggml.model') != 'gpt2' or metadata.get('tokenizer.ggml.pre') != 'qwen35':
        raise ValueError('only verified qwen35 byte BPE is supported')
    tokens, types = metadata['tokenizer.ggml.tokens'], metadata['tokenizer.ggml.token_type']
    if len(tokens) != len(types):
        raise ValueError('inconsistent GGUF token types')
    decoder = _byte_decoder()
    ranks = {bytes(decoder[c] for c in token): index
             for index, (token, kind) in enumerate(zip(tokens, types)) if kind == 1}
    if len(ranks) != sum(kind == 1 for kind in types) or any(bytes([b]) not in ranks for b in range(256)):
        raise ValueError('ambiguous or incomplete byte vocabulary')
    # tiktoken uses token ranks. Refuse a vocabulary whose merge order disagrees
    # with its IDs instead of silently counting with a different BPE algorithm.
    ordered = []
    for merge in metadata.get('tokenizer.ggml.merges', []):
        left, right = merge.split(' ')
        ordered.append(ranks[bytes(decoder[c] for c in left + right)])
    if not ordered or ordered != sorted(ordered):
        raise ValueError('GGUF merge order is not compatible with token ranks')
    encoding = tiktoken.Encoding(name='local-qwen35', pat_str=QWEN35_PATTERN,
        mergeable_ranks=ranks, special_tokens={token: index for index, (token, kind)
            in enumerate(zip(tokens, types)) if kind in {3, 4}})
    return encoding, 'qwen35-bpe:' + metadata['metadata_sha256'], metadata.get('tokenizer.chat_template')


@lru_cache(maxsize=4)
def _compile_template(source):
    from jinja2.sandbox import SandboxedEnvironment
    environment = SandboxedEnvironment(trim_blocks=True, lstrip_blocks=True)
    def reject(message):
        raise ValueError(message)
    environment.globals['raise_exception'] = reject
    environment.filters['tojson'] = lambda value, **kwargs: json.dumps(value, ensure_ascii=False, **kwargs)
    return environment.from_string(source)


@dataclass(frozen=True)
class LocalTextTokenizer:
    encoding: object
    template: str | None

    def __call__(self, text):
        count = len(self.encoding.encode_ordinary(text))
        return count + (count + 9) // 10

    def render_wire(self, payload):
        if not self.template:
            raise ValueError('model has no chat template')
        messages = deepcopy(payload['messages'])
        for message in messages:
            for call in message.get('tool_calls', []):
                function = call.get('function', call)
                if isinstance(function.get('arguments'), str):
                    function['arguments'] = json.loads(function['arguments'])
        thinking = payload.get('think', payload.get('reasoning_effort') != 'none')
        return _compile_template(self.template).render(messages=messages, tools=payload.get('tools'),
            add_generation_prompt=True, enable_thinking=thinking, add_vision_id=False)

    def measure_wire(self, payload):
        from jinja2 import TemplateError
        try:
            rendered = self.render_wire(payload)
            text_tokens = len(self.encoding.encode(rendered, allowed_special='all'))
            visual = any(isinstance(message.get('content'), list) and any(
                isinstance(part, dict) and part.get('type') in {'image_url', 'image', 'video', 'video_url'}
                for part in message['content']) for message in payload['messages'])
            return {'status': 'estimated' if not visual else 'partial', 'text_tokens': text_tokens,
                'text_margin_tokens': (text_tokens + 9) // 10,
                'total_tokens': text_tokens + (text_tokens + 9) // 10 if not visual else None,
                'vision_tokens_status': 'unknown' if visual else 'not_present',
                'template_sha256': hashlib.sha256(self.template.encode()).hexdigest()}
        except (ValueError, KeyError, TypeError, TemplateError):
            return {'status': 'unknown', 'total_tokens': None, 'reason': 'template rendering unsupported for this request'}


def local_tokenizer(profile, identity, root=None):
    """Return an identity-bound text counter, or an explicit unavailable reason."""
    if identity.get('backend') != 'ollama' or not re.fullmatch(r'[0-9a-f]{64}', identity.get('digest', '')):
        return None, 'no verified local Ollama model identity'
    if not re.fullmatch(r'[A-Za-z0-9_.-]+(?::[A-Za-z0-9_.-]+)?', profile.served_name):
        return None, 'local manifest name unsupported'
    root = Path(root or os.environ.get('OLLAMA_MODELS', Path.home() / '.ollama/models'))
    model, _, tag = profile.served_name.partition(':')
    try:
        data = (root / 'manifests/registry.ollama.ai/library' / model / (tag or 'latest')).read_bytes()
        if hashlib.sha256(data).hexdigest() != identity['digest']:
            return None, 'local manifest does not match frozen model digest'
        manifest = json.loads(data)
        layer = next(layer for layer in manifest['layers'] if layer['mediaType'] == 'application/vnd.ollama.image.model')
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', layer['digest']):
            return None, 'invalid model layer identity'
        path = root / 'blobs' / layer['digest'].replace(':', '-')
        stat = path.stat()
        encoding, source, template = _load(str(path), stat.st_size, stat.st_mtime_ns)
        return LocalTextTokenizer(encoding, template), source + '+10pct-text-margin'
    except (OSError, ValueError, KeyError, StopIteration, ImportError, TypeError) as error:
        return None, 'local tokenizer unavailable: ' + type(error).__name__
