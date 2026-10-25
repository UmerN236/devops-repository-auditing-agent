"""Read-only inspection tools the agent can call on a cloned repository.

Every tool returns plain text for the LLM to interpret. Tools never modify the
repository and never execute code from it (Python files are only parsed with
`ast`). Likely secret values are masked before any output leaves a tool.
"""

import ast
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

from report import redact

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib

MAX_LIST = 400          # files shown by list_files
MAX_READ_LINES = 300    # lines returned per read_file call
MAX_LINE_CHARS = 300    # long lines are cut to this length
MAX_MATCHES = 80        # search_code results
MAX_FILE_BYTES = 1_000_000  # files larger than this are not read/searched
MAX_OUTPUT_CHARS = 20_000
GIT_TIMEOUT = 60
SEARCH_TIMEOUT = 10


class ToolError(Exception):
    """A tool failed in a way the agent should be told about."""


class RepoTools:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.inspected = set()  # files whose content the agent has actually seen
        self.observed_lines = {}  # path -> current-tree lines actually returned to the LLM
        self._files = None

    # ------------------------------------------------------------------ helpers
    def _git(self, *args):
        try:
            proc = subprocess.run(
                ["git", "-C", str(self.root), *args],
                capture_output=True, text=True, timeout=GIT_TIMEOUT,
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            raise ToolError(f"git {args[0]} timed out")
        if proc.returncode != 0:
            raise ToolError(f"git {args[0]} failed: {proc.stderr.strip()[:300]}")
        return proc.stdout

    def files(self):
        """All tracked files (relative POSIX paths)."""
        if self._files is None:
            out = self._git("ls-files", "-z")
            self._files = sorted(f for f in out.split("\0") if f)
        return self._files

    def _resolve(self, path):
        if not isinstance(path, str):
            raise ToolError('path must be a string')
        if Path(path).is_absolute():
            raise ToolError('path escapes the repository: use a relative path')
        path = path.strip().removeprefix("./")
        full = (self.root / path).resolve()
        if full != self.root and self.root not in full.parents:
            raise ToolError("path escapes the repository")
        return path, full

    def _tracked_file(self, path):
        path, full = self._resolve(path)
        if path not in self.files() or not full.is_file():
            raise ToolError(f"tracked file not found: {path}")
        return path, full

    def _observe(self, path, lines):
        self.inspected.add(path)
        self.observed_lines.setdefault(path, set()).update(lines)

    def _read_text(self, full):
        if full.stat().st_size > MAX_FILE_BYTES:
            return None
        data = full.read_bytes()
        if b"\0" in data[:8000]:
            return None
        return data.decode("utf-8", errors="replace")

    # -------------------------------------------------------------------- tools
    def list_files(self, directory=""):
        directory, _ = self._resolve(directory)
        prefix = directory.rstrip("/") + "/" if directory else ""
        files = [f for f in self.files() if f.startswith(prefix)]
        if not files:
            return f"No tracked files under '{directory or '.'}'."

        lines = [f"{len(files)} tracked files under '{directory or '.'}':"]
        for f in files[:MAX_LIST]:
            size = (self.root / f).stat().st_size if (self.root / f).is_file() else 0
            lines.append(f + (f"  ({size / 1e6:.1f} MB)" if size > 1e6 else ""))
        if len(files) > MAX_LIST:
            counts = {}
            for f in files:
                top = "/".join(f[len(prefix):].split("/")[:2])
                counts[top] = counts.get(top, 0) + 1
            lines.append(f"... {len(files) - MAX_LIST} more. File counts by directory:")
            lines += [f"  {d}: {n}" for d, n in sorted(counts.items(), key=lambda x: -x[1])[:40]]
            lines.append("Call list_files with a subdirectory to see more.")

        if not directory:
            ignored = self._git("ls-files", "-ci", "--exclude-standard").split("\n")
            ignored = [f for f in ignored if f]
            if ignored:
                lines.append(f"\nTracked files that match .gitignore rules ({len(ignored)}):")
                lines += ignored[:30]
        return "\n".join(lines)

    def read_file(self, path, start_line=1, end_line=None):
        path, full = self._tracked_file(path)
        text = self._read_text(full)
        if text is None:
            return f"{path}: binary or larger than {MAX_FILE_BYTES} bytes ({full.stat().st_size} bytes); not shown."
        lines = redact(text).splitlines()
        start = max(1, int(start_line or 1))
        end = min(len(lines), int(end_line) if end_line else start + MAX_READ_LINES - 1)
        end = min(end, start + MAX_READ_LINES - 1)
        body, shown, size = [], [], 0
        for i in range(start, end + 1):
            row = f"{i}: {lines[i - 1][:MAX_LINE_CHARS]}"
            if size + len(row) > MAX_OUTPUT_CHARS:
                break
            body.append(row)
            shown.append(i)
            size += len(row) + 1
        self._observe(path, shown)
        end = shown[-1] if shown else start - 1
        body = "\n".join(body)
        header = f"{path} (lines {start}-{end} of {len(lines)})"
        more = f"\n... file continues; call read_file with start_line={end + 1}" if end < len(lines) else ""
        return f"{header}\n{body}{more}" if lines else f"{path} is empty."

    def search_code(self, pattern, file_glob=None, ignore_case=True):
        try:
            regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as e:
            raise ToolError(f"invalid regex: {e}")
        candidates = []
        for f in self.files():
            if file_glob and not (fnmatch.fnmatch(f, file_glob) or fnmatch.fnmatch(Path(f).name, file_glob)):
                continue
            _, full = self._resolve(f)
            if not full.is_file():
                continue
            candidates.append((f, str(full)))
        request = {'pattern': regex.pattern, 'ignore_case': ignore_case, 'files': candidates,
                   'max_file_bytes': MAX_FILE_BYTES, 'max_line_chars': MAX_LINE_CHARS,
                   'max_matches': MAX_MATCHES, 'max_output_chars': MAX_OUTPUT_CHARS}
        try:
            process = subprocess.run([sys.executable, str(Path(__file__).with_name('search_worker.py'))],
                                     input=json.dumps(request), capture_output=True, text=True, timeout=SEARCH_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise ToolError(f'search timed out after {SEARCH_TIMEOUT}s; simplify the regex or narrow file_glob')
        if process.returncode:
            raise ToolError('search worker failed; simplify the pattern or narrow file_glob')
        data = json.loads(process.stdout)
        results, total = [], data['total']
        for match in data['matches']:
            results.append(match['text'])
            self._observe(match['path'], [match['line']])
        skipped = (f'\nSkipped {data["skipped_count"]} binary, oversized or unreadable files '
                   f'(examples: {", ".join(data["skipped"])}).' if data['skipped_count'] else '')
        if not results:
            return f"No matches for /{pattern}/" + (f" in files matching {file_glob}" if file_glob else "") + skipped
        extra = (f"\n... {total - len(results)} more matches not shown; narrow the pattern or glob."
                 if total > len(results) else "")
        return f"{total} matches for /{pattern}/:\n" + "\n".join(results) + extra + skipped

    def git_log(self, max_count=20, path=None, search=None):
        max_count = max(1, min(int(max_count or 20), 100))
        args = ["log", f"-n{max_count}", "--date=short", "--format=commit %H%n%ad %an: %s",
                "--no-ext-diff", "--no-textconv", "--stat=120,60"]
        if search:
            args.append(f"-G{search}")
            args += ["--patch", "--unified=2"]
        if path:
            args += ["--", self._resolve(path)[0]]
        log = self._git(*args).strip()

        branch = self._git("rev-parse", "--abbrev-ref", "HEAD").strip()
        try:
            remote = self._git("ls-remote", "--heads", "origin")
            branches = sorted(line.split("refs/heads/", 1)[1] for line in remote.splitlines() if "refs/heads/" in line)
        except ToolError:
            branches = []
        authors = self._git("shortlog", "-sn", "HEAD").strip().splitlines()
        header = (
            f"Default branch: {branch}\n"
            f"Remote branches: {', '.join(branches[:30]) or 'unknown'}\n"
            f"Authors (commits in clone history): {'; '.join(a.strip() for a in authors[:10])}\n"
        )
        shallow = self._git('rev-parse', '--is-shallow-repository').strip()
        header += f"Shallow history: {shallow} (only cloned commits are available)\n"
        result = redact(header + "\n" + (log or "No commits matched."))
        return result[:MAX_OUTPUT_CHARS] + ('\n... history output truncated; narrow search/path.'
                                           if len(result) > MAX_OUTPUT_CHARS else '')

    def check_syntax(self, path):
        path, full = self._tracked_file(path)
        text = self._read_text(full)
        if text is None:
            return f"{path}: binary or too large to check."
        suffix = full.suffix.lower()
        try:
            if suffix == ".py":
                ast.parse(text, filename=path)
            elif suffix in (".yml", ".yaml"):
                list(yaml.safe_load_all(text))
            elif suffix == ".json":
                json.loads(text)
            elif suffix == ".toml" and tomllib:
                tomllib.loads(text)
            else:
                return f"{path}: no syntax checker for '{suffix}' files."
        except Exception as e:  # each parser raises its own error type
            return f"{path}: SYNTAX ERROR: {redact(str(e))[:500]}"
        return f"{path}: syntax OK."


# OpenAI-style function schemas for the inspection tools.
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List tracked files in the repository (or a subdirectory). At the root it also "
                           "lists tracked files that match .gitignore rules. Files over 1 MB show their size.",
            "parameters": {
                "type": "object",
                "properties": {"directory": {"type": "string", "description": "Subdirectory, '' for root."}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": f"Read a text file with line numbers (at most {MAX_READ_LINES} lines per call).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "description": "1-based, default 1"},
                    "end_line": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Regex search (Python syntax) over all tracked text files. Returns path:line: text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "file_glob": {"type": "string", "description": "Optional glob, e.g. '*.py' or '.github/workflows/*'"},
                    "ignore_case": {"type": "boolean", "description": "Default true"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_log",
            "description": "Show commit history with changed-file stats, plus default branch, remote branches "
                           "and authors. 'search' finds commits whose diff added/removed lines matching a regex "
                           "(useful for secrets removed from the current tree but still in history).",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_count": {"type": "integer", "description": "Default 20, max 100"},
                    "path": {"type": "string", "description": "Only commits touching this path"},
                    "search": {"type": "string", "description": "Regex for git log -G"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_syntax",
            "description": "Parse a .py, .yml/.yaml, .json or .toml file without executing it and report syntax errors.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
]
