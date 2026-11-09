from contextlib import nullcontext, redirect_stdout
from hashlib import sha256
from io import StringIO
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from tracegraph import research_experiment as experiment


class ResearchExperimentTests(TestCase):
    def test_variant_loader_uses_exact_source_and_shared_package_without_writing_to_checkout(self):
        module_name = "tracegraph.experimental_prompt"
        with TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory, patch.dict(sys.modules):
            root = Path(directory)
            module_path = root / "tracegraph" / "experimental_prompt.py"
            module_path.parent.mkdir()
            source = (
                b"from .runtime import LOCAL_DSN\n"
                b"def search(researcher, request, *, emit):\n    return LOCAL_DSN, {}\n"
                b"def evaluate_capture(researcher, request, brief, citations, *, emit):\n    return brief, citations\n"
            )
            module_path.write_bytes(source)
            loaded, metadata = experiment._load_variant("prompt", root)
            self.assertIs(sys.modules[module_name], loaded)
            self.assertEqual(loaded.search(None, {}, emit=None), (experiment.LOCAL_DSN, {}))
            self.assertEqual(metadata["sha256"], sha256(source).hexdigest())
            self.assertEqual(list(module_path.parent.iterdir()), [module_path])

    def test_schedule_rotates_starting_variant_between_cases(self):
        self.assertEqual(list(experiment._schedule(experiment.QUESTIONS, experiment.VARIANTS)), [
            ("H009", "control"), ("H009", "prompt"), ("H009", "langgraph"),
            ("H011", "prompt"), ("H011", "langgraph"), ("H011", "control"),
            ("H012", "langgraph"), ("H012", "control"), ("H012", "prompt"),
            ("H016", "control"), ("H016", "prompt"), ("H016", "langgraph"),
        ])

    def test_replay_uses_only_passages_from_even_a_failed_control_job(self):
        case = "H012"
        capture = {
            "type": "research_capture",
            "brief": "An earlier model conclusion.",
            "citations": {"c1": {
                "url": "https://example.org/official",
                "title": "Official report",
                "excerpt": "A literal source passage.",
                "context": "Earlier model prose.",
            }},
        }
        base = {"case": case, "query": experiment.QUESTIONS[case], "status": "FAILED"}
        report = {"mode": "live", "model": experiment.MODEL, "runs": [
            {**base, "variant": "prompt", "events": [capture]},
            {**base, "variant": "control", "events": [capture]},
        ]}
        brief, citations = experiment._replay_captures(report)[case]
        self.assertEqual(brief, "[c1] Official report\nA literal source passage.")
        self.assertEqual(citations["c1"]["context"], "A literal source passage.")

    def test_failed_job_retains_capture_and_resume_does_not_repeat_attempt(self):
        with TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory, redirect_stdout(StringIO()):
            output = Path(directory) / "live.json"
            app = Mock()
            app.respond.return_value = 202, {}, {"job_id": "job-1"}
            app.store.get_job.return_value = {"status": "FAILED", "error": {"code": "RESEARCH_FAILED"}}

            def application(dsn, mode, **kwargs):
                emit = kwargs["emit"]
                self.assertTrue(kwargs["enable_conflict_review"])
                self.assertTrue(kwargs["allow_web_search"])

                def process():
                    self.assertEqual(experiment._read_report(output)["runs"][0]["status"], "RUNNING")
                    emit({"type": "research_start"})
                    emit({"type": "provider_call", "usage": {"input_tokens": 2}, "search_enabled": True})
                    emit({"type": "research_capture", "brief": "Public passage.", "citations": {}})
                    return True

                app.process_one_job.side_effect = process
                return app

            with patch.object(experiment, "evaluation_catalog", side_effect=lambda _: nullcontext("isolated")), \
                    patch.object(experiment, "create_application", side_effect=application) as factory:
                options = {
                    "mode": "live", "output": output, "dsn": "local", "key_file": None, "roots": {},
                    "cases": {"H012"}, "variants": ("control",),
                }
                report = experiment.run_experiment(**options)
                record = report["runs"][0]
                self.assertEqual(record["status"], "FAILED")
                self.assertEqual(record["failure_stage"], "after_capture")
                self.assertEqual(record["events"][1]["usage"], {"input_tokens": 2})
                self.assertIsNotNone(record["timings"]["research_seconds"])
                record["status"] = "RUNNING"
                experiment._write_checkpoint(output, report)
                resumed = experiment.run_experiment(**options)
                self.assertEqual(resumed["runs"][0]["status"], "INTERRUPTED")
                factory.assert_called_once()

    def test_replay_disables_web_search_and_flags_an_attempt(self):
        record = {"query": experiment.QUESTIONS["H009"], "events": [], "graph": None}
        app = Mock()
        app.respond.return_value = 202, {}, {"job_id": "job-2"}
        app.store.get_job.return_value = {"status": "COMPLETED", "result": {"outcome": "NO_PUBLIC_PROMISE"}}

        def application(dsn, mode, **kwargs):
            self.assertFalse(kwargs["enable_conflict_review"])
            self.assertFalse(kwargs["allow_web_search"])
            kwargs["emit"]({"type": "provider_call", "search_enabled": True, "usage": None})
            return app

        with patch.object(experiment, "evaluation_catalog", side_effect=lambda _: nullcontext("isolated")), \
                patch.object(experiment, "create_application", side_effect=application):
            experiment._run_job(record, "local", None, None, "replay", lambda: None)
        self.assertEqual(record["status"], "FAILED")
        self.assertEqual(record["error"], "WEB_SEARCH_ATTEMPTED")
