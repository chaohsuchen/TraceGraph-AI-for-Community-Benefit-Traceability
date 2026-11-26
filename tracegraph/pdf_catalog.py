"""Command-line entry point for trusted PDF extraction and catalog import."""
import argparse
import runpy
import sys


# Keep extraction on the exact implementation pinned by the release manifest.
COMMANDS = {
    "extract": "evaluation.pdf-extraction-101.all_documents.extract",
    "import": "tracegraph.pdf_import",
}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m tracegraph.pdf_catalog", description=__doc__,
                                     epilog="Use extract --help or import --help for command options.")
    parser.add_argument("command", choices=COMMANDS)
    command = parser.parse_args(sys.argv[1:2]).command
    sys.argv = [f"{parser.prog} {command}", *sys.argv[2:]]
    runpy.run_module(COMMANDS[command], run_name="__main__")
