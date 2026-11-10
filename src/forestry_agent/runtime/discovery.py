"""Small lexical index over the already-authorized tool definitions.

Uses standard BM25 with Chinese character/bigram terms. The registry remains
the definition source; retrieval neither creates permissions nor changes an
already frozen request. No external code is executed or embedded here.
"""
from collections import Counter
import math
import re
import unicodedata

from .interfaces import ToolDescriptor

ESSENTIAL = frozenset({'search_tools', 'list_inputs', 'list_files', 'read_file',
    'inspect_artifact', 'read_artifact', 'write_file', 'wait_job', 'set_job_follow_up',
    'read_tool_result', 'read_tool_result_window'})

DISCOVERY_GUIDANCE = (
    '当前只展示已加载的工具子集，未见某项工具不等于没有该能力或资料。'
    '需要尚不可见的能力时，调用search_tools按用途检索；先发现再在下一模型步骤调用。'
    '目录涵盖当前允许的能力；检索没有授予新权限，也不证明资料存在或已经读取。'
    '只有读取/查询的实际结果才能支持“没有找到”，不能因没有看到文件路径或工具名而断言资料未提供。'
)

# Vocabulary expands retrieval terms only; it never chooses or invokes a tool.
SYNONYMS = {'缺失值': 'csv python code', '数据清洗': 'csv python code',
    '反演': 'prosail lai invert', '正射': 'orthomosaic odm',
    '地形': 'terrain laz lidar', '计数': 'image 图像', '记忆': 'memory 笔记'}


def terms(text):
    value = unicodedata.normalize('NFKC', text).lower()
    result = re.findall(r'[a-z0-9]+', value)
    for run in re.findall(r'[\u3400-\u9fff]+', value):
        result.extend(run)
        result.extend(run[i:i + 2] for i in range(len(run) - 1))
    return result


def search(descriptors: list[ToolDescriptor], query: str, limit: int = 8) -> list[dict]:
    if not query.strip() or len(query) > 1000 or not 1 <= limit <= 16:
        raise ValueError('query须为1–1000字符，limit须为1–16')
    expanded = query + ' ' + ' '.join(v for k, v in SYNONYMS.items() if k in query)
    query_terms = set(terms(expanded))
    docs = []
    for tool in descriptors:
        counts = Counter(terms(tool.name + ' ' + tool.capability) * 3
            + terms(tool.summary_zh + ' ' + tool.summary_en + ' ' + ' '.join(tool.accepts)))
        docs.append((tool, counts, sum(counts.values())))
    if not docs:
        return []
    average = sum(length for _, _, length in docs) / len(docs) or 1
    frequency = Counter(term for _, counts, _ in docs for term in counts)
    ranked = []
    for tool, counts, length in docs:
        score = 0.0
        for term in query_terms:
            tf = counts[term]
            if tf:
                inverse = math.log(1 + (len(docs) - frequency[term] + 0.5) / (frequency[term] + 0.5))
                score += inverse * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / average))
        if score:
            ranked.append({'name': tool.name, 'summary': tool.summary_zh,
                           'capability': tool.capability, 'score': round(score, 6)})
    return sorted(ranked, key=lambda match: (-match['score'], match['name']))[:limit]

