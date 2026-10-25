"""DevOps repository auditing agent.

Usage: python agent.py --repo <github_repository_url> --output report.json
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

from core import Auditor
from llm import LLM, LLMError
from tools import RepoTools

GITHUB_URL = re.compile(r"^https?://(www\.)?github\.com/([\w.-]+)/([\w.-]+?)(\.git)?/?$")
CLONE_TIMEOUT = 300


class FatalError(Exception):
    pass


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def normalize_url(url):
    m = GITHUB_URL.match(url.strip())
    if not m:
        raise FatalError(f"Invalid repository URL '{url}'. Expected https://github.com/<owner>/<repo>")
    return f"https://github.com/{m.group(2)}/{m.group(3)}"


def clone(url, dest):
    """Shallow-clone without running hooks, LFS filters, submodules or creating symlinks."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1"}
    cmd = ["git", "-c", "core.symlinks=false", "-c", "core.hooksPath=/dev/null",
           "-c", "protocol.file.allow=never", "clone", "--quiet", "--depth", "200",
           "--no-tags", url + ".git", str(dest)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=CLONE_TIMEOUT, env=env)
    except FileNotFoundError:
        raise FatalError("git is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        raise FatalError(f"Cloning {url} timed out after {CLONE_TIMEOUT}s")
    if proc.returncode != 0:
        raise FatalError(f"Could not clone {url} (does it exist and is it public?): {proc.stderr.strip()}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="AI agent that audits a GitHub repository for DevOps violations.")
    parser.add_argument("--repo", required=True, help="Public GitHub repository URL")
    parser.add_argument("--output", required=True, help="Path of the JSON report to write")
    parser.add_argument("--max-steps", type=int, default=40, help="Maximum LLM turns (default 40)")
    parser.add_argument("--trace", help="Where to write the tool-call trace (default: <output>.trace.json)")
    parser.add_argument("--quiet", action="store_true", help="Do not log agent steps to stderr")
    args = parser.parse_args(argv)

    load_dotenv(Path(__file__).resolve().parent / ".env")
    output = Path(args.output)
    trace_path = Path(args.trace) if args.trace else output.with_suffix(".trace.json")
    try:
        url = normalize_url(args.repo)
        llm = LLM()
        with tempfile.TemporaryDirectory(prefix="audit-") as tmp:
            repo_dir = Path(tmp) / "repo"
            log(f"Cloning {url} ...")
            clone(url, repo_dir)
            log(f"Auditing with model {llm.model} ...")
            auditor = Auditor(llm, RepoTools(repo_dir), url, max_steps=args.max_steps,
                              log=(lambda m: None) if args.quiet else log)
            report = auditor.run()

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n")
        trace_path.write_text(json.dumps({"repository": url, "model": llm.model, "steps": auditor.trace}, indent=2))
    except (FatalError, LLMError) as e:
        log(f"ERROR: {e}")
        return 1
    except KeyboardInterrupt:
        log("Interrupted.")
        return 130
    except OSError as e:
        log(f"ERROR: {e}")
        return 1

    log(f"Wrote {len(report['violations'])} violations to {output} (trace: {trace_path})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
