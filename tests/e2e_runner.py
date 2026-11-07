"""Launch an isolated database, backend, and frontend for browser E2E tests."""

import argparse
from contextlib import contextmanager
import os
from pathlib import Path
from queue import Empty, Queue
import re
import subprocess
import sys
from threading import Thread

from tests.db_support import isolated_database
from tracegraph.runtime import LOCAL_DSN, ROOT


@contextmanager
def local_server(command, env, readiness):
    child = subprocess.Popen(
        command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    ready = Queue()

    def read_output():
        found = False
        for line in child.stdout:
            if not found and (match := readiness.search(line)):
                ready.put(match.group(0))
                found = True
            elif not found:
                print(line.rstrip(), file=sys.stderr)
        if not found:
            ready.put(None)

    reader = Thread(target=read_output, daemon=True)
    reader.start()
    try:
        try:
            address = ready.get(timeout=30)
        except Empty:
            raise RuntimeError("Local service did not start in 30 seconds.") from None
        if not address:
            raise RuntimeError("Local service exited before it was ready.")
        yield address
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        reader.join(timeout=5)
        child.stdout.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--anthropic-key-file", type=Path)
    parser.add_argument("--anthropic-model")
    parser.add_argument("--database-url", default=os.getenv("TEST_DATABASE_URL", LOCAL_DSN))
    args = parser.parse_args()
    mode = "live" if args.live else "fixture"
    url = re.compile(r"http://127\.0\.0\.1:[1-9]\d*")
    command = [sys.executable, "-B", "-m", "tracegraph.runtime"]
    with isolated_database(args.database_url) as dsn:
        env = {**os.environ, "TRACEGRAPH_DATABASE_URL": dsn, "TRACEGRAPH_E2E_MODE": mode}
        subprocess.run([*command, "prepare"], cwd=ROOT, env=env, check=True)
        backend = [*command, "serve", "--mode", mode, "--port", "0"]
        if args.anthropic_key_file:
            backend.extend(("--anthropic-key-file", str(args.anthropic_key_file.resolve())))
        if args.anthropic_model:
            backend.extend(("--anthropic-model", args.anthropic_model))
        front_env = {key: value for key, value in env.items() if key != "ANTHROPIC_API_KEY"}
        with local_server(backend, env, url) as api:
            with local_server(["node", "frontend/dev.mjs"], {**front_env, "TRACEGRAPH_BACKEND": api, "PORT": "0"}, url) as front:
                subprocess.run(
                    ["node", "tests/browser_e2e.mjs"], cwd=ROOT,
                    env={**front_env, "TRACEGRAPH_E2E_URL": front}, check=True,
                )


if __name__ == "__main__":
    main()
