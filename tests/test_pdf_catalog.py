import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless

from tests.db_support import isolated_database
from tracegraph.pdf_extract import anchored, load_document, source_units


def make_pdf(path, text):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(600, 800)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                             NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({
        NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 10 Tf 30 750 Td ({text}) Tj ET".encode('ascii'))
    page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(path)


def sample(path, sentence="Example Transit Authority will operate an overnight electric shuttle."):
    make_pdf(path, sentence)
    doc = load_document(path)
    units = source_units(doc)
    return anchored(doc, units, 0, len(units), {"organization": "Example Transit Authority",
                    "description": sentence, "citations": [0]})


def seed_pdf(dsn):
    from tracegraph.evidence_store import EvidenceStore
    from tracegraph.pdf_import import publish

    with TemporaryDirectory() as tmp:
        path = Path(tmp) / 'transit-plan.pdf'
        row = sample(path)
        report = publish(EvidenceStore(dsn), [row], [path], lambda _: {})
        return next(iter(report.values()))['commitment_id']


class PdfIdentityTests(TestCase):
    def test_similar_topic_and_transitive_match_do_not_collapse_distinct_duties(self):
        from tracegraph.pdf_import import resolve_batch

        class EmptyCatalog:
            def candidates(self, value):
                return value, "1", []
        rows = [{"organization": "Authority", "description": text} for text in (
            "Hire a monitor", "File the monitor's public report", "Publish that monitoring report")]
        proposed = {"item-0":"NEW", "item-1":"item-0", "item-2":"item-1"}
        def verify(pairs):
            self.assertEqual(set(pairs), {"item-1/item-0","item-2/item-1","item-2/item-0"})
            return {key: "SAME" if key == "item-2/item-1" else "DISTINCT" for key in pairs}
        _, decisions = resolve_batch(EmptyCatalog(), lambda _: proposed.copy(), rows, verify)
        self.assertEqual(decisions, {"item-0":"NEW", "item-1":"NEW", "item-2":"item-1"})


@skipUnless(os.getenv('TEST_DATABASE_URL'), 'PostgreSQL integration requires TEST_DATABASE_URL')
class PdfCatalogTests(TestCase):
    def test_import_repeat_link_concurrent_lookup_failure_retry_and_completion(self):
        from tracegraph.pdf_import import publish, item_key
        from tracegraph.runtime import create_application, prepare_catalog

        with isolated_database(os.environ['TEST_DATABASE_URL']) as dsn, TemporaryDirectory() as tmp:
            prepare_catalog(dsn)
            app = create_application(dsn, mode="fixture")
            p1, p2 = Path(tmp)/'plan.pdf', Path(tmp)/'agreement.pdf'
            first = sample(p1)
            second = sample(p2, "Our agency will deliver the overnight electric shuttle service.")
            def judge(packet):
                return {name: "item-0" for name in packet} | {"item-0": "NEW"}
            report = publish(app.store, [first, second], [p1, p2], judge)
            cid = report[item_key(first)]['commitment_id']
            self.assertEqual(cid, report[item_key(second)]['commitment_id'])
            from tracegraph.providers import _refresh_target
            self.assertEqual(_refresh_target(app.store.load_commitment(cid))['original_text'],
                             first['citations'][0]['excerpt'])
            repeat = publish(app.store, [first, second], [p1, p2], lambda _: self.fail("paid on replay"))
            self.assertEqual({r['status'] for r in repeat.values()}, {'EXISTING'})
            graph = app.store.graph(cid)
            self.assertEqual((graph['research_state'], graph['assessment']), ('NOT_STARTED', None))
            self.assertEqual(len(graph['document_citations']), 2)
            doc_url = '/api/v1/documents/'+first['document_sha256']+'/pdf'
            self.assertEqual(app.respond('GET', doc_url, {})[2], p1.read_bytes())
            with self.assertRaises(ValueError):
                publish(app.store, [{**first, 'citations': [{**first['citations'][0],'excerpt':'fabricated'}]}],
                        [p1], lambda _: self.fail())
            def lookup(n):
                body = {'query': first['description']} if n%2 else {'commitment_id':cid}
                return app.respond('POST','/api/v1/lookup',{'Idempotency-Key':str(n)},
                                   json.dumps(body).encode())[2]
            with ThreadPoolExecutor(4) as pool:
                jobs = {r['job_id'] for r in pool.map(lookup,range(8))}
            self.assertEqual(len(jobs),1)
            claim = app.store.claim_next()
            app.store.fail(claim, {'code':'FAILED','message':'Test retry','retryable':True})
            failed = app.store.graph(cid)
            self.assertEqual((failed['research_state'],failed['assessment']),('FAILED',None))
            self.assertNotIn(lookup(9)['job_id'],jobs)
            self.assertTrue(app.process_one_job())
            self.assertEqual(app.store.graph(cid)['research_state'],'ASSESSED')
            self.assertEqual(lookup(10),{'outcome':'MATCH','commitment_id':cid})
