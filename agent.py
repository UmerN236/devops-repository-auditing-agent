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
from report import redact
from tools import RepoTools

GITHUB_URL = re.compile(r"^https?://(www\.)?github\.com/([\w.-]+)/([\w.-]+?)(\.git)?/?$")
CLONE_TIMEOUT = 300


class FatalError(Exception):
    pass


def log(msg):
    print(redact(str(msg)), file=sys.stderr, flush=True)


def normalize_url(url):
    m = GITHUB_URL.match(url.strip())
    if not m:
        raise FatalError(f"Invalid repository URL '{url}'. Expected https://github.com/<owner>/<repo>")
    return f"https://github.com/{m.group(2)}/{m.group(3)}"


def clone(url, dest):
    """Shallow-clone without running hooks, LFS filters, submodules or creating symlinks."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
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


def write_json(path, data):
    """Replace an output atomically so interrupted writes cannot leave invalid JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.' + path.name,
                                         suffix='.tmp', delete=False, encoding='utf-8') as stream:
            temp_path = Path(stream.name)
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
        temp_path.replace(path)
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)


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
        if args.max_steps < 1:
            raise FatalError('--max-steps must be at least 1')
        if output.resolve() == trace_path.resolve():
            raise FatalError('--output and --trace must be different paths')
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
            revision = auditor.tools._git('rev-parse', 'HEAD').strip()

        write_json(trace_path, {"repository": url, "revision": revision, "model": llm.model,
                               "completed": auditor.done, "steps": auditor.trace})
        if not auditor.done:
            raise FatalError(f'Audit incomplete: step budget exhausted. Increase --max-steps; trace: {trace_path}')
        write_json(output, report)
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
