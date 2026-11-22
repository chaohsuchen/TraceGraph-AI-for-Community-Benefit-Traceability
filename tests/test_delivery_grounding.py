import json
import unittest
from unittest.mock import Mock

from tracegraph.providers import ClaudeReconciliationJudge, ProviderError, _condition_scope_locks
from tracegraph.reconcile import EvidenceReconciler
from tests.test_reconcile import (
    _criterion, _delivery_challenge, _delivery_check, _delivery_review, _direct_witness, _outcome_read,
    _final_assessment, _json_message,
    _observed_outcomes, _plan, _research, _snapshot, _support,
)


def assess(criterion, excerpts, *replies, client=None, scope_locks=(), query=None):
    """Run the production assessor with scripted model decisions and literal source text."""
    findings = [
        {"id": f"f{n}", "claim": text, "supports": [_support(f"record-{n}", text)]}
        for n, text in enumerate(excerpts, 1)
    ]
    context = {
        "commitment": {"original_promise": {"source": findings[0]["supports"][0]["source"], "excerpt": excerpts[0]}},
        "requested_query": query or criterion["criterion"],
    }
    initial = _final_assessment({**criterion, "scope_criteria": ["s1"]})
    initial["scope_locks"] = list(scope_locks)
    client = client or Mock()
    client.create.side_effect = [_json_message(initial), *replies]
    return ClaudeReconciliationJudge(client).assess(context, _snapshot(*findings))


class DeliveryGroundingTests(unittest.TestCase):
    def test_compound_promise_can_have_distinct_criteria_and_a_shared_project(self):
        citations = {"c1": {"excerpt": "Project Lantern will serve Harbor East and Harbor West."},
                     "c2": {"excerpt": "The city also serves the Promenade."}}
        shared = {"mention": "Project Lantern", "citation_id": "c1", "condition_numbers": [], "aliases": []}
        east = {"mention": "Harbor East", "citation_id": "c1", "condition_numbers": [1],
                "aliases": [{"mention": "Promenade", "citation_id": "c2"}]}
        west = {"mention": "Harbor West", "citation_id": "c1", "condition_numbers": [2], "aliases": []}
        locks = _condition_scope_locks({"criteria": [{}, {}], "scope_locks": [shared, east, west]}, citations)
        self.assertEqual([[x["mention"] for x in row] for row in locks],
                         [["Project Lantern", "Harbor East"], ["Project Lantern", "Harbor West"]])
        self.assertEqual(locks[0][1]["aliases"], [])

    def test_generic_words_in_true_corporate_observation_cannot_replace_the_original_local_scope(self):
        query = "Did Bally's fund memberships in Upwards for priority childcare placement?"
        pledge = "We partnered with Upwards to offer childcare benefits, including for Bally's Bronx employees."
        lock = {"mention": "Bronx", "citation_id": "c1", "aliases": [], "condition_numbers": []}
        for occurrence, words in (
            ("We fund every employee membership to the Upwards network.", "every employee"),
            ("Members also receive priority placement at Upwards facilities.", "Members"),
            ("Every Bronxville employee received the benefit.", "Bronxville"),
        ):
            row = _criterion(query, status="DELIVERED", promise=("c1",), delivery=("c2",))
            impossible = "Bronx employees received employer-funded memberships and priority placement."
            witness = _direct_witness("c2", occurrence, words)
            result = assess(row, [pledge, occurrence], _observed_outcomes(("c2", occurrence)),
                            _delivery_review(_delivery_check(query, impossible, support=[("c2", occurrence)])),
                            _delivery_challenge(_outcome_read(impossible, "c2", witness=witness)), _json_message({"p1": "DIFFERENT"}),
                            scope_locks=[lock], query=query)
            with self.subTest(words=words):
                self.assertEqual(result["label"], "UNVERIFIED")
                self.assertIn(occurrence, result["reasoning"])
                self.assertNotIn(impossible, result["reasoning"] + result["verdict"])
                self.assertEqual(result["criteria"][0]["locked_scopes"], [dict(mention="Bronx", citation_id="c1", aliases=[])])
                self.assertEqual(result["criteria"][0]["scope_expression_checks"][0]["decision"], "DIFFERENT")

    def test_same_source_owned_scope_word_order_or_pronoun_can_support_the_battery_outcome(self):
        pledge = "Apple committed to use recycled cobalt in all Apple-designed batteries by 2025."
        lock = {"mention": "all Apple-designed batteries", "citation_id": "c1", "aliases": [], "condition_numbers": []}
        for occurrence, phrase in (
            ("All batteries designed by Apple are made with 100 percent recycled cobalt.", "All batteries designed by Apple"),
            ("Apple now uses 100 percent recycled cobalt in all the batteries it designs.", "all the batteries it designs"),
        ):
            with self.subTest(phrase=phrase):
                row = _criterion(pledge, status="DELIVERED", promise=("c1",), delivery=("c2",), timing="by 2025")
                witness = _direct_witness("c2", occurrence, phrase)
                client = Mock()
                result = assess(row, [pledge, occurrence], _observed_outcomes(("c2", occurrence)),
                                _delivery_review(_delivery_check(pledge, occurrence, support=[("c2", occurrence)])),
                                _delivery_challenge(_outcome_read(occurrence, "c2", incomplete="The achievement date is unclear.",
                                                                  witness=witness)), _json_message({"p1": "EQUIVALENT"}),
                                scope_locks=[lock], client=client)
                self.assertEqual(result["label"], "PARTIALLY_CORROBORATED")
                self.assertEqual(result["criteria"][0]["delivery_citation_ids"], ["c2"])
                self.assertIn("achievement date", result["verdict"])
                pair, = json.loads(client.create.call_args.kwargs["messages"][0]["content"])["pairs"].values()
                self.assertEqual(pair, {"locked_phrase": lock["mention"], "locked_context": pledge,
                                        "observed_phrase": phrase, "observed_context": occurrence})
                self.assertNotIn("tools", client.create.call_args.kwargs)
                self.assertEqual(result["criteria"][0]["scope_expression_checks"][0]["observed_citation_id"], "c2")

    def test_all_mismatched_locks_are_batched_and_an_unresolved_reading_blocks_delivery(self):
        pledge = "The agency promised to move the Harbor East crews in Region Nine."
        moved = "The agency moved the Eastern Harbor crews in the Ninth Region."
        locks = [{"mention": name, "citation_id": "c1", "aliases": [], "condition_numbers": []}
                 for name in ("Harbor East", "Region Nine")]
        for answer in (_json_message({"p1": "EQUIVALENT", "p2": "UNCERTAIN"}),
                       _json_message({"wrong": "EQUIVALENT", "p2": "EQUIVALENT"}),
                       ProviderError("fixture offline")):
            row = _criterion(pledge, status="DELIVERED", promise=("c1",), delivery=("c2",))
            client = Mock()
            result = assess(row, [pledge, moved], _observed_outcomes(("c2", moved)),
                            _delivery_review(_delivery_check(pledge, moved, support=[("c2", moved)])),
                            _delivery_challenge(_outcome_read(moved, "c2")), answer, scope_locks=locks, client=client)
            self.assertEqual(client.create.call_count, 5)
            self.assertEqual(result["label"], "UNVERIFIED")
            self.assertEqual(result["criteria"][0]["context_citation_ids"], ["c2"])
            self.assertIn("UNCERTAIN", [r["decision"] for r in result["criteria"][0]["scope_expression_checks"]])

    def test_semantic_equivalence_cannot_repair_a_witness_copied_from_another_source(self):
        pledge = "The city promised to inspect all agency-owned lifts."
        genuine = "Several lifts were inspected."
        other = "The city inspected all lifts owned by the agency."
        row = _criterion(pledge, status="DELIVERED", promise=("c1",), delivery=("c2",))
        client = Mock()
        check = _outcome_read(other, "c2", witness=_direct_witness("c2", other, "all lifts owned by the agency"))
        result = assess(row, [pledge, genuine, other], _observed_outcomes(("c2", genuine)),
                        _delivery_review(_delivery_check(pledge, genuine, support=[("c2", genuine)])),
                        _delivery_challenge(check), _json_message({"p1": "EQUIVALENT"}),
                        scope_locks=[{"mention": "all agency-owned lifts", "citation_id": "c1",
                                      "aliases": [], "condition_numbers": []}], client=client)
        self.assertEqual(client.create.call_count, 4)
        self.assertEqual(result["label"], "UNVERIFIED")
        self.assertNotIn("scope_expression_checks", result["criteria"][0])

    def test_funded_batch_register_can_bind_an_observed_payment_to_its_project(self):
        name = "Pay North Pier apprentices' travel allowances."
        pledge = "Meridian will pay North Pier apprentices' travel allowances."
        sent = "Meridian disbursed apprentice travel allowances under batch MP-104."
        register = "The beneficiaries of disbursement MP-104 were the apprentices enrolled at North Pier."
        row = _criterion(name, status="DELIVERED", promise=("c1",), delivery=("c2",))
        check = _delivery_check(name, sent, support=[("c2", sent)], in_scope=True)
        witness = {"binding": "BRIDGED", "action_citation_id": "c2", "action_quote": sent,
                   "scope_citation_id": "c3", "scope_quote": register, "scope_words": "apprentices enrolled at North Pier",
                   "event_reference": "MP-104"}
        final = _outcome_read("Meridian disbursed the North Pier apprentices' allowances in batch MP-104.",
                              "c2", witness=witness)
        result = assess(row, [pledge, sent, register], _observed_outcomes(("c2", sent)),
                        _delivery_review(check), _delivery_challenge(final),
                        scope_locks=[{"mention": "North Pier", "citation_id": "c1", "condition_numbers": [], "aliases": []}])
        self.assertEqual(result["label"], "CORROBORATED")
        self.assertEqual(result["criteria"][0]["scope_evidence_id"], "c3")
        self.assertIn("Scope evidence: c3", result["reasoning"])

    def test_scope_bridge_requires_the_same_actual_event_in_both_owned_passages(self):
        pledge = "Meridian promised to pay travel allowances for North Pier apprentices."
        sent = "Meridian disbursed travel allowances under batch MP-104."
        wrong = "The North Pier apprentices received their allowances under batch MP-114."
        row = _criterion(pledge, status="DELIVERED", promise=("c1",), delivery=("c2",))
        for reference in ("MP-104", "MP-114", ""):
            witness = {"binding": "BRIDGED", "action_citation_id": "c2", "action_quote": sent,
                       "scope_citation_id": "c3", "scope_quote": wrong, "scope_words": "North Pier",
                       "event_reference": reference}
            result = assess(row, [pledge, sent, wrong], _observed_outcomes(("c2", sent)),
                            _delivery_review(_delivery_check(pledge, sent, support=[("c2", sent)])),
                            _delivery_challenge(_outcome_read(sent, "c2", witness=witness)),
                            scope_locks=[{"mention": "North Pier", "citation_id": "c1", "condition_numbers": [], "aliases": []}])
            with self.subTest(reference=reference):
                self.assertEqual(result["label"], "UNVERIFIED")
                self.assertEqual(result["criteria"][0]["delivery_citation_ids"], [])

    def test_attested_site_alias_in_a_completed_event_can_bind_directly(self):
        pledge = "The city pledged to reopen North Pier to the public."
        registry = "North Pier is also known as the Lime Jetty."
        opened = "The Lime Jetty reopened to the public after repairs."
        row = _criterion(pledge, status="DELIVERED", promise=("c1",), delivery=("c3",))
        lock = {"mention": "North Pier", "citation_id": "c1", "condition_numbers": [1],
                "aliases": [{"mention": "Lime Jetty", "citation_id": "c2"}]}
        result = assess(row, [pledge, registry, opened], _observed_outcomes(("c3", opened)),
                        _delivery_review(_delivery_check(pledge, opened, support=[("c3", opened)])),
                        _delivery_challenge(_outcome_read(opened, "c3")), scope_locks=[lock])
        self.assertEqual(result["label"], "CORROBORATED")
        self.assertEqual(result["criteria"][0]["locked_scopes"][0]["aliases"], lock["aliases"])

    def test_optimistic_corporate_report_requires_a_source_owned_local_occurrence(self):
        name = "Pay the North Pier apprentices' travel allowances."
        pledge = "We promise to pay the North Pier apprentices' travel allowances."
        policy = "We cover every apprentice travel allowance."
        proposal = "Our program proposes to include North Pier apprentices."
        row = _criterion(name, status="DELIVERED", promise=("c1",), delivery=("c2",))
        check = _delivery_check(name, policy, support=[("c2", policy)], in_scope=True)
        optimistic = "North Pier apprentices received funded allowances."
        # The counterfactual fields wrongly approve every example. There is no
        # in-scope occurrence to cite. Copying its location from a plan won't do.
        for witness in (
            {"binding": "NONE", "action_citation_id": "", "action_quote": "", "scope_citation_id": "",
             "scope_quote": "", "scope_words": "", "event_reference": ""},
            _direct_witness("c2", policy, "North Pier apprentices"),
            {"binding": "BRIDGED", "action_citation_id": "c2", "action_quote": policy,
             "scope_citation_id": "c3", "scope_quote": optimistic, "scope_words": "North Pier", "event_reference": ""},
        ):
            with self.subTest(binding=witness):
                final = _outcome_read(optimistic, "c2", witness=witness)
                result = assess(row, [pledge, policy, proposal], _observed_outcomes(("c2", policy)),
                                _delivery_review(check), _delivery_challenge(final))
                self.assertEqual(result["label"], "UNVERIFIED")
                self.assertEqual(result["criteria"][0]["context_citation_ids"], ["c2"])
                self.assertIn(policy, result["reasoning"])
                self.assertNotIn(optimistic, result["reasoning"] + result["verdict"])

    def test_h012_company_benefit_is_only_context_for_bronx_entitlement(self):
        name = "Provide Bronx employees with company-paid memberships and priority childcare eligibility."
        promise = "We will fund memberships for Bronx employees with priority childcare eligibility."
        corporate = "We fund employee memberships at our company."
        row = _criterion(name, status="DELIVERED", promise=("c1",), delivery=("c2",),
                         elements=("Company-paid memberships", "Priority eligibility for Bronx employees"))
        check = _delivery_check(name, corporate, support=[("c2", corporate)], in_scope=False,
                                element_support={"e1": ["c2"], "e2": []})
        result = assess(row, [promise, corporate], _observed_outcomes(("c2", corporate)), _delivery_review(check))
        self.assertEqual(result["label"], "UNVERIFIED")
        self.assertEqual(result["criteria"][0]["promise_citation_ids"], ["c1"])
        self.assertEqual(result["criteria"][0]["delivery_citation_ids"], [])

    def test_h012_confirmed_bronx_membership_does_not_also_prove_priority(self):
        name = "Provide Bronx employees with company-paid memberships and priority childcare eligibility."
        promise = "Bronx employees will receive funded memberships and priority childcare eligibility."
        funding = "Bally's funded the memberships of all Bronx employees."
        priority = "Priority eligibility for Bronx employees"
        row = _criterion(name, status="DELIVERED", promise=("c1",), delivery=("c2",),
                         elements=("Company-paid Bronx memberships", priority))
        check = _delivery_check(name, funding, support=[("c2", funding)], gap="",
                                element_support={"e1": ["c2"], "e2": []})
        result = assess(row, [promise, funding], _observed_outcomes(("c2", funding)), _delivery_review(check),
                        _delivery_challenge(_outcome_read(funding, "c2", incomplete="Bronx employees may have no priority eligibility.")))
        self.assertEqual(result["label"], "PARTIALLY_CORROBORATED")
        self.assertIn("Bronx employees may have no priority eligibility.", result["criteria"][0]["gap"])
        self.assertEqual(result["criteria"][0]["delivery_citation_ids"], ["c2"])

    def test_cobalt_timing_must_be_part_of_the_observed_delivery_evidence(self):
        name = "Use 100% recycled cobalt in all Apple-designed batteries by 2025."
        promise = "We will use 100% recycled cobalt in all Apple-designed batteries by 2025."
        actual = "All batteries designed by Apple now use 100% recycled cobalt."
        for dated, same_event, expected in (
            ("Apple achieved several targets in 2025.", False, "PARTIALLY_CORROBORATED"),
            ("Apple reached 100% recycled cobalt in all Apple-designed batteries in 2025.", True, "CORROBORATED"),
        ):
            with self.subTest(same_event=same_event):
                row = _criterion(name, status="DELIVERED", promise=("c1",), delivery=("c2",),
                                 timing="by 2025", elements=("All Apple-designed batteries use 100% recycled cobalt",
                                                            "Target met by 2025"))
                support = [("c2", actual), ("c3", dated)] if same_event else [("c2", actual)]
                check = _delivery_check(name, "Apple reports full use.", support=support, timing_support=("t1",),
                                        element_support={"e1": ["c2"], "e2": ["c3"]})
                reread = (_outcome_read(dated, "c3", "c2") if same_event else
                          _outcome_read(actual, "c2", incomplete="The battery target might have been reached after 2025."))
                result = assess(row, [promise, actual, dated],
                                _observed_outcomes(("c2", actual), ("c3", dated), timing=[("c3", "in 2025")]),
                                _delivery_review(check), _delivery_challenge(reread))
                self.assertEqual(result["label"], expected)
                if same_event:
                    self.assertEqual(result["criteria"][0]["timing_support"], [{"citation_id": "c3", "quote": "in 2025"}])
                else:
                    self.assertEqual(result["criteria"][0]["timing_support"], [])

    def test_h007_unverified_actual_hiring_preserves_documented_announcement(self):
        name = "Achieve the permanent Bronx workforce composition targets."
        promise = "Bally's stated targets of 70% Bronx residents, 47% women and 75% minorities."
        chicago = "The Chicago facility reports hiring a workforce of 45% women."
        denial = "Bally's never publicly stated its Bronx workforce targets."
        announcement = "Document that Bally's publicly stated these workforce targets"
        for initial_status in ("UNVERIFIED", "DELIVERED"):
            with self.subTest(initial_status=initial_status):
                row = _criterion(name, status=initial_status, promise=("c1",), gap=denial, elements=(announcement,))
                row.update(explanation=denial, observed_scope=denial)
                replies = []
                if initial_status == "DELIVERED":
                    check = _delivery_check(name, denial, gap=denial, in_scope=False)
                    replies = [_observed_outcomes(("c2", chicago)), _delivery_review(check)]
                result = assess(row, [promise, chicago], *replies)
                self.assertEqual(result["label"], "UNVERIFIED")
                checked = result["criteria"][0]
                self.assertEqual(checked["promise_citation_ids"], ["c1"])
                self.assertEqual(checked["delivery_citation_ids"], [])
                self.assertIn(promise, result["reasoning"])
                self.assertNotIn(announcement, checked["gap"])
                self.assertTrue(checked["gap"])
                self.assertNotIn(denial, result["verdict"] + result["reasoning"] + checked["gap"])

    def test_partly_invalid_quote_classification_preserves_only_independent_verified_outcomes(self):
        name = "Use 100% recycled cobalt in all Apple-designed batteries by 2025."
        pledge = "We will use 100% recycled cobalt in all Apple-designed batteries by 2025."
        outcome = "All Apple-designed batteries now use 100% recycled cobalt."
        proposal = "We plan to reach our packaging target in 2025."
        bad = _observed_outcomes(("c2", outcome), ("c3", proposal), planned_terms=[("c3", proposal)],
                                 timing=[("c3", "in 2025")])
        for support, expected in ((("c2", outcome), "PARTIALLY_CORROBORATED"), (("c3", proposal), "UNVERIFIED")):
            with self.subTest(support=support[0]):
                row = _criterion(name, status="DELIVERED", promise=("c1",), delivery=(support[0],), timing="by 2025")
                check = _delivery_check(name, support[1], support=[support], timing_support=("t1",))
                extra = ([_delivery_challenge(_outcome_read(outcome, "c2", incomplete="The target may have been met after 2025."))]
                         if support[0] == "c2" else [])
                result = assess(row, [pledge, outcome, proposal], bad, bad, _delivery_review(check), *extra)
                self.assertEqual(result["label"], expected)
                checked = result["criteria"][0]
                self.assertNotIn("c3", checked["delivery_citation_ids"])
                if support[0] == "c2":
                    self.assertEqual(checked["delivery_citation_ids"], ["c2"])
                    self.assertEqual(checked["timing_support"], [])

    def test_valid_date_in_the_same_document_can_belong_to_another_event(self):
        name = "Achieve the battery cobalt target by 2025."
        promise = "Apple committed to the battery cobalt target by 2025."
        current = "The battery cobalt target is now achieved."
        other = "In 2025 the company achieved its packaging target."
        row = _criterion(name, status="DELIVERED", promise=("c1",), delivery=("c2",), timing="by 2025")
        check = _delivery_check(name, "The battery target was achieved in 2025.", support=[("c2", current)],
                                timing_support=("t1",))
        result = assess(row, [promise, f"{current} {other}"],
                        _observed_outcomes(("c2", current), ("c2", other), timing=[("c2", "In 2025")]),
                        _delivery_review(check), _delivery_challenge(_outcome_read(
                            current, "c2", incomplete="The later battery milestone might not have occurred by 2025.")))
        self.assertEqual(result["label"], "PARTIALLY_CORROBORATED")
        self.assertEqual(result["criteria"][0]["timing_support"], [])
        self.assertNotIn(check["explanation"], result["verdict"] + result["reasoning"])

    def test_dart_measured_change_establishes_original_success_threshold(self):
        name = "Achieve the DART mission's minimum 73-second change in Dimorphos' orbital period."
        promise = "NASA defined DART success as changing Dimorphos' orbital period by at least 73 seconds."
        measured = "DART's impact changed Dimorphos' orbital period by 32 minutes."
        row = _criterion(name, status="DELIVERED", promise=("c1",), delivery=("c2",),
                         elements=("Impact changed Dimorphos' orbital period", "Change reached at least 73 seconds"))
        check = _delivery_check(name, measured, support=[("c2", measured)], element_support={"e1": ["c2"], "e2": ["c2"]})
        result = assess(row, [promise, measured], _observed_outcomes(("c2", measured)), _delivery_review(check),
                        _delivery_challenge(_outcome_read(measured, "c2")))
        self.assertEqual(result["label"], "CORROBORATED")
        self.assertEqual(result["criteria"][0]["delivery_citation_ids"], ["c2"])

    def test_h012_independent_read_rejects_locally_unproved_benefits_after_a_positive_review(self):
        name = "Fund memberships providing priority childcare placement for Bronx employees."
        promise = "Bally's promised Bronx employees funded memberships with priority eligibility."
        corporate = "Bally's covers employee memberships at its company."
        review_claim = "The first review incorrectly concluded that Bronx employees now have priority eligibility."
        row = _criterion(name, status="DELIVERED", promise=("c1",), delivery=("c2",),
                         elements=("Bronx employees", "Funded memberships with priority eligibility"))
        check = _delivery_check(name, review_claim, support=[("c2", corporate)], in_scope=True,
                                element_support={"e1": ["c2"], "e2": ["c2"]})
        independent = _outcome_read(corporate, "c2",
                                    zero="The corporate benefit could exist while no Bronx employees have received it.",
                                    incomplete="The policy could be available only at other sites.")
        client = Mock()
        result = assess(row, [promise, corporate], _observed_outcomes(("c2", corporate)), _delivery_review(check),
                        _delivery_challenge(independent), client=client)
        self.assertEqual(client.create.call_count, 4)
        challenge_input = json.loads(client.create.call_args.kwargs["messages"][0]["content"])
        serialized = json.dumps(challenge_input)
        self.assertIn(name, serialized)
        self.assertIn(promise, serialized)
        self.assertIn(corporate, serialized)
        self.assertNotIn(review_claim, serialized)
        self.assertEqual(result["label"], "UNVERIFIED")
        self.assertEqual(result["criteria"][0]["delivery_citation_ids"], [])
        self.assertNotIn(review_claim, result["reasoning"] + result["verdict"])

    def test_tentative_partial_credit_is_removed_when_bronx_received_nothing_is_still_possible(self):
        name = "Fund priority childcare memberships for Bronx employees."
        promise = "Bally's pledged company-funded memberships with priority access for its Bronx employees."
        corporate = "Bally's offers an employee childcare membership benefit across its corporate program."
        row = _criterion(name, status="PARTIAL", promise=("c1",), delivery=("c2",))
        check = _delivery_check(name, corporate, support=[("c2", corporate)], in_scope=True,
                                gap="The number of Bronx employees reached is unknown.")
        no_bronx = "Every quoted statement remains true if the program has not yet reached any Bronx employee."
        reconsidered = _outcome_read(corporate, "c2", zero=no_bronx,
                                     incomplete="Only employees at other sites may currently have funded memberships.")
        result = assess(row, [promise, corporate], _observed_outcomes(("c2", corporate)),
                        _delivery_review(check), _delivery_challenge(reconsidered))
        checked = result["criteria"][0]
        self.assertEqual(result["label"], "UNVERIFIED")
        self.assertEqual(checked["delivery_citation_ids"], [])
        self.assertEqual(checked["context_citation_ids"], ["c2"])
        self.assertIn(corporate, result["reasoning"])
        self.assertIn(no_bronx, checked["gap"])

    def test_corroboration_challenge_cannot_erase_a_previously_established_gap(self):
        name = "Fund memberships for all 100 Bronx employees."
        promise = "All 100 Bronx employees will receive funded memberships."
        twenty = "The provider confirmed funded memberships for twenty Bronx employees."
        gap = "The remaining eighty Bronx employees have no enrollment records in the source."
        row = _criterion(name, status="PARTIAL", promise=("c1",), delivery=("c2",))
        check = _delivery_check(name, twenty, support=[("c2", twenty)], in_scope=True, gap=gap)
        overly_positive = _outcome_read("All 100 employees received memberships.", "c2",
                                        witness=_direct_witness("c2", twenty, "Bronx employees"))
        result = assess(row, [promise, twenty], _observed_outcomes(("c2", twenty)), _delivery_review(check),
                        _delivery_challenge(overly_positive))
        self.assertEqual(result["label"], "PARTIALLY_CORROBORATED")
        self.assertIn(twenty, result["verdict"])
        self.assertIn(gap, result["verdict"])
        self.assertNotIn(overly_positive["observed_fact"], result["verdict"])

    def test_cobalt_independent_read_rejects_a_date_for_an_unnamed_target(self):
        name = "Use 100% recycled cobalt in all Apple-designed batteries by 2025."
        pledge = "The goal is 100% recycled cobalt for Apple-designed batteries by 2025."
        current = "Apple-designed batteries now use 100% recycled cobalt."
        other = "In 2025 Apple reached several recycled component targets."
        row = _criterion(name, status="DELIVERED", promise=("c1",), delivery=("c2", "c3"), timing="by 2025",
                         elements=("100% recycled cobalt in Apple-designed batteries", "Target met by 2025"))
        review_claim = "Apple met the battery target by 2025."
        check = _delivery_check(name, review_claim, support=[("c2", current), ("c3", other)], timing_support=("t1",),
                                in_scope=True, element_support={"e1": ["c2"], "e2": ["c3"]})
        missing = "The specific battery target could have been achieved after 2025."
        independent = _outcome_read(current, "c2", incomplete=missing)
        result = assess(row, [pledge, current, other],
                        _observed_outcomes(("c2", current), ("c3", other), timing=[("c3", "In 2025")]),
                        _delivery_review(check), _delivery_challenge(independent))
        self.assertEqual(result["label"], "PARTIALLY_CORROBORATED")
        self.assertEqual(result["criteria"][0]["delivery_citation_ids"], ["c2"])
        self.assertEqual(result["criteria"][0]["timing_support"], [])
        self.assertNotIn("c3", result["criteria"][0]["element_support"]["e2"])
        self.assertIn(missing, result["verdict"])
        self.assertIn(current, result["verdict"])
        self.assertNotIn(review_claim, result["reasoning"] + result["verdict"])

    def test_independent_entailment_cannot_waive_source_ownership_or_an_explicit_gap(self):
        name = "Open the east route."
        promise = "The authority promised to open the east route."
        opened = "The east route opened."
        row = _criterion(name, status="DELIVERED", promise=("c1",), delivery=("c2",))
        review = _delivery_review(_delivery_check(name, opened, support=[("c2", opened)]))
        for ids, fact, gap, label in (
            (["c1"], opened, "", "UNVERIFIED"),
            (["c2"], "", "", "UNVERIFIED"),
            (["c2"], opened, "The full service extent remains undocumented.", "PARTIALLY_CORROBORATED"),
        ):
            with self.subTest(ids=ids, fact=fact, gap=gap):
                independent = _outcome_read(fact, *ids, incomplete=gap)
                result = assess(row, [promise, opened], _observed_outcomes(("c2", opened)), review,
                                _delivery_challenge(independent))
                self.assertEqual(result["label"], label)
                if label == "UNVERIFIED":
                    self.assertEqual(result["criteria"][0]["delivery_citation_ids"], [])

    def test_unavailable_independent_check_retains_evidence_without_certifying_full_delivery(self):
        name = "Open the east route."
        promise = "The authority promised to open the east route."
        opened = "The east route opened."
        row = _criterion(name, status="DELIVERED", promise=("c1",), delivery=("c2",))
        result = assess(row, [promise, opened], _observed_outcomes(("c2", opened)),
                        _delivery_review(_delivery_check(name, opened, support=[("c2", opened)])),
                        ProviderError("A simulated provider timeout containing private diagnostic details."))
        self.assertEqual(result["label"], "UNVERIFIED")
        self.assertEqual(result["criteria"][0]["promise_citation_ids"], ["c1"])
        self.assertEqual(result["criteria"][0]["delivery_citation_ids"], [])
        self.assertIn(opened, result["reasoning"])
        self.assertIn("https://transit.example/record-2", result["reasoning"])
        self.assertIn("check", result["reasoning"].lower())
        self.assertNotIn("private diagnostic", result["verdict"] + result["reasoning"])

    def test_independent_reader_must_retain_the_owner_of_each_required_element_and_the_date(self):
        for timed in (False, True):
            with self.subTest(timed=timed):
                name = "Operate east and west routes" + (" by 2025." if timed else ".")
                pledge = "The authority pledged to " + name
                first = "The east and west routes are now operating." if timed else "East service is operating."
                second = "Both routes opened in 2025." if timed else "West service is operating."
                elements = ("Both routes operating",) if timed else ("East operating", "West operating")
                owners = {"e1": ["c2"]} if timed else {"e1": ["c2"], "e2": ["c3"]}
                row = _criterion(name, status="DELIVERED", promise=("c1",), delivery=("c2", "c3"),
                                 elements=elements, timing="by 2025" if timed else "")
                check = _delivery_check(name, "Both grounds were verified.", support=[("c2", first), ("c3", second)],
                                        element_support=owners, timing_support=("t1",) if timed else ())
                timing = [("c3", "in 2025")] if timed else []
                result = assess(row, [pledge, first, second], _observed_outcomes(("c2", first), ("c3", second), timing=timing),
                                _delivery_review(check), _delivery_challenge(_outcome_read(
                                    "The whole promise was met.", "c2", witness=_direct_witness("c2", first, first))))
                self.assertEqual(result["label"], "PARTIALLY_CORROBORATED")
                self.assertEqual(result["criteria"][0]["delivery_citation_ids"], ["c2"])
                self.assertEqual(result["criteria"][0]["timing_support"], [])
                self.assertIn(first, result["verdict"])
                self.assertNotIn("The whole promise was met.", result["verdict"])

    def test_rejected_refutation_preserves_independently_verified_delivery_in_the_public_result(self):
        pledge = "The authority pledged to begin east and west overnight services."
        opened = "The east overnight service began operating."
        update = "The authority is considering a timetable change for the west service."
        supports = [_support("undertaking", pledge), _support("opening", opened), _support("timetable", update)]
        snapshot = _snapshot(*[
            {"id": f"f{n}", "claim": support["excerpt"], "supports": [support]}
            for n, support in enumerate(supports, 1)
        ])
        query = "Were both east and west overnight services delivered?"
        context = {"requested_query": query, "commitment": {"description": pledge}}
        east = _criterion("Begin east service", status="DELIVERED", promise=("c1",), delivery=("c2",))
        west = _criterion("Begin west service", status="REFUTED", promise=("c1",))
        initial = _final_assessment(east, west, label="REFUTED")
        initial.update(verdict="All overnight service was cancelled.",
                       refutation=[{"basis": "DIRECT_DISPROOF", "finding_id": "f3", "quote": update}])
        client = Mock()
        client.create.side_effect = [
            _json_message(initial), _observed_outcomes(("c2", opened)),
            _delivery_review(_delivery_check(east["criterion"], opened, support=[("c2", opened)])),
            _delivery_challenge(_outcome_read(opened, "c2")),
        ]
        verifier = Mock(return_value=False)
        result = EvidenceReconciler(Mock(return_value=_plan()), assessor=ClaudeReconciliationJudge(client).assess,
                                    verify_refutation=verifier).reconcile(context, snapshot, _research())
        verifier.assert_called_once_with(context, supports[2], "DIRECT_DISPROOF", update)
        self.assertEqual(result["label"], "PARTIALLY_CORROBORATED")
        self.assertIn(opened, result["verdict"])
        self.assertIn("Delivery evidence: c2", result["reasoning"])
        self.assertIn(supports[1]["source"]["url"], result["reasoning"])
        self.assertIn(query, result["reasoning"])
        self.assertNotIn(initial["verdict"], result["verdict"] + result["reasoning"])

    def test_h008_amended_amount_remains_documented_when_payment_is_unverified(self):
        name = "Pay the amended annual public-safety contribution."
        pledge = "Bally's initially committed $3 million annually for public safety."
        amendment = "Bally's increased the annual pledge from $3 million to $5 million."
        row = _criterion(name, promise=("c1", "c2"), elements=("Payment of the amended annual $5 million",))
        result = assess(row, [pledge, amendment])
        self.assertEqual(result["label"], "UNVERIFIED")
        self.assertEqual(result["criteria"][0]["promise_citation_ids"], ["c1", "c2"])
        self.assertEqual(result["criteria"][0]["delivery_citation_ids"], [])
        self.assertIn(amendment, result["reasoning"])
