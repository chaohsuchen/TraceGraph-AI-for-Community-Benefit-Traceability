import json
import unittest
from unittest.mock import Mock

from tracegraph.providers import ClaudeReconciliationJudge, ClaudeWebResearcher, ProviderError
from tracegraph.reconcile import EvidenceReconciler, ReconciliationError
from tests.test_providers import _json_message, _search_message, _structured_finding


_CONTEXT = {
    "commitment": {"description": "The authority promised five overnight shuttle routes.",
                   "notes": "private workbook note"},
    "organization": {"id": "org-private-id", "name": "Transit Authority"},
}
_VERIFICATION_DATE = "2026-11-07"
_ASSESSMENT = {
    "label": "PARTIALLY_CORROBORATED", "verdict": "Some routes are running.",
    "reasoning": "The published route list records three of the promised five routes.",
}


def _support(page, excerpt):
    return {
        "source": {"url": f"https://transit.example/{page}", "title": page},
        "excerpt": excerpt, "summary": None, "locator": "Section 1",
        "status": "Published record", "explanation": "Direct reporting.",
        "retrieved_at": "2026-11-07T12:00:00+00:00",
    }


def _snapshot(*findings, relations=(), revision=4):
    return {"revision": revision, "findings": list(findings), "relations": list(relations), "assessment": None}


def _research(*evidence):
    return {"verification_date": _VERIFICATION_DATE, "evidence": list(evidence)}


def _assignment(observation_id, finding_id, claim=""):
    return {"observation_id": observation_id, "finding_id": finding_id, "claim": claim}


def _link(kind, from_id, to_id, *, status=None, preferred_id=None, support_ids=()):
    return {"kind": kind, "from_id": from_id, "to_id": to_id, "status": status,
            "preferred_id": preferred_id, "support_ids": list(support_ids), "reasoning": "Same scope and period."}


def _plan(assignments=(), relations=(), assessment=None):
    return {"assignments": list(assignments), "relations": list(relations),
            "assessment": assessment or dict(_ASSESSMENT)}


def _provider_plan(assignments=(), relations=(), *, new_claims=None):
    return _json_message({
        **_plan(relations=relations), "new_claims": new_claims or {},
        "assignments": {row["observation_id"]: row["finding_id"] for row in assignments},
    })


def _criterion(name="Operate five overnight routes", *, status="UNVERIFIED", scope=(), promise=(), delivery=(),
               gap=None, timing=""):
    if gap is None:
        gap = "" if status == "DELIVERED" else "Obtain the completed service records."
    return {
        "criterion": name, "scope_criteria": list(scope), "promise_citation_ids": list(promise),
        "delivery_citation_ids": list(delivery),
        "delivery_status": status, "explanation": "Only use outcomes supported by the source passages.",
        "gap": gap, "timing_requirement": timing,
    }


def _final_assessment(*criteria, label="UNVERIFIED", not_yet_due=()):
    return {**_ASSESSMENT, "label": label, "criteria": list(criteria),
            "not_yet_due": list(not_yet_due),
            "refutation": []}


def _delivery_check(criterion, explanation, *, support=(), gap="", fulfilled_part=None, timing="", timing_support=()):
    if fulfilled_part is None:
        fulfilled_part = explanation if support else ""
    return {"criterion": criterion, "explanation": explanation, "gap": gap,
            "fulfilled_part": fulfilled_part, "timing_requirement": timing,
            "timing_support_ids": list(timing_support),
            "delivery_support": [{"citation_id": citation_id, "quote": quote} for citation_id, quote in support]}


def _observed_outcomes(*quotes, planned_terms=(), timing=()):
    return _json_message({
        "observed_outcomes": [{"citation_id": citation_id, "quote": quote} for citation_id, quote in quotes],
        "planned_terms": [{"citation_id": citation_id, "quote": quote} for citation_id, quote in planned_terms],
        "observed_timing": [{"citation_id": citation_id, "quote": quote} for citation_id, quote in timing],
    })


def _delivery_review(*checks, scope_gap=""):
    return _json_message({"scope_gap": scope_gap, "checks": {
        f"v{index}": {key: value for key, value in check.items() if key != "criterion"}
        for index, check in enumerate(checks, 1)
    }})


class ReconciliationTests(unittest.TestCase):
    def test_repeated_excerpt_is_deduplicated_and_paraphrases_keep_existing_id(self):
        support = _support("board-report", "Three routes began operating.")
        prior = _snapshot({"id": "f-1", "claim": "Three routes opened", "supports": [support]})
        newer = _support("city-audit", "The Authority began service on 3 routes.")

        def judge(context, snapshot, observations, reviewed_pairs):
            self.assertEqual([item["support"] for item in observations], [newer])
            return _plan([_assignment("o1", "f-1")])

        def assess(context, state):
            self.assertEqual(len(state["findings"]), 1)
            self.assertEqual(state["findings"][0]["supports"], [support, newer])
            return dict(_ASSESSMENT)

        duplicate = dict(support, retrieved_at="2026-11-07T14:00:00+00:00")
        result = EvidenceReconciler(judge, assessor=assess).reconcile(
            _CONTEXT, prior, _research(duplicate, newer, newer)
        )
        self.assertEqual(result["expected_revision"], 4)
        self.assertEqual(result["verification_date"], _VERIFICATION_DATE)
        self.assertEqual(result["findings"][0]["id"], "f-1")
        self.assertEqual(result["label"], "PARTIALLY_CORROBORATED")
        self.assertEqual(prior["findings"][0]["supports"], [support])

    def test_exact_claim_reuses_canonical_finding_even_if_model_creates_alias(self):
        prior = _snapshot({"id": "f-1", "claim": "Three routes opened", "supports": [
            _support("board-report", "Three routes began operating.")
        ]})
        judge = Mock(return_value=_plan([_assignment("o1", "new:1", "THREE routes   opened")]))
        result = EvidenceReconciler(judge).reconcile(
            _CONTEXT, prior, _research(_support("city-audit", "Three routes operate."))
        )
        self.assertEqual([finding["id"] for finding in result["findings"]], ["f-1"])
        self.assertEqual(len(result["findings"][0]["supports"]), 2)

    def test_progress_points_from_prior_to_new_milestone(self):
        prior = _snapshot({"id": "f-1", "claim": "The Board approved a shuttle pilot.", "supports": [
            _support("approval", "The Board approved the pilot on June 4.")
        ]})
        judge = Mock(return_value=_plan(
            [_assignment("o1", "new:1", "Three routes entered service in October.")],
            [_link("PROGRESS", "f-1", "new:1")],
        ))
        result = EvidenceReconciler(judge).reconcile(
            _CONTEXT, prior, _research(_support("launch", "Three routes entered service in October."))
        )
        later = result["findings"][1]["id"]
        self.assertEqual(result["relations"], [{
            "kind": "PROGRESS", "from_id": "f-1", "to_id": later, "status": None,
            "preferred_id": None, "reasoning": "Same scope and period.",
        }])

    def test_rejects_cycles_and_invented_ids(self):
        prior = _snapshot(
            {"id": "f-1", "claim": "One route entered service.", "supports": [_support("one", "First route.")]},
            {"id": "f-2", "claim": "Three routes entered service.", "supports": [_support("three", "Three routes.")]},
            relations=[_link("PROGRESS", "f-1", "f-2")],
        )
        judge = Mock()
        reconciler = EvidenceReconciler(judge)
        for edge, failure in [
            (_link("PROGRESS", "f-2", "f-1"), "form a cycle"),
            (_link("PROGRESS", "f-2", "invented"), "unknown finding"),
        ]:
            judge.return_value = _plan(relations=[edge])
            with self.assertRaisesRegex(ReconciliationError, failure):
                reconciler.reconcile(_CONTEXT, prior, _research())

    def test_both_conflict_sides_survive_and_review_is_reused_on_retry(self):
        prior = _snapshot({"id": "f-1", "claim": "Three routes were active on November 1.", "supports": [
            _support("authority", "We operated three routes November 1.")
        ]})
        contradiction = _support("notice", "All routes had suspended service on November 1.")
        corroboration = _support("audit", "Vehicle logs show three routes in service November 1.")

        def judge(context, snapshot, observations, pairs):
            self.assertEqual(len(observations), 1)
            if not pairs:
                return _plan(
                    [_assignment("o1", "new:1", "No routes were active on November 1.")],
                    [_link("CONFLICT", "f-1", "new:1", status="UNRESOLVED")],
                )
            self.assertTrue(observations[0]["from_review"])
            return _plan(
                [_assignment("o1", "f-1")],
                [_link("CONFLICT", pairs[0]["to_id"], "f-1", status="RESOLVED",
                       preferred_id="f-1", support_ids=["o1"])],
            )

        followup = Mock(return_value=[corroboration])
        assessor = Mock(return_value=_ASSESSMENT)
        verify_conflicts = Mock(return_value=[{
            "id": "c1", "contradicts": True, "left_quote": "We operated three routes November 1.",
            "right_quote": "All routes had suspended service on November 1.",
        }])
        reconciler = EvidenceReconciler(
            judge, followup=followup, assessor=assessor, verify_conflicts=verify_conflicts,
        )
        cache = {}
        first = reconciler.reconcile(_CONTEXT, prior, _research(contradiction), followup_cache=cache)
        second = reconciler.reconcile(
            _CONTEXT, {**prior, "revision": 5}, _research(contradiction), followup_cache=cache
        )
        self.assertEqual(second["expected_revision"], 5)
        self.assertEqual(first["findings"], second["findings"])
        self.assertEqual([len(finding["supports"]) for finding in first["findings"]], [2, 1])
        edge = first["relations"][0]
        self.assertEqual(edge["status"], "RESOLVED")
        self.assertEqual(edge["preferred_id"], "f-1")
        self.assertIn("https://transit.example/audit", edge["reasoning"])
        followup.assert_called_once()
        self.assertEqual(verify_conflicts.call_count, 2)
        self.assertEqual(assessor.call_args.args[1]["relations"], second["relations"])

    def test_no_new_review_evidence_keeps_conflict_unresolved(self):
        support = _support("notice", "All routes had suspended service on November 1.")
        judge = Mock(return_value=_plan(
            [_assignment("o1", "new:1", "Three routes were active on November 1."),
             _assignment("o2", "new:2", "No routes were active on November 1.")],
            [_link("CONFLICT", "new:1", "new:2", status="UNRESOLVED")],
            {"label": "CONFLICTING", "verdict": "Service status conflicts.", "reasoning": "The two accounts disagree."},
        ))
        followup = Mock(return_value=[support])
        result = EvidenceReconciler(judge, followup=followup).reconcile(
            _CONTEXT, _snapshot(), _research(_support("operator", "Three were running November 1."), support)
        )
        self.assertEqual(result["label"], "CONFLICTING")
        self.assertEqual(result["relations"][0]["status"], "UNRESOLVED")
        self.assertIsNone(result["relations"][0]["preferred_id"])
        judge.assert_called_once()

    def test_refresh_can_resolve_an_existing_conflict_after_a_new_endpoint_excerpt(self):
        first = _support("operator", "Three routes ran on November 1.")
        second = _support("notice", "No routes ran on November 1.")
        findings = [{"id": name, "claim": support["excerpt"], "supports": [support]}
                    for name, support in (("left", first), ("right", second))]
        edge = _link("CONFLICT", "left", "right", status="UNRESOLVED")
        prior = _snapshot(*findings, relations=[edge])

        for preferred in ("left", "right"):
            with self.subTest(preferred=preferred):
                count = "three" if preferred == "left" else "no"
                refreshed = _support("dispatch", f"The dispatch report records {count} routes operating on November 1.")
                review = _support("audit", f"Independent vehicle logs confirm {count} routes operated on November 1.")

                def judge(context, snapshot, observations, pairs):
                    assignments = [_assignment("o1", preferred)] if observations else []
                    if not pairs:
                        return _plan(assignments)
                    self.assertEqual([(pair["from_id"], pair["to_id"]) for pair in pairs], [("left", "right")])
                    self.assertEqual([item["support"] for item in observations], [review])
                    return _plan(assignments, [_link("CONFLICT", "left", "right", status="RESOLVED",
                                                     preferred_id=preferred, support_ids=["o1"])])

                verify = Mock(return_value=[{"id": "c1", "contradicts": True, "left_quote": first["excerpt"],
                                             "right_quote": second["excerpt"]}])
                followup = Mock(return_value=[review])
                reconciler = EvidenceReconciler(judge, followup=followup, verify_conflicts=verify)
                cache = {}
                result = reconciler.reconcile(_CONTEXT, prior, _research(refreshed), followup_cache=cache)
                self.assertEqual(result["relations"][0]["status"], "RESOLVED")
                self.assertEqual(result["relations"][0]["preferred_id"], preferred)

                concurrent = [{**finding, "supports": [*finding["supports"], refreshed]}
                              if finding["id"] == preferred else finding for finding in findings]
                retry = reconciler.reconcile(_CONTEXT, _snapshot(*concurrent, relations=[edge], revision=5),
                                             _research(refreshed), followup_cache=cache)
                followup.assert_called_once()
                self.assertEqual(retry["relations"][0]["status"], "UNRESOLVED")
                self.assertEqual(retry["findings"], concurrent)

    def test_refresh_leaves_conflict_review_idle_without_a_new_endpoint_excerpt(self):
        first = _support("operator", "Three routes ran on November 1.")
        second = _support("notice", "No routes ran on November 1.")
        cases = (
            ("exact duplicate", "UNRESOLVED", first, []),
            ("quote variant", "UNRESOLVED", {**first, "excerpt": "THREE routes  ran on November 1.",
                                              "locator": "Page 2"}, [_assignment("o1", "left")]),
            ("summary only", "UNRESOLVED", {**first, "excerpt": None, "summary": "A separate abstract."},
             [_assignment("o1", "left")]),
            ("unrelated finding", "UNRESOLVED", _support("second-report", "Two routes had new buses."),
             [_assignment("o1", "new:1", "Two routes had new buses.")]),
            ("already resolved", "RESOLVED", _support("audit", "Vehicle logs confirm three routes."),
             [_assignment("o1", "left")]),
        )
        for case, status, support, assignments in cases:
            with self.subTest(case=case):
                prior = _snapshot({"id": "left", "claim": first["excerpt"], "supports": [first]},
                                  {"id": "right", "claim": second["excerpt"], "supports": [second]},
                                  relations=[_link("CONFLICT", "left", "right", status=status,
                                                   preferred_id="left" if status == "RESOLVED" else None)])
                followup = Mock()
                judge = Mock(return_value=_plan(assignments))
                result = EvidenceReconciler(judge, followup=followup).reconcile(_CONTEXT, prior, _research(support))
                followup.assert_not_called()
                self.assertEqual(result["relations"][0]["status"], status)

    def test_verified_unresolved_conflict_blocks_full_corroboration(self):
        delivered = "All five routes operated on November 1."
        disputed = "No routes operated on November 1."
        assessment = {"label": "CORROBORATED", "verdict": "All five routes ran.",
                      "reasoning": "The operator reports full delivery."}
        judge = Mock(return_value=_plan(
            [_assignment("o1", "new:1", delivered), _assignment("o2", "new:2", disputed)],
            [_link("CONFLICT", "new:1", "new:2", status="UNRESOLVED")], assessment,
        ))
        check = {"id": "c1", "contradicts": True, "left_quote": delivered, "right_quote": disputed}
        evidence = _research(_support("operator", delivered), _support("audit", disputed))

        for verifier, expected in ((Mock(return_value=[check]), "CONFLICTING"), (None, "CORROBORATED")):
            with self.subTest(expected=expected):
                result = EvidenceReconciler(judge, verify_conflicts=verifier).reconcile(
                    _CONTEXT, _snapshot(), evidence,
                )
                self.assertEqual(result["label"], expected)
                self.assertEqual(result["relations"][0]["status"], "UNRESOLVED")
                if verifier is not None:
                    self.assertEqual(result["verdict"], "Verified sources disagree.")
                    self.assertIn("unresolved", result["reasoning"])

    def test_count_conflict_can_coexist_with_supported_partial_delivery(self):
        assessment = {"label": "PARTIALLY_CORROBORATED", "verdict": "Some service is confirmed.",
                      "reasoning": "Both sources report at least two routes. The exact count is disputed."}
        judge = Mock(return_value=_plan(
            [_assignment("o1", "new:1", "Three routes operated."), _assignment("o2", "new:2", "Two routes operated.")],
            [_link("CONFLICT", "new:1", "new:2", status="UNRESOLVED")], assessment,
        ))
        verifier = Mock(return_value=[{"id": "c1", "contradicts": True, "left_quote": "Three routes operated.",
                                       "right_quote": "Two routes operated."}])
        result = EvidenceReconciler(judge, verify_conflicts=verifier).reconcile(_CONTEXT, _snapshot(), _research(
            _support("notice", "Three routes operated."), _support("audit", "Two routes operated."),
        ))
        self.assertEqual(result["label"], "PARTIALLY_CORROBORATED")
        self.assertEqual(result["relations"][0]["status"], "UNRESOLVED")

    def test_cannot_resolve_conflict_without_targeted_evidence_for_preferred_side(self):
        first = _support("operator", "Three were running November 1.")
        second = _support("notice", "No routes were running November 1.")
        assignments = [_assignment("o1", "new:1", "Three operating"), _assignment("o2", "new:2", "None operating")]
        judge = Mock(return_value=_plan(assignments, [
            _link("CONFLICT", "new:1", "new:2", status="RESOLVED", preferred_id="new:1", support_ids=["o1"]),
        ]))
        with self.assertRaisesRegex(ReconciliationError, "after targeted review"):
            EvidenceReconciler(judge).reconcile(_CONTEXT, _snapshot(), _research(first, second))

        def unresolved_then_forged(context, snapshot, observations, pairs):
            if not pairs:
                return _plan(assignments, [_link("CONFLICT", "new:1", "new:2", status="UNRESOLVED")])
            left, right = pairs[0]["from_id"], pairs[0]["to_id"]
            return _plan([_assignment("o1", right)], [
                _link("CONFLICT", left, right, status="RESOLVED", preferred_id=left, support_ids=["o1"]),
            ])

        followup = Mock(return_value=[_support("audit", "Independent records support none operating.")])
        with self.assertRaisesRegex(ReconciliationError, "must support the preferred finding"):
            EvidenceReconciler(unresolved_then_forged, followup=followup).reconcile(
                _CONTEXT, _snapshot(), _research(first, second)
            )

    def test_rejects_omitted_and_invented_observations(self):
        judge = Mock()
        reconciler = EvidenceReconciler(judge)
        result = _research(_support("operator", "Three were running November 1."))
        judge.return_value = _plan()
        with self.assertRaisesRegex(ReconciliationError, "Every new observation"):
            reconciler.reconcile(_CONTEXT, _snapshot(), result)
        judge.return_value = _plan([_assignment("o2", "new:1", "Forged claim")])
        with self.assertRaisesRegex(ReconciliationError, "unknown or assigned more than once"):
            reconciler.reconcile(_CONTEXT, _snapshot(), result)

    def test_artemis_orbital_test_does_not_refute_a_lunar_landing_promise(self):
        context = {"commitment": {
            "description": "NASA pledged to land the first woman and first person of color on the Moon through Artemis.",
        }}
        test_quote = "The mission will undertake a series of challenging tests in Earth orbit in 2027"
        announcement = _support("artemis-iii", test_quote)
        wording = _support("nasa-statement", "the change in language does not indicate a change in crew assignments")
        prior_edge = _link("CONFLICT", "wording", "test", status="RESOLVED", preferred_id="test")
        findings = [
            {"id": "wording", "claim": "NASA said that the wording change did not indicate a crew change.", "supports": [wording]},
            {"id": "test", "claim": "NASA announced the Artemis III crew for a test in Earth orbit.", "supports": [announcement]},
        ]
        verdict = {
            "label": "REFUTED", "verdict": "NASA abandoned the landing promise.",
            "reasoning": "The Artemis III crew was announced for the orbital test.",
            "refutation": {"basis": "DIRECT_DISPROOF", "finding_id": "test", "quote": test_quote},
        }
        for existing in (False, True):
            with self.subTest(existing=existing):
                candidate = _link("CONFLICT", "wording", "test", status="UNRESOLVED")
                judge = Mock(return_value=_plan(relations=[] if existing else [candidate]))
                compare = Mock(return_value=[{"id": "c1", "contradicts": False,
                                             "left_quote": "", "right_quote": ""}])
                verify_refutation = Mock(return_value=False)
                followup = Mock()
                assess = Mock(return_value=verdict)
                reconciler = EvidenceReconciler(
                    judge, followup=followup, assessor=assess,
                    verify_conflicts=compare, verify_refutation=verify_refutation,
                )
                graph = reconciler.reconcile(
                    context, _snapshot(*findings, relations=[prior_edge] if existing else []), _research(),
                )
                self.assertEqual(graph["label"], "UNVERIFIED")
                self.assertEqual(graph["findings"], findings)
                self.assertEqual(graph["relations"], [])
                self.assertEqual(graph["dismissed_conflicts"], [("wording", "test")] if existing else [])
                self.assertEqual(judge.call_args.args[1]["relations"], [])
                self.assertEqual(assess.call_args.args[1]["relations"], [])
                followup.assert_not_called()
                verify_refutation.assert_called_once_with(context, announcement, "DIRECT_DISPROOF", test_quote)

    def test_fabricated_conflict_quote_does_not_trigger_research_or_conflicting_verdict(self):
        first = {"id": "one", "claim": "One route ran.", "supports": [_support("report", "One route ran.")]}
        second = {"id": "two", "claim": "Two routes ran.", "supports": [_support("audit", "Two routes ran.")]}
        assessment = {"label": "CONFLICTING", "verdict": "Disputed.", "reasoning": "The reports differ."}
        judge = Mock(return_value=_plan(
            relations=[_link("CONFLICT", "one", "two", status="UNRESOLVED")], assessment=assessment,
        ))
        compare = Mock(return_value=[{
            "id": "c1", "contradicts": True, "left_quote": "One route ran.", "right_quote": "An invented quote.",
        }])
        followup = Mock()
        graph = EvidenceReconciler(judge, verify_conflicts=compare, followup=followup).reconcile(
            _CONTEXT, _snapshot(first, second), _research(),
        )
        self.assertEqual(graph["label"], "UNVERIFIED")
        self.assertEqual(graph["relations"], [])
        followup.assert_not_called()

    def test_refutation_requires_a_real_quote_and_preserves_verified_direct_evidence(self):
        quote = "The authority has withdrawn its commitment to provide five overnight shuttle routes."
        notice = _support("withdrawal", quote)
        finding = {"id": "withdrawal", "claim": "The authority withdrew its commitment.", "supports": [notice]}
        assess = Mock()
        verify = Mock(return_value=True)
        reconciler = EvidenceReconciler(Mock(return_value=_plan()), assessor=assess, verify_refutation=verify)
        verdict = {"label": "REFUTED", "verdict": "The pledge was withdrawn.", "reasoning": "The authority withdrew it."}
        for proof in ({}, {"basis": "OFFICIAL_WITHDRAWAL", "finding_id": "withdrawal", "quote": "Invented."}):
            assess.return_value = {**verdict, "refutation": proof}
            self.assertEqual(reconciler.reconcile(_CONTEXT, _snapshot(finding), _research())["label"], "UNVERIFIED")
        verify.assert_not_called()
        assess.return_value = {
            **verdict, "refutation": {"basis": "OFFICIAL_WITHDRAWAL", "finding_id": "withdrawal", "quote": quote},
        }
        graph = reconciler.reconcile(_CONTEXT, _snapshot(finding), _research())
        verify.assert_called_once_with(_CONTEXT, notice, "OFFICIAL_WITHDRAWAL", quote)
        self.assertEqual(graph["label"], "REFUTED")
        self.assertIn(quote, graph["reasoning"])
        self.assertIn(notice["source"]["url"], graph["reasoning"])

    def test_refutation_can_use_a_second_stronger_quoted_passage(self):
        first = _support("update", "The authority considered changing its service plan.")
        second = _support("withdrawal", "The authority has withdrawn its promise to run five overnight routes.")
        snapshot = _snapshot(
            {"id": "update", "claim": first["excerpt"], "supports": [first]},
            {"id": "withdrawal", "claim": second["excerpt"], "supports": [second]},
        )
        value = {"label": "REFUTED", "verdict": "The pledge was withdrawn.", "reasoning": "An official notice.",
                 "refutation": [{"basis": "OFFICIAL_WITHDRAWAL", "finding_id": id_, "quote": support["excerpt"]}
                                 for id_, support in (("update", first), ("withdrawal", second))]}
        verify = Mock(side_effect=[False, True])
        result = EvidenceReconciler(Mock(return_value=_plan()), assessor=Mock(return_value=value),
                                    verify_refutation=verify).reconcile(
            _CONTEXT, snapshot, _research(),
        )
        self.assertEqual(result["label"], "REFUTED")
        self.assertIn(second["excerpt"], result["reasoning"])
        self.assertEqual(verify.call_count, 2)


class ClaudeReconciliationTests(unittest.TestCase):
    def test_claude_relation_schema_uses_strings_and_restores_nulls_for_storage(self):
        client = Mock()
        client.create.return_value = _provider_plan(relations=[
            _link("PROGRESS", "earlier", "later", status="", preferred_id=""),
            _link("CONFLICT", "earlier", "other", status="UNRESOLVED", preferred_id=""),
            _link("CONFLICT", "later", "other", status="RESOLVED", preferred_id="later"),
        ])
        snapshot = _snapshot(*({"id": id_, "claim": id_, "supports": []}
                               for id_ in ("earlier", "later", "other")))
        plan = ClaudeReconciliationJudge(client)(_CONTEXT, snapshot, [], [])
        schema = client.create.call_args.kwargs["output_config"]["format"]["schema"]
        fields = schema["properties"]["relations"]["items"]["properties"]
        self.assertEqual(fields["status"], {"type": "string", "enum": ["", "UNRESOLVED", "RESOLVED"]})
        self.assertEqual(fields["preferred_id"], {"type": "string"})
        self.assertEqual(
            [(edge["status"], edge["preferred_id"]) for edge in plan["relations"]],
            [(None, None), ("UNRESOLVED", None), ("RESOLVED", "later")],
        )

    def test_assessor_receives_only_the_normalized_evidence_and_public_context(self):
        client = Mock()

        def respond(**request):
            data = json.loads(request["messages"][0]["content"])
            if "observations" in data:
                return _provider_plan([{"observation_id": "o1", "finding_id": "new:1"}],
                                      new_claims={"new:1": "Three routes opened."})
            if "excerpts" in data:
                return _observed_outcomes(("c1", "Three routes opened."))
            if "check_conditions" in data:
                check = _delivery_check("Operate five overnight routes", "Three routes opened.",
                                        support=[("c1", "Three routes opened.")],
                                        gap="Delivery of two routes remains unverified.")
                return _delivery_review(check)
            return _json_message(_final_assessment(
                _criterion(status="PARTIAL", delivery=("c1",)), label="PARTIALLY_CORROBORATED",
            ))

        client.create.side_effect = respond
        judge = ClaudeReconciliationJudge(client)
        evidence = _support("board", "Three routes opened.")
        evidence["source"]["annotator"] = "private annotator"
        result = EvidenceReconciler(judge, assessor=judge.assess).reconcile(
            _CONTEXT, _snapshot(), _research(evidence)
        )
        self.assertEqual(result["label"], _ASSESSMENT["label"])
        planner, assessor, classifier, verifier = [json.loads(call.kwargs["messages"][0]["content"])
                                                   for call in client.create.call_args_list]
        self.assertEqual(len(planner["observations"]), 1)
        self.assertEqual(planner["new_finding_ids"], ["new:1"])
        schema = client.create.call_args_list[0].kwargs["output_config"]["format"]["schema"]
        assignment = schema["properties"]["assignments"]
        self.assertEqual(assignment["required"], ["o1"])
        self.assertEqual(assignment["properties"]["o1"]["enum"], ["new:1"])
        self.assertEqual(list(assessor["citations"]), ["c1"])
        self.assertEqual(assessor["citations"]["c1"]["excerpt"], "Three routes opened.")
        self.assertEqual(assessor["citations"], verifier["citations"])
        self.assertEqual(classifier, {"excerpts": {"c1": "Three routes opened."}})
        self.assertEqual(verifier["observed_outcomes"], [{"citation_id": "c1", "quote": "Three routes opened."}])
        self.assertEqual(verifier["observed_timing"], {})
        self.assertNotIn("explanation", assessor["citations"]["c1"])
        for body in (planner, assessor, classifier, verifier):
            serialized = json.dumps(body)
            self.assertNotIn("private workbook note", serialized)
            self.assertNotIn("private annotator", serialized)
            self.assertNotIn("org-private-id", serialized)
        for body in (assessor, verifier):
            self.assertNotIn("observations", body)
            self.assertNotIn("findings", body)
            self.assertNotIn("relations", body)

    def test_assessor_anchors_scope_in_the_original_passage_instead_of_old_interpretation(self):
        client = Mock()
        client.create.return_value = _json_message(_final_assessment(_criterion(scope=("s1",))))
        original = _support("original", "The authority promises five overnight routes.")
        context = {"commitment": {
            "description": "The pledge was formally abandoned.",
            "milestone": "A separate pilot was delayed.",
            "original_promise": {"source": original["source"], "excerpt": original["excerpt"]},
        }}
        ClaudeReconciliationJudge(client).assess(context, _snapshot())
        payload = json.loads(client.create.call_args.kwargs["messages"][0]["content"])
        target = payload["target"]
        self.assertEqual(target["original_text"], original["excerpt"])
        self.assertEqual(payload["requested_criteria"], {"s1": original["excerpt"]})
        self.assertNotIn("description", target)
        self.assertNotIn("milestone", target)

    def test_assessor_ignores_scope_ids_when_a_refresh_supplies_no_anchors(self):
        client = Mock()
        client.create.return_value = _json_message(_final_assessment(_criterion(scope=("invented",))))
        result = ClaudeReconciliationJudge(client).assess(_CONTEXT, _snapshot())
        self.assertEqual(result["criteria"][0]["scope_criteria"], [])
        self.assertEqual(client.create.call_count, 1)

    def test_assessor_retries_malformed_structured_output_once(self):
        client = Mock()
        client.create.side_effect = [
            {"stop_reason": "end_turn", "content": [{"type": "text", "text": "{"}]},
            _json_message(_final_assessment(_criterion())),
        ]
        self.assertEqual(ClaudeReconciliationJudge(client).assess(_CONTEXT, _snapshot())["label"], "UNVERIFIED")
        self.assertEqual(client.create.call_count, 2)

    def test_reconciliation_assignments_share_one_claim_definition_per_new_alias(self):
        first = _support("opening", "Three routes began operating.")
        second = _support("audit", "The board opened three routes.")
        observations = [{"id": id_, "from_review": False, "support": support}
                        for id_, support in (("o1", first), ("o2", second))]
        client = Mock()
        client.create.return_value = _provider_plan(
            [{"observation_id": id_, "finding_id": "new:1"} for id_ in ("o1", "o2")],
            new_claims={"new:1": "Three routes are open.", "new:2": ""},
            relations=[
                _link("PROGRESS", "earlier", "new:1", status="", preferred_id=""),
                _link("PROGRESS", "new:1", "new:2", status="", preferred_id=""),
            ],
        )
        snapshot = _snapshot({"id": "earlier", "claim": "Routes were announced.", "supports": []})
        plan = ClaudeReconciliationJudge(client)(_CONTEXT, snapshot, observations, [])
        self.assertEqual([row["claim"] for row in plan["assignments"]], ["Three routes are open."] * 2)
        self.assertEqual([(edge["from_id"], edge["to_id"]) for edge in plan["relations"]],
                         [("earlier", "new:1")])
        schema = client.create.call_args.kwargs["output_config"]["format"]["schema"]
        self.assertEqual(schema["properties"]["new_claims"]["required"], ["new:1", "new:2"])

    def test_assessor_derives_the_label_when_delivery_is_unverified(self):
        client = Mock()
        seed = "Operate five overnight routes"
        item = _criterion(scope=("s1",), promise=("c1",))
        client.create.return_value = _json_message(_final_assessment(item, label="CORROBORATED"))
        snapshot = _snapshot({"id": "original", "claim": "Five routes were promised.", "supports": [
            _support("board", "We promise five overnight shuttle routes."),
        ]})
        context = {**_CONTEXT, "requested_query": "Were all five routes delivered?",
                   "requested_criteria": [seed]}
        result = ClaudeReconciliationJudge(client).assess(context, snapshot)
        self.assertEqual(result["label"], "UNVERIFIED")
        client.create.assert_called_once()
        data = json.loads(client.create.call_args.kwargs["messages"][0]["content"])
        self.assertEqual(data["requested_query"], context["requested_query"])
        self.assertEqual(data["requested_criteria"], {"s1": seed})

    def test_assessor_rejects_missing_requested_scope_or_an_unknown_citation_after_one_retry(self):
        routes = "Operate five overnight routes"
        stops = "Add accessible stops"
        context = {**_CONTEXT, "requested_criteria": [routes, stops]}
        for reason, item in [
            ("unmapped scope", _final_assessment(_criterion(scope=("s1",)))),
            ("unknown citation", _final_assessment(_criterion(scope=("s1",), delivery=("unknown",)),
                                                   _criterion(stops, scope=("s2",)))),
        ]:
            with self.subTest(reason=reason):
                client = Mock()
                client.create.return_value = _json_message(item)
                with self.assertRaises(ProviderError):
                    ClaudeReconciliationJudge(client).assess(context, _snapshot())
                self.assertEqual(client.create.call_count, 2)

    def test_a021_bid_rates_are_not_evidence_of_rates_actually_applied(self):
        bid = _support("bid", "RWNYC offers a tax rate of 56 percent for slots and 30 percent for table games.")
        report = _support("report", "Resorts World New York City proposed a 56 percent tax on slot machine "
                          "games and 30 percent on table games and other gaming sources.")
        bid_finding = {"id": "bid", "claim": "Resorts World offered the rates.", "supports": [bid, report]}
        seeds = ["Slot machine tax rate", "Table game tax rate"]
        conditions = ["Apply the 56 percent slot tax rate", "Apply the 30 percent table game tax rate"]
        context = {
            "commitment": {"original_promise": {"source": bid["source"], "excerpt": bid["excerpt"]}},
            "requested_query": "What rates were offered, and how do they compare with the rates required?",
            "requested_criteria": seeds,
        }
        initial = _final_assessment(*[
            _criterion(name, status="DELIVERED", scope=(f"s{number}",), promise=("c1",), delivery=("c2",))
            for number, name in enumerate(conditions, 1)
        ], label="CORROBORATED")
        misclassified = [_delivery_check(name, "The bid announced the rates.",
                                         support=[("c2", report["excerpt"])]) for name in conditions]
        opening = _support("opening", "Resorts World opened its gaming floor to customers.")
        opening_finding = {"id": "opening", "claim": "Resorts World opened.", "supports": [opening]}
        pledge = ("c1", bid["excerpt"])
        proposal = ("c2", report["excerpt"])
        proposed_rate = ("c2", "56 percent tax on slot machine games and 30 percent on table games")
        actual = ("c3", opening["excerpt"])
        for scenario, outcomes in (
            ("proposal only", ()),
            ("unrelated actual outcome", (actual,)),
            ("pledge and proposal misclassified as outcomes", (pledge, proposed_rate, actual)),
        ):
            with self.subTest(scenario=scenario):
                client = Mock()
                responses = [_json_message(initial), _observed_outcomes(*outcomes, planned_terms=(pledge, proposal))]
                if outcomes:
                    snapshot = _snapshot(bid_finding, opening_finding)
                    responses.append(_delivery_review(*misclassified))
                else:
                    snapshot = _snapshot(bid_finding)
                client.create.side_effect = responses

                result = ClaudeReconciliationJudge(client).assess(context, snapshot)

                self.assertEqual(result["label"], "UNVERIFIED")
                for row in result["criteria"]:
                    self.assertEqual(row["delivery_status"], "UNVERIFIED")
                    self.assertEqual(row["delivery_citation_ids"], [])
                    self.assertTrue(row["gap"])
                self.assertEqual(client.create.call_count, 3 if outcomes else 2)
                if outcomes:
                    verifier = json.loads(client.create.call_args.kwargs["messages"][0]["content"])
                    self.assertEqual(verifier["check_conditions"], {
                        f"v{index}": {"criterion": name, "timing_requirement": ""}
                        for index, name in enumerate(conditions, 1)
                    })
                    self.assertEqual(verifier["observed_outcomes"], [{"citation_id": "c3", "quote": opening["excerpt"]}])

    def test_already_formed_partnership_can_document_both_original_terms_and_delivery(self):
        name = "Form a workforce housing partnership with Cirrus."
        excerpt = "The authority and Cirrus formed a workforce housing partnership on 1 November 2025."
        support = _support("partnership-agreement", excerpt)
        snapshot = _snapshot({"id": "partnership", "claim": excerpt, "supports": [support]})
        context = {"commitment": {"original_text": excerpt, "original_promise": {
            "source": support["source"], "excerpt": excerpt,
        }}}
        initial = _final_assessment(_criterion(name, status="DELIVERED", scope=("s1",),
                                               promise=("c1",), delivery=("c1",)),
                                    label="CORROBORATED")
        client = Mock()
        client.create.side_effect = [_json_message(initial), _observed_outcomes(("c1", excerpt)),
                                     _delivery_review(_delivery_check(name, excerpt, support=[("c1", excerpt)]))]

        result = ClaudeReconciliationJudge(client).assess(context, snapshot)

        self.assertEqual(result["label"], "CORROBORATED")
        self.assertEqual(result["criteria"][0]["promise_citation_ids"], ["c1"])
        self.assertEqual(result["criteria"][0]["delivery_citation_ids"], ["c1"])

    def test_a060_confirmed_payment_does_not_verify_the_payment_deadline(self):
        license_ = _support("license", "Resorts World New York City shall pay, on or before December 31, 2025, "
                            "a $500,000,000 gaming facility license fee.")
        paid = "all three licensees have remitted their $500M license fee"
        transcript = _support("february-transcript", f"First, {paid}, which has, consistent with state law, in turn "
                              "been remitted to the Metropolitan Transit Authority.")
        transcript["source"]["published_on"] = "2026-02-03"
        edge = _link("PROGRESS", "license", "remittance")
        edge["reasoning"] = "Generated statement claiming payment was made on time."
        snapshot = _snapshot(
            {"id": "license", "claim": "A payment deadline was established.", "supports": [license_]},
            {"id": "remittance", "claim": "Generated claim of payment on time.", "supports": [transcript]},
            relations=(edge,),
        )
        seed = "Pay the $500 million casino license fee by the stated deadline"
        context = {
            "commitment": {"original_promise": {"source": license_["source"], "excerpt": license_["excerpt"]}},
            "requested_query": "Did Resorts World New York City pay its license fee by December 31, 2025?",
            "requested_criteria": [seed],
        }
        amount = "Pay the $500 million fee"
        deadline = "Pay on or before December 31, 2025"
        initial = _final_assessment(*[
            _criterion(name, status="DELIVERED", scope=("s1",), promise=("c1",), delivery=("c2",), timing=timing)
            for name, timing in ((amount, ""), (deadline, deadline))
        ], label="CORROBORATED")
        gap = "Obtain a dated receipt confirming payment on or before December 31, 2025."
        payment = _delivery_check(amount, "The Commission confirms payment.", support=[("c2", paid)])
        for timing_support, status in (([], "UNVERIFIED"), ([("c2", paid)], "PARTIAL")):
            with self.subTest(timing_support=timing_support):
                timing = _delivery_check(deadline, "The transcript gives no payment date.",
                                         support=timing_support, gap=gap)
                client = Mock()
                client.create.side_effect = [_json_message(initial), _observed_outcomes(("c2", paid)),
                                             _delivery_review(payment, timing)]

                result = ClaudeReconciliationJudge(client).assess(context, snapshot)

                self.assertEqual(result["label"], "PARTIALLY_CORROBORATED")
                by_condition = {row["criterion"]: row for row in result["criteria"]}
                self.assertEqual(by_condition[amount]["delivery_citation_ids"], ["c2"])
                self.assertEqual(by_condition[deadline]["delivery_status"], status)
                self.assertEqual(by_condition[deadline]["delivery_citation_ids"], ["c2"] if timing_support else [])
                self.assertEqual(by_condition[deadline]["gap"], gap)
                for call in client.create.call_args_list:
                    payload = call.kwargs["messages"][0]["content"]
                    self.assertNotIn(edge["reasoning"], payload)
                    self.assertNotIn(snapshot["findings"][1]["claim"], payload)

        for event, approved_timing, proposed_timing, expected in (
            (paid, (), (), "PARTIALLY_CORROBORATED"),
            (paid, (), ("t1",), "PARTIALLY_CORROBORATED"),
            (paid + " on December 30, 2025", (("c2", "on December 30, 2025"),),
             ("t1",), "CORROBORATED"),
        ):
            with self.subTest(event=event, approved_timing=approved_timing, proposed_timing=proposed_timing):
                transcript["excerpt"] = event
                outcome = _delivery_check(amount, "Payment was reported.", support=[("c2", event)])
                timing = _delivery_check(deadline, "Payment was made on time.", support=[("c2", event)],
                                         timing_support=proposed_timing)
                client = Mock()
                client.create.side_effect = [_json_message(initial), _observed_outcomes(("c2", event), timing=approved_timing),
                                             _delivery_review(outcome, timing)]
                result = ClaudeReconciliationJudge(client).assess(context, snapshot)
                self.assertEqual(result["label"], expected)
                if not approved_timing:
                    self.assertNotIn("Payment was made on time", result["reasoning"])
                    self.assertIn("does not establish that delivery met this timing requirement", result["reasoning"])

    def test_verified_delivery_requires_complete_scope_for_full_corroboration(self):
        name = "Operate five overnight routes"
        pledge = "The authority pledged to operate five overnight routes."
        excerpt = "All five overnight routes began operating."
        snapshot = _snapshot({"id": "opening", "claim": excerpt, "supports": [_support("audit", f"{pledge} {excerpt}")]})
        initial = _final_assessment(_criterion(name, status="DELIVERED", delivery=("c1",)), label="CORROBORATED")
        check = _delivery_check(name, excerpt, support=[("c1", excerpt)])
        for scope_gap, expected in (
            ("", "CORROBORATED"),
            ("The query also asked whether the service covers every requested neighborhood.", "PARTIALLY_CORROBORATED"),
        ):
            with self.subTest(scope_gap=scope_gap):
                client = Mock()
                client.create.side_effect = [_json_message(initial),
                                             _observed_outcomes(("c1", excerpt), planned_terms=(("c1", pledge),)),
                                             _delivery_review(check, scope_gap=scope_gap)]
                result = ClaudeReconciliationJudge(client).assess(_CONTEXT, snapshot)
                self.assertEqual(result["label"], expected)
                self.assertEqual(result["scope_gap"], scope_gap)
                self.assertEqual(result["criteria"][0]["delivery_citation_ids"], ["c1"])

    def test_delivery_review_cannot_invent_a_quote_for_an_existing_citation(self):
        name = "Operate five overnight routes"
        support = _support("audit", "The authority began operating three overnight routes.")
        snapshot = _snapshot({"id": "opening", "claim": support["excerpt"], "supports": [support]})
        initial = _final_assessment(_criterion(name, status="DELIVERED", delivery=("c1",)), label="CORROBORATED")
        fabricated = _delivery_check(name, "Five routes opened.",
                                     support=[("c1", "The authority began operating five overnight routes.")])
        client = Mock()
        client.create.side_effect = [_json_message(initial), _observed_outcomes(("c1", support["excerpt"])),
                                     _delivery_review(fabricated)]

        result = ClaudeReconciliationJudge(client).assess(_CONTEXT, snapshot)
        self.assertEqual(result["label"], "UNVERIFIED")
        self.assertEqual(result["criteria"][0]["delivery_citation_ids"], [])
        self.assertNotIn("Five routes opened", result["reasoning"])

    def test_unverifiable_classifier_quote_does_not_discard_the_research_result(self):
        name = "Operate five overnight routes"
        excerpt = "The authority began operating five overnight routes."
        snapshot = _snapshot({"id": "opening", "claim": name, "supports": [_support("audit", excerpt)]})
        initial = _json_message(_final_assessment(_criterion(name, status="DELIVERED", delivery=("c1",))))
        incorrect = _observed_outcomes(("c1", "The authority started operating five overnight routes."))
        client = Mock()
        for corrected, expected in ((False, "UNVERIFIED"), (True, "CORROBORATED")):
            with self.subTest(corrected=corrected):
                if corrected:
                    retry = [_observed_outcomes(("c1", excerpt)),
                             _delivery_review(_delivery_check(name, excerpt, support=[("c1", excerpt)]))]
                else:
                    retry = [incorrect]
                client.create.side_effect = [initial, incorrect, *retry]
                result = ClaudeReconciliationJudge(client).assess(_CONTEXT, snapshot)
                self.assertEqual(result["label"], expected)

    def test_preparatory_action_does_not_partially_deliver_a_future_cutoff(self):
        name = "End sales of new petrol cars"
        deadline = "We will end new petrol car sales on 1 January 2099."
        consultation = "The agency started a consultation on the proposed sales cutoff."
        snapshot = _snapshot(
            {"id": "promise", "claim": name, "supports": [_support("policy", deadline)]},
            {"id": "consultation", "claim": "Consultation began.", "supports": [_support("news", consultation)]},
        )
        proof = {"criterion": name, "due_on": "2099-01-01", "citation_id": "c1", "quote": deadline}
        criterion = _criterion(name, status="PARTIAL", promise=("c1",), delivery=("c2",))
        review = _delivery_check(name, "The cutoff has not occurred.", support=[("c2", consultation)],
                                 gap="An actual sales cutoff remains undocumented.", fulfilled_part="")
        client = Mock()
        client.create.side_effect = [
            _json_message(_final_assessment(criterion, label="PARTIALLY_CORROBORATED", not_yet_due=[proof])),
            _observed_outcomes(("c2", consultation), planned_terms=(("c1", deadline),)), _delivery_review(review),
        ]
        result = ClaudeReconciliationJudge(client).assess(_CONTEXT, snapshot)
        self.assertEqual(result["label"], "NOT_YET_DUE")
        self.assertEqual(result["criteria"][0]["delivery_citation_ids"], [])

    def test_not_yet_due_requires_a_future_deadline_cited_in_the_original_terms(self):
        name = "End sales of new petrol cars"
        excerpt = "We will end new petrol car sales on 1 January 2000."
        snapshot = _snapshot({"id": "promise", "claim": name, "supports": [_support("policy", excerpt)]})
        criterion = _criterion(name, status="NOT_YET_DUE", promise=("c1",))
        proof = {"criterion": name, "due_on": "2000-01-01", "citation_id": "c1", "quote": excerpt}
        for proofs in ([], [proof], [{**proof, "due_on": "2099-01-01"}]):
            with self.subTest(proofs=proofs):
                client = Mock()
                client.create.return_value = _json_message(_final_assessment(
                    criterion, label="NOT_YET_DUE", not_yet_due=proofs,
                ))
                self.assertEqual(ClaudeReconciliationJudge(client).assess(_CONTEXT, snapshot)["label"], "UNVERIFIED")

    def test_unverified_future_year_is_checked_against_original_quote(self):
        name = "End sales of new petrol cars"
        excerpt = "We will end new petrol car sales by 2099."
        snapshot = _snapshot({"id": "promise", "claim": name, "supports": [_support("policy", excerpt)]})
        criterion = _criterion(name, promise=("c1",))
        proof = {"criterion": name, "due_on": "2099-01-01", "citation_id": "c1", "quote": excerpt}
        client = Mock()
        client.create.side_effect = [_json_message(_final_assessment(criterion)),
                                     _json_message({"not_yet_due": [proof]})]
        result = ClaudeReconciliationJudge(client).assess(_CONTEXT, snapshot)
        self.assertEqual(result["label"], "NOT_YET_DUE")
        self.assertEqual(result["criteria"][0]["due_on"], "2099-01-01")

    def test_nonpreferred_finding_cannot_supply_delivery_proof(self):
        name = "Operate five overnight routes"
        excerpt = "All five routes started service."
        snapshot = _snapshot(
            {"id": "claim", "claim": name, "supports": [_support("claim", excerpt)]},
            {"id": "audit", "claim": "No route started.", "supports": [_support("audit", "No route started.")]},
            relations=(_link("CONFLICT", "claim", "audit", status="RESOLVED", preferred_id="audit"),),
        )
        client = Mock()
        client.create.side_effect = [
            _json_message(_final_assessment(_criterion(name, status="DELIVERED", delivery=("c1",)),
                                            label="CORROBORATED")),
            _observed_outcomes(("c1", excerpt)),
        ]
        self.assertEqual(ClaudeReconciliationJudge(client).assess(_CONTEXT, snapshot)["label"], "UNVERIFIED")

    def test_verifiers_receive_source_passages_without_generated_evidence_explanations(self):
        first = _support("test-flight", "The mission is a test in Earth orbit.")
        first["explanation"] = "Model-generated interpretation."
        second = _support("landing", "The lunar landing is planned for a separate mission.")
        comparisons = [{"id": "c1", "contradicts": False, "left_quote": "", "right_quote": "",
                        "left_kind": "OTHER", "right_kind": "PLAN"}]
        client = Mock()
        client.create.side_effect = [_json_message({"comparisons": comparisons}),
                                     _json_message({"basis": ""})]
        judge = ClaudeReconciliationJudge(client)
        self.assertEqual(judge.verify_conflicts(_CONTEXT, [{
            "id": "c1", "left": {"claim": "Orbital test", "supports": [first]},
            "right": {"claim": "Lunar landing", "supports": [second]},
        }]), comparisons)
        self.assertFalse(judge.verify_refutation(_CONTEXT, first, "DIRECT_DISPROOF", first["excerpt"]))
        self.assertEqual(client.create.call_count, 2)
        classification = json.loads(client.create.call_args.kwargs["messages"][0]["content"])
        self.assertNotIn("target", classification)
        self.assertNotIn("basis", classification)
        for call in client.create.call_args_list:
            payload = call.kwargs["messages"][0]["content"]
            self.assertIn(first["excerpt"], payload)
            self.assertNotIn(first["explanation"], payload)
            self.assertNotIn("Orbital test", payload)
            self.assertNotIn("private workbook note", payload)

    def test_conflict_verifier_distinguishes_a_plan_revision_from_competing_outcomes(self):
        plan = _support("plan", "The rate will be removed next April.")
        revision = _support("revision", "We are no longer proceeding with removal of the rate.")
        removed = _support("removed", "The rate was removed on April 6.")
        retained = _support("retained", "The same rate remained in force on April 6.")
        client = Mock()
        judge = ClaudeReconciliationJudge(client)
        for first, second, left_kind, right_kind, expected in (
            (plan, revision, "PLAN", "REVISION", False), (removed, retained, "OUTCOME", "OUTCOME", True),
        ):
            with self.subTest(left_kind=left_kind, right_kind=right_kind):
                client.create.return_value = _json_message({"comparisons": [{
                    "id": "c1", "contradicts": True, "left_quote": first["excerpt"], "right_quote": second["excerpt"],
                    "left_kind": left_kind, "right_kind": right_kind,
                }]})
                pair = {"id": "c1", "left": {"supports": [first]}, "right": {"supports": [second]}}
                result = judge.verify_conflicts(_CONTEXT, [pair])
                self.assertIs(result[0]["contradicts"], expected)

    def test_explicit_withdrawal_still_reaches_scope_verification(self):
        quote = "The authority withdraws its promise to run five overnight routes."
        client = Mock()
        client.create.side_effect = [_json_message({"basis": "OFFICIAL_WITHDRAWAL"}),
                                     _json_message({"directly_refutes": True})]
        judge = ClaudeReconciliationJudge(client)
        self.assertTrue(judge.verify_refutation(_CONTEXT, _support("withdrawal", quote), "DIRECT_DISPROOF", quote))
        verification = json.loads(client.create.call_args.kwargs["messages"][0]["content"])
        self.assertEqual(verification["basis"], "OFFICIAL_WITHDRAWAL")
        self.assertEqual(client.create.call_count, 2)

    def test_conflict_review_uses_targeted_search_and_verified_citations(self):
        client = Mock()
        client.create.side_effect = [
            _search_message(), _json_message({"findings": [_structured_finding("c2")]})
        ]
        pair = {"from_id": "internal-finding", "from_claim": "Five routes opened.",
                "to_id": "internal-other-finding", "to_claim": "Three routes opened."}
        evidence = ClaudeWebResearcher(client).review_conflicts(_CONTEXT, [pair])
        self.assertEqual(evidence[0]["source"]["url"], "https://transit.example/public/service-bulletin")
        search = client.create.call_args_list[0].kwargs
        self.assertEqual(search["tools"][0]["max_uses"], 3)
        body = json.loads(search["messages"][0]["content"])
        self.assertEqual(body["conflicts"], [{"from_claim": "Five routes opened.", "to_claim": "Three routes opened."}])
        self.assertNotIn("private workbook note", json.dumps(body))


if __name__ == "__main__":
    unittest.main()
