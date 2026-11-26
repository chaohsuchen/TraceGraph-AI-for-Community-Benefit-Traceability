from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from tests.test_pdf_catalog import sample
from tracegraph.corpus import load_document
from tracegraph.tender_review import CONFIRM_PROMPT, GENERAL_PROMPT, ROLE_PROMPT, VERIFY_PROMPT, filtered, review


class TenderReviewTests(TestCase):
    def test_only_archived_tender_rows_are_removed(self):
        with TemporaryDirectory() as tmp:
            docs, rows = {}, []
            for i, sentence in enumerate(["Our procurement agency invites applications.",
                                          "The evaluation board selected the project."]):
                path = Path(tmp) / f"{i}.pdf"
                r = sample(path, sentence)
                doc = load_document(path)
                docs[doc.file_hash] = doc
                rows.append(r)
            template = rows[0]
            rows += [{**template, "description": s} for s in ("Post-award public obligation", "Mixed clause")]
            def respond(client, prompt, packet, schema, limit):
                if prompt == ROLE_PROMPT:
                    return {"role": "SOLICITATION" if "invites" in packet["opening"] else "OTHER",
                            "reason": "Document purpose"}
                categories = {rows[0]["description"]: "ARCHIVE", rows[2]["description"]: "KEEP",
                              rows[3]["description"]: "REVIEW"}
                return {k: {"decision": categories[v["candidate"]], "passage_id": "q0", "reason": "Evidence"}
                        for k, v in packet.items()}
            with patch("tracegraph.tender_review.structured", respond):
                audit = review(rows, docs, SimpleNamespace(model="fixture"))
            self.assertEqual(filtered(rows, audit), rows[1:])
            self.assertEqual(len(audit["decisions"]), 3)

    def test_uncertain_document_passes_through_and_invalid_citations_fail(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "document.pdf"
            row = sample(path)
            doc = load_document(path)
            args = [row], {doc.file_hash: doc}, SimpleNamespace(model="fixture")
            with patch("tracegraph.tender_review.structured", return_value={"role":"UNCERTAIN","reason":"Mixed"}) as call:
                self.assertEqual(filtered([row], review(*args)), [row])
                self.assertEqual(call.call_count, 1)
            with patch("tracegraph.tender_review.structured", side_effect=[
                {"role":"SOLICITATION","reason":"Call for bids"},
                {"r0":{"decision":"ARCHIVE","passage_id":"unknown","reason":"Bid paperwork"}}]):
                with self.assertRaises(ValueError):
                    review(*args)

    def test_all_scope_reviews_every_document_and_only_filters_existing_rows(self):
        with TemporaryDirectory() as tmp:
            docs, rows = {}, []
            for name in ["Solicitation", "Selection", "License", "Mixed"]:
                path = Path(tmp) / f"{name}.pdf"
                row = sample(path, f"{name} source text.")
                doc = load_document(path)
                docs[doc.file_hash] = doc
                rows.append(row)
            def respond(client, prompt, packet, schema, limit):
                if prompt == ROLE_PROMPT:
                    opening = packet["opening"]
                    role = "SOLICITATION" if "Solicitation" in opening else "UNCERTAIN" if "Mixed" in opening else "OTHER"
                    return {"role": role, "reason": "Source type"}
                out = {}
                for key, value in packet.items():
                    name = value["candidate"].split()[0]
                    if prompt == CONFIRM_PROMPT:
                        self.assertIn(name, {"Selection", "License"})
                        decision = "ARCHIVE" if name == "Selection" else "REVIEW"
                    else:
                        self.assertEqual(prompt, VERIFY_PROMPT if name == "Solicitation" else GENERAL_PROMPT)
                        decision = {"Selection":"ARCHIVE", "License":"ARCHIVE", "Mixed":"REVIEW"}.get(name,"KEEP")
                    out[key] = {"decision": decision, "passage_id":"q0", "reason":"Source assessment"}
                return out
            with patch("tracegraph.tender_review.structured", respond):
                first = review(rows, docs, SimpleNamespace(model="fixture"), scope="all")
                audit = review(rows, docs, SimpleNamespace(model="fixture"), scope="all", confirm=True)
            self.assertEqual(filtered(rows, first), [rows[0], rows[3]])
            kept = filtered(rows, audit)
            self.assertEqual(len(audit["decisions"]), 4)
            self.assertEqual(len(audit["archive_checks"]), 2)
            self.assertEqual(kept, [rows[0], rows[2], rows[3]])
            self.assertIs(kept[-1], rows[-1])
