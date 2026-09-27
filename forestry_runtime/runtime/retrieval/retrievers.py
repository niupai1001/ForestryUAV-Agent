"""Retrievers: one per source, each answering in the same shape.

Two are implemented here, and the split between them is the point of the whole
fabric:

* **FailureRetriever** answers *exact* questions -- "what is already known to be false
  about this resource". It is a lookup, not a search, and it is ranked first because a
  recorded fact should not compete with a plausible text match.
* **ToolRetriever** answers *fuzzy* questions -- "which capability bears on this". It
  is lexical, because the tool set is small and named things (``fs_read``,
  ``install``, ``job``) are looked up by name far more often than by meaning.

Chinese support is the honest weak point. Tool descriptions are English, so a Chinese
query shares no terms with them and pure lexical matching scores zero -- the observed
"correct tool never entered the candidate set" failure. Until a dense index exists,
each tool carries a small set of Chinese aliases that extend its searchable text. They
are not a routing rule: they are added to the same haystack and scored identically,
and every one of them is pinned by the frozen bilingual test set so it cannot rot
quietly.
"""
from __future__ import annotations

import re

from ..failure.store import FailureStore
from .models import Candidate

_CJK = "一-鿿"


def terms_of(text: str, minimum: int = 2) -> set[str]:
    """Search terms for one query.

    Whitespace tokens alone would make a Chinese query a single unmatchable blob, so
    CJK runs are also sliced into bigrams: "检查目录" yields 检查, 查目, 目录.
    """
    text = (text or "").casefold()
    found: set[str] = set()
    for token in re.split(r"[^\w" + _CJK + "]+", text):
        if len(token) >= minimum:
            found.add(token)
    for run in re.findall("[" + _CJK + "]+", text):
        for index in range(len(run) - 1):
            found.add(run[index:index + 2])
    return found


def score_text(terms: set[str], haystack: str) -> float:
    """Overlap score, weighted by term length.

    Longer terms are more specific, so matching "dependency_install" should outrank
    matching "it". This is deliberately not BM25: with a corpus of dozens of short
    documents, term frequency carries almost no signal and inverse document frequency
    costs a maintained index to compute.
    """
    haystack = (haystack or "").casefold()
    if not haystack:
        return 0.0
    return float(sum(len(term) for term in terms if term in haystack))


class FailureRetriever:
    """Recorded failures, as facts the model can query instead of rediscovering."""

    source_type = "failure"

    def __init__(self, store: FailureStore):
        self.store = store

    def retrieve(self, query: str, top_k: int = 5) -> list[Candidate]:
        terms = terms_of(query)
        candidates = []
        for record in self.store.all():
            haystack = " ".join(filter(None, (
                record.resource_key or "", record.code, record.label,
                " ".join(record.invalid_assumptions), record.tool,
            )))
            score = score_text(terms, haystack)
            if score <= 0:
                continue
            candidates.append(Candidate(
                id=record.id,
                source_type=self.source_type,
                content=(
                    f"{record.label}: {'; '.join(record.invalid_assumptions) or record.code}"
                    f" (policy {record.retry_policy}, {record.occurrences}×)"
                ),
                exact_reference={"evidence_id": record.id, "resource_key": record.resource_key},
                version=record.resource_version,
                # A recorded failure is a fact, not a match. Promoting it above fused
                # text matches is what stops the model re-deriving something the
                # Runtime already knows to be false.
                structural_score=1.0 + score / 100.0,
                metadata={"code": record.code, "policy": record.retry_policy},
            ))
        candidates.sort(key=lambda item: item.structural_score or 0.0, reverse=True)
        return candidates[:top_k]


class ToolRetriever:
    """Which capability bears on this query."""

    source_type = "tool"

    #: Chinese phrases added to each tool's searchable text. See the module docstring:
    #: a bridge until a dense index exists, scored exactly like any other text.
    ALIASES: dict[str, tuple[str, ...]] = {
        "fs_read": ("读取文件", "读文件", "查看文件内容", "打开文件"),
        "fs_list": ("列出目录", "列目录", "查看目录", "列举文件"),
        "fs_search": ("搜索内容", "查找字符串", "全文检索", "搜索文件"),
        "fs_write": ("写文件", "创建文件", "新建文件"),
        "fs_edit": ("修改文件", "编辑文件", "改写文件"),
        "code_run": ("运行代码", "执行代码", "跑脚本", "运行 python"),
        "dependency_install": ("安装依赖", "安装包", "装包"),
        "environment_check": ("检查环境", "环境检查", "依赖是否可用", "模块能否导入"),
        "job_status": ("作业状态", "任务状态", "查看进度"),
        "job_wait": ("等待作业", "等待任务完成"),
        "job_log": ("作业日志", "查看日志"),
        "job_cancel": ("取消作业", "终止任务"),
        "tool_result_read": ("读取工具结果", "查看工具输出"),
        "artifacts_inspect": ("查看产物", "检查产物"),
        "artifacts_preview": ("预览产物", "生成预览"),
        "knowledge_search": ("检索知识", "搜索指南", "知识库检索"),
        "knowledge_read": ("读取指南", "读文档", "查看指南正文"),
        "domain_guide": ("领域指南", "领域知识"),
        "work_plan": ("工作计划", "记录计划", "更新计划"),
    }

    def __init__(self, specs=None):
        self.specs = specs if specs is not None else self._load()

    @staticmethod
    def _load():
        try:
            from ..capabilities.core_specs import load_specs
            return tuple(load_specs())
        except Exception:
            return ()

    def _haystack(self, spec) -> str:
        parts = [spec.name, spec.description, spec.equivalence_group,
                 spec.returns, " ".join(spec.keywords or ())]
        parts.extend(self.ALIASES.get(spec.name, ()))
        declaration = getattr(spec, "declaration", None)
        if declaration is not None:
            parts.append(str(getattr(declaration, "requires", "") or ""))
        return " ".join(part for part in parts if part)

    def retrieve(self, query: str, top_k: int = 10) -> list[Candidate]:
        terms = terms_of(query)
        if not terms:
            return []
        scored = []
        for spec in self.specs:
            haystack = self._haystack(spec)
            score = score_text(terms, haystack)
            # A query that names the tool, or one of its deferred keywords, is not a
            # fuzzy match at all -- it is a lookup, and it must win.
            folded = (query or "").casefold()
            if spec.name.casefold() in folded or spec.name.replace("_", " ") in folded:
                score += 200.0
            else:
                # The verb in a tool name is the strongest lexical signal available --
                # "read", "install", "cancel" -- and it is what a query asking for that
                # capability actually contains.
                for token in spec.name.split("_"):
                    if len(token) >= 3 and token in folded:
                        score += 60.0
            for keyword in spec.keywords or ():
                if str(keyword).casefold() in folded:
                    score += 80.0
            if score <= 0:
                continue
            scored.append((score, spec))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            Candidate(
                id=spec.name,
                source_type=self.source_type,
                content=spec.description[:400],
                exact_reference={"tool": spec.name, "group": spec.equivalence_group},
                lexical_rank=index,
                metadata={"plugin": spec.plugin, "deferred": bool(spec.deferred)},
            )
            for index, (_, spec) in enumerate(scored[:top_k], start=1)
        ]


class DomainRetriever:
    """Domain tool groups, on the same ranked list as every other capability.

    Without this the domain groups were decided by a pre-pass inside the agent that
    nobody outside the call could see: which schemas are undeferred was chosen by
    one scorer, while what the request was judged to be about was chosen by another,
    and the two could disagree silently. Registering them here means a group has to
    compete with tools, memory and failures for the budget like anything else, and
    the ranking that produced the context is the ranking that opened the schema.

    ``domain_registry`` is imported inside the call, not at module scope: it scores
    with ``score_text`` from this module, so a top-level import between the two is a
    cycle whichever way round it is written.
    """

    source_type = "tool"

    def retrieve(self, query: str, top_k: int = 10) -> list[Candidate]:
        try:
            from ..domain_registry import domain_candidates
            return list(domain_candidates(query, top_k=top_k))
        except Exception:
            # A broken group costs the Run that group, not its whole tool set.
            return []


class MemoryRetriever:
    """Project memory and knowledge, through the manager that already owns it.

    Deliberately a wrapper rather than a second implementation: memory has its own
    store, its own hybrid keyword/vector ranking and its own project scoping, and
    duplicating any of that would produce a second answer to "what does this project
    know" that disagrees with the first. What this adds is the contract -- a result
    that can be fused with tools and failures and budgeted against them.
    """

    def __init__(self, search=None, source_type: str = "memory"):
        self.search = search
        self.source_type = source_type

    def retrieve(self, query: str, top_k: int = 6) -> list[Candidate]:
        if not callable(self.search) or not (query or "").strip():
            return []
        try:
            payload = self.search(query, top_k)
        except Exception:
            return []
        rows = payload.get("results") if isinstance(payload, dict) else payload
        candidates = []
        for index, row in enumerate(rows or [], start=1):
            if not isinstance(row, dict):
                continue
            candidates.append(Candidate(
                id=str(row.get("id") or row.get("chunk_id") or f"mem_{index}"),
                source_type=self.source_type,
                content=str(row.get("content") or "")[:800],
                exact_reference={
                    "source_id": row.get("source_id"),
                    "citation": row.get("citation"),
                    "document": row.get("document") or row.get("title"),
                },
                version=str(row.get("source_version") or row.get("version") or "") or None,
                lexical_rank=index,
                metadata={
                    "project_id": row.get("project_id"),
                    "mode": payload.get("mode") if isinstance(payload, dict) else None,
                },
            ))
        return candidates


__all__ = ["DomainRetriever", "FailureRetriever", "MemoryRetriever", "ToolRetriever",
           "score_text", "terms_of"]
