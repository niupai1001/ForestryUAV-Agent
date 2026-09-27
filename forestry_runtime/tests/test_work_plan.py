"""Phase 2 acceptance: a plan whose claims point at evidence.

The behaviours under test are the ones that make a plan more than prose:

* a condition that cannot be pointed at is refused, because an observation the Run
  did not make is not an observation;
* a candidate that does not state its precondition is refused, because nothing can
  then be checked against the inputs;
* a guide may be cited only after its body was returned. Listing the catalogue says
  which documents exist -- a Run cited guides it had never opened, and that is the
  claim this rule makes impossible to write down;
* changing the selected method requires a reason, and every revision is kept, so
  "why did the method change" is answered by the record.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import tempfile
import unittest
import uuid

from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.tools import ToolDefinition

from runtime.context import ContextCompiler
from runtime.lifecycle import Sessions
from runtime.plan import ObservationLedger, PlanError, PlanStore, observation_id
from runtime.capabilities.runtime import RuntimeTools
from runtime.workspace import WorkspaceRegistry


def _tool(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name, description="x", parameters_json_schema={"type": "object"}
    )


class _Toolbox:
    def __init__(self):
        self.owner = "alice"
        self.chat_id = "chat"

        class _Workspaces:
            @staticmethod
            def list_grants(owner, chat_id):
                return []

        self.workspaces = _Workspaces()

    @staticmethod
    def attachment_context():
        return []


class WorkPlanStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = ObservationLedger(self.root / "observations.json")
        self.observation = self.ledger.record(
            "inspect_file", {"path": "oam-02.tif"}, ok=True, summary="path=oam-02.tif",
        )
        self.store = PlanStore(self.root, self.ledger)

    def tearDown(self):
        self.temp.cleanup()

    def _candidate(self, **overrides):
        entry = {
            "id": "gray-otsu",
            "method": "Grayscale Otsu threshold",
            "precondition": "A single luminance band derived from the RGB image",
            "evidence": self.observation,
        }
        entry.update(overrides)
        return entry

    def test_an_observation_id_is_derived_from_the_call_not_a_counter(self):
        first = observation_id("fs_list", {"path": "."})
        second = observation_id("fs_list", {"path": "."})
        other = observation_id("fs_list", {"path": "sub"})
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertTrue(re.fullmatch(r"obs_[0-9a-f]{12}", first))

    def test_a_condition_without_evidence_is_refused(self):
        with self.assertRaises(PlanError) as caught:
            self.store.revise(inputs=[{"condition": "image is three-band RGB"}])
        self.assertEqual(caught.exception.failure_details["code"], "plan_input_without_evidence")

    def test_an_unmade_observation_cannot_be_cited(self):
        with self.assertRaises(PlanError) as caught:
            self.store.revise(inputs=[{
                "condition": "image is three-band RGB", "evidence": "obs_ffffffffffff",
            }])
        self.assertEqual(caught.exception.failure_details["code"], "plan_observation_unknown")

    def test_a_recorded_observation_can_be_cited(self):
        plan = self.store.revise(objective="Extract canopy cover from RGB", inputs=[{
            "condition": "three-band RGB, no NIR band declared",
            "evidence": self.observation,
        }])
        self.assertEqual(plan.revision, 1)
        self.assertEqual(plan.inputs[0]["evidence"], self.observation)
        self.assertIn(self.observation, plan.render())

    def test_a_candidate_without_a_precondition_is_refused(self):
        with self.assertRaises(PlanError) as caught:
            self.store.revise(candidates=[self._candidate(precondition="")])
        self.assertEqual(
            caught.exception.failure_details["code"], "plan_candidate_without_precondition"
        )

    def test_a_guide_catalogue_entry_is_not_evidence_of_the_guide(self):
        # The catalogue names documents; citing one without having read it is the
        # failure this refuses.
        with self.assertRaises(PlanError) as caught:
            self.store.revise(candidates=[self._candidate(evidence="guide://rgb-canopy-cover@1")])
        details = caught.exception.failure_details
        self.assertEqual(details["code"], "plan_document_not_read")
        self.assertEqual(details["citation"], "guide://rgb-canopy-cover@1")

    def test_a_guide_body_returned_by_a_tool_can_be_cited(self):
        self.ledger.record(
            "domain_guide", {"guide_id": "rgb-canopy-cover"}, ok=True,
            documents=["guide://rgb-canopy-cover@1"],
        )
        plan = self.store.revise(candidates=[
            self._candidate(evidence="guide://rgb-canopy-cover@1"),
        ])
        self.assertEqual(plan.candidates[0]["evidence"], "guide://rgb-canopy-cover@1")

    def test_a_search_hit_with_content_is_a_body_and_a_listing_is_not(self):
        self.ledger.record(
            "knowledge_search", {"query": "canopy"}, ok=True,
            documents=["knowledge://src_1/chunk_2#p4"],
        )
        plan = self.store.revise(candidates=[
            self._candidate(evidence="knowledge://src_1/chunk_2#p4"),
        ])
        self.assertTrue(plan.candidates)
        # A citation that was never returned is still refused.
        with self.assertRaises(PlanError):
            self.store.revise(candidates=[
                self._candidate(evidence="knowledge://src_1/chunk_9#p1"),
            ])

    def test_changing_the_selected_method_needs_a_reason(self):
        self.store.revise(candidates=[self._candidate(), self._candidate(
            id="exg-otsu", method="Excess Green index with Otsu",
            precondition="A visible green channel", evidence=self.observation,
        )])
        with self.assertRaises(PlanError) as caught:
            self.store.revise(selected="exg-otsu")
        self.assertEqual(
            caught.exception.failure_details["code"], "plan_selection_without_reason"
        )
        self.assertEqual(caught.exception.failure_details["previous"], "")

        revised = self.store.revise(
            selected="exg-otsu",
            reason="obs shows a green channel and no NIR, so the NDVI route is unavailable",
        )
        self.assertEqual(revised.selected, "exg-otsu")
        self.assertIn("no NIR", revised.selection_reason)
        self.assertIn("exg-otsu", revised.render())

    def test_selecting_an_unknown_candidate_is_refused(self):
        self.store.revise(candidates=[self._candidate()])
        with self.assertRaises(PlanError) as caught:
            self.store.revise(selected="invented", reason="because")
        self.assertEqual(caught.exception.failure_details["code"], "plan_unknown_candidate")
        self.assertEqual(caught.exception.failure_details["candidates"], ["gray-otsu"])

    def test_every_revision_is_kept(self):
        self.store.revise(objective="first")
        self.store.revise(inputs=[{
            "condition": "three-band RGB", "evidence": self.observation,
        }])
        history = self.store.history()
        self.assertEqual([entry["revision"] for entry in history], [1, 2])
        self.assertEqual(history[0]["objective"], "first")

    def test_the_rendered_plan_states_each_sections_evidence(self):
        self.store.revise(
            objective="Deliver a canopy cover estimate",
            outputs=["canopy_mask.tif", "summary.json"],
            inputs=[{"condition": "0.1 m RGB, no NIR", "evidence": self.observation}],
            candidates=[self._candidate()],
            open_questions=[{
                "question": "Does the canopy mask include grass?",
                "blocks": "candidate choice", "resolve_with": "domain_guide",
            }],
            acceptance=["mask is 0/1 with 255 invalid", "reported ratio matches the mask"],
            selected="gray-otsu", reason="only luminance is available",
        )
        text = self.store.load().render()
        for fragment in (
            "Deliver a canopy cover estimate", "canopy_mask.tif", self.observation,
            "gray-otsu", "requires", "Does the canopy mask include grass?",
            "0/1 with 255 invalid", "only luminance",
        ):
            self.assertIn(fragment, text)


class WorkPlanCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sessions = Sessions(self.root)
        self.chat_id = str(uuid.uuid4())
        self.sessions.create("alice", self.chat_id)
        self.store = self.sessions.acquire("alice", self.chat_id)
        self.sessions.release(self.chat_id)
        self.registry = WorkspaceRegistry(self.root)
        self.box = RuntimeTools(self.store, "alice", [], self.registry, "")

    def tearDown(self):
        self.temp.cleanup()

    def test_a_tool_result_carries_the_observation_id_the_plan_can_cite(self):
        listed = self.box.execute("fs_list", {"path": "."})
        self.assertTrue(listed["ok"], listed)
        identifier = listed["data"]["observation_id"]
        self.assertTrue(re.fullmatch(r"obs_[0-9a-f]{12}", identifier))

        planned = self.box.execute("work_plan", {
            "objective": "Check the workspace",
            "inputs": [{"condition": "the workspace is empty", "evidence": identifier}],
        })
        self.assertTrue(planned["ok"], planned)
        self.assertEqual(planned["data"]["revision"], 1)

    def test_a_failed_call_is_also_an_observation(self):
        failed = self.box.execute("fs_read", {"path": "absent.txt"})
        self.assertFalse(failed["ok"])
        identifier = failed["observation_id"]
        recorded = self.box.ledger.known()["observations"][identifier]
        self.assertFalse(recorded["ok"])
        self.assertEqual(recorded["tool"], "fs_read")

    def test_reading_a_guide_body_makes_it_citable(self):
        self.box.execute("fs_read", {"path": "absent.txt"})  # any first observation
        catalogue = self.box.execute("domain_guide", {"query": "树冠 覆盖率 RGB"})
        self.assertTrue(catalogue["ok"], catalogue)
        match = catalogue["data"]["matches"][0]
        refused = self.box.execute("work_plan", {
            "candidates": [{
                "id": "rgb-route", "method": "RGB canopy route",
                "precondition": "a visible-band image",
                "evidence": match["citation"],
            }],
        })
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["failure"]["code"], "plan_document_not_read")

        opened = self.box.execute("domain_guide", {"guide_id": match["id"]})
        self.assertTrue(opened["ok"], opened)
        accepted = self.box.execute("work_plan", {
            "candidates": [{
                "id": "rgb-route", "method": "RGB canopy route",
                "precondition": "a visible-band image",
                "evidence": opened["data"]["citation"],
            }],
        })
        self.assertTrue(accepted["ok"], accepted)
        self.assertEqual(accepted["data"]["plan"]["candidates"][0]["id"], "rgb-route")

    def test_reading_the_plan_does_not_create_a_revision(self):
        self.box.execute("fs_list", {"path": "."})
        identifier = self.box.execute("fs_list", {"path": "."})["data"]["observation_id"]
        self.box.execute("work_plan", {
            "objective": "Deliver canopy cover",
            "inputs": [{"condition": "workspace holds one file", "evidence": identifier}],
        })
        read = self.box.execute("work_plan", {"include_history": True})
        self.assertTrue(read["ok"], read)
        self.assertEqual(read["data"]["revision"], 1)
        self.assertEqual(len(read["data"]["history"]), 1)
        self.assertEqual(read["data"]["plan"]["objective"], "Deliver canopy cover")

    def test_the_plan_is_shown_in_the_request_context(self):
        self.box.execute("fs_list", {"path": "."})
        identifier = self.box.execute("fs_list", {"path": "."})["data"]["observation_id"]
        self.box.execute("work_plan", {
            "objective": "Deliver canopy cover",
            "inputs": [{"condition": "workspace holds one file", "evidence": identifier}],
            "acceptance": ["the mask is 0/1"],
        })

        toolbox = _Toolbox()
        toolbox.current_plan_text = self.box.current_plan_text
        toolbox._plan_store = self.box._plan_store
        compiler = ContextCompiler(toolbox, project_context=lambda: {
            "project_id": "p", "content": "x", "instruction_revision": 1,
        })
        parts, manifest, _ = compiler.compile(
            "问题", [_tool("domain_guide"), _tool("work_plan")],
            len([ModelRequest(parts=[UserPromptPart(content="问题")])]),
        )
        self.assertTrue(any("work plan" in part.casefold() for part in parts))
        self.assertTrue(any("Deliver canopy cover" in part for part in parts))
        self.assertEqual(manifest["plan_revision"], 1)
        self.assertTrue(manifest["plan_present"])
        # The catalogue still precedes the plan, and the volatile facts still close.
        self.assertIn("Domain guide catalogue", parts[1])
        self.assertIn("Runtime facts", parts[-1])

    def test_a_request_without_a_plan_carries_no_plan_section(self):
        toolbox = _Toolbox()
        toolbox.current_plan_text = self.box.current_plan_text
        compiler = ContextCompiler(toolbox, project_context=lambda: {})
        parts, manifest, _ = compiler.compile("问题", [_tool("fs_read")], 1)
        self.assertFalse(any("work plan" in part.casefold() for part in parts))
        self.assertFalse(manifest["plan_present"])


if __name__ == "__main__":
    unittest.main()
