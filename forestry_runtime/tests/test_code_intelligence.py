"""Code answers have to state how they were obtained, or they are guesses.

The failure this test set exists to catch: a question about *where something is
defined* answered from a substring search, reported in a form indistinguishable from
a real lookup. Every one of those answers looks plausible; the damage is that nothing
tells the model which kind it got, so it cannot decide how much to trust it.

The assertions are therefore on precision as much as on position: a definition from
the index says ``structural``, a text match says ``heuristic``, and asking about a
symbol that is not in the index is answered as degraded rather than as a confident
guess.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtime.code_intelligence import (
    CodeIntelligenceService, LexicalBackend, SymbolIndex,
)
from runtime.code_intelligence.service import looks_like_symbol
from runtime.code_intelligence.index_store import file_version

#: One workspace where "mentioned" and "defined" are deliberately different places,
#: so a text search cannot accidentally produce the right kind of answer.
MODULE_A = '''\
"""Module A."""
ERROR_CODE = "E-4412"


def alpha():
    """Defined here."""
    return beta()


def beta():
    return 1
'''

MODULE_B = '''\
"""Module B.

Mentions alpha in prose, and calls it once.
"""


def gamma():
    # alpha is mentioned in this comment too
    return alpha()
'''


class _Workspace:
    def __init__(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="ci_"))
        (self.dir / "a.py").write_text(MODULE_A, encoding="utf-8")
        (self.dir / "b.py").write_text(MODULE_B, encoding="utf-8")

    def service(self) -> CodeIntelligenceService:
        return CodeIntelligenceService(self.dir)


class SymbolIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = _Workspace()
        self.service = self.workspace.service()

    def test_definition_is_found_in_the_index(self) -> None:
        found = self.service.find_symbol("alpha")
        self.assertTrue(found, "a function defined in the workspace must be indexed")
        self.assertEqual(found[0].short_name, "alpha")
        self.assertEqual(found[0].kind, "function")
        self.assertEqual(found[0].path, "a.py")

    def test_definition_location_carries_structural_precision(self) -> None:
        locations = self.service.definition("alpha")
        self.assertTrue(locations)
        self.assertEqual(locations[0].precision, "structural")
        self.assertIn("def alpha", locations[0].snippet)

    def test_callers_come_from_call_relations_not_text(self) -> None:
        callers = self.service.callers("alpha")
        paths = {item.path for item in callers}
        self.assertIn("b.py", paths, "gamma calls alpha; that is a caller")
        # a.py mentions alpha only as its own definition, so it must not appear.
        self.assertNotIn("a.py", paths)

    def test_references_include_the_definition(self) -> None:
        references = self.service.references("alpha")
        self.assertTrue(any(item.path == "a.py" for item in references))

    def test_impact_is_structural_when_the_symbol_is_known(self) -> None:
        result = self.service.impact("alpha")
        self.assertFalse(result.degraded)
        self.assertEqual(result.precision, "structural")
        self.assertTrue(result.definitions)

    def test_impact_is_degraded_when_the_symbol_is_unknown(self) -> None:
        result = self.service.impact("no_such_symbol_anywhere")
        self.assertTrue(result.degraded, "an unknown symbol must not be answered confidently")
        self.assertEqual(result.precision, "heuristic")
        self.assertTrue(result.note)

    def test_invalidate_drops_what_is_known_about_a_file(self) -> None:
        self.assertTrue(self.service.definition("alpha"))
        self.service.invalidate(str(self.workspace.dir / "a.py"))
        self.assertEqual(
            self.service.index.find_symbols("alpha", limit=5), [],
            "after an edit the stale index entry must not be served",
        )

    def test_file_version_changes_when_the_file_changes(self) -> None:
        target = self.workspace.dir / "a.py"
        before = file_version(target)
        target.write_text(MODULE_A + "\n# touched\n", encoding="utf-8")
        self.assertNotEqual(before, file_version(target))


class LexicalBackendTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = _Workspace()
        self.lexical = LexicalBackend(self.workspace.dir)

    def test_text_search_finds_a_string_that_is_not_a_symbol(self) -> None:
        found = self.lexical.search("E-4412")
        self.assertTrue(found, "an error code is exactly what a lexical search is for")

    def test_text_results_are_labelled_heuristic(self) -> None:
        found = self.lexical.search("E-4412")
        self.assertEqual(found[0].precision, "heuristic")

    def test_text_search_finds_a_comment_mention_the_index_ignores(self) -> None:
        found = self.lexical.search("alpha is mentioned")
        self.assertTrue(found)
        self.assertIn("b.py", found[0].path)


class RetrievalShapeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = _Workspace()
        self.service = self.workspace.service()

    def test_retrieve_returns_candidates_in_the_retrieval_contract(self) -> None:
        candidates = self.service.retrieve("alpha")
        self.assertTrue(candidates)
        self.assertTrue(all(item.source_type == "code" for item in candidates))

    def test_a_definition_outranks_a_mention_for_a_symbol_query(self) -> None:
        candidates = self.service.retrieve("alpha")
        # Definitions carry a structural score precisely so a comment mentioning the
        # name cannot outrank the definition on term overlap.
        self.assertTrue(any(item.structural_score for item in candidates))
        self.assertIn("def alpha", candidates[0].content)

    def test_symbol_queries_are_distinguished_from_descriptions(self) -> None:
        self.assertTrue(looks_like_symbol("alpha"))
        self.assertTrue(looks_like_symbol("Module.alpha"))
        self.assertFalse(looks_like_symbol("where is alpha defined?"))


if __name__ == "__main__":
    unittest.main()
