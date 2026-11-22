"""Verify the default model through the live application and evaluation entry points."""
import os
import unittest
from unittest.mock import patch

from tracegraph import nli_eval, nli_retrieval_eval, runtime


class ModelDefaultTests(unittest.TestCase):
    def test_live_clients_use_full_gpt54(self):
        with (
            patch.dict(os.environ, {"OPENAI_MODEL": ""}),
            patch.object(runtime, "check_embedding_profile"),
            patch.object(runtime, "read_credential", return_value="offline") as credential,
        ):
            agent = runtime.create_application("unused", "live").agent
        credential.assert_called_once_with(None, provider="openai")
        clients = (
            agent._retrieval._judge._client,
            agent._researcher._search_client,
            agent._researcher._client,
            agent._reconciler._judge._client,
            agent._reconciler._assessor.__self__._client,
        )
        self.assertEqual([client.model for client in clients], ["gpt-5.4"] * len(clients))

    def test_serve_infers_provider_from_explicit_options(self):
        for options, provider in (([], "openai"), (["--anthropic-model", "alternate"], "anthropic")):
            with (
                self.subTest(options=options),
                patch.object(runtime, "scoped_database", return_value="unused"),
                patch.object(runtime, "create_application") as create,
                patch.object(runtime, "serve"),
            ):
                runtime.main(["serve", *options])
                self.assertEqual(create.call_args.kwargs["provider"], provider)

    def test_evaluation_entry_points_use_full_gpt54(self):
        for action, handler in (("research", "_research"), ("judge", "_judge")):
            with self.subTest(action=action), patch("sys.argv", ["nli_eval", action, "--run-dir", "."]), patch.object(nli_eval, handler) as run:
                nli_eval.main()
                self.assertEqual(run.call_args.args[0].model, "gpt-5.4")
        with (
            patch("sys.argv", ["nli_retrieval_eval", "--run-dir", "."]),
            patch.object(nli_retrieval_eval, "evaluate", return_value={"case_id": "offline", "capture_status": "empty", "score": None}) as run,
            patch("builtins.print"),
        ):
            nli_retrieval_eval.main()
            self.assertEqual(run.call_args.args[1], "gpt-5.4")
