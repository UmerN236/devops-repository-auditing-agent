# DevOps Repository Auditing Agent

An LLM-driven agent that clones a public GitHub repository, investigates it with read-only
inspection tools, and writes a JSON report of DevOps best-practice violations with evidence and
recommendations.

The agent loop, tool dispatch and control flow are written in plain Python. The only LLM
dependency is the `openai` SDK, used as a client for any OpenAI-compatible endpoint (OpenAI,
Gemini, Groq, OpenRouter, Ollama, ...). No agent frameworks are used.

## Installation

Requires Python 3.10+ and `git` on `PATH`.

```bash
git clone <this-repository-url>
cd <this-repository>
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Setup

```bash
cp .env.example .env
```

Edit `.env`:

| Variable | Required | Meaning |
|---|---|---|
| `LLM_API_KEY` | yes | API key for the provider (`OPENAI_API_KEY` is also accepted) |
| `LLM_MODEL` | no | Model name, default `gpt-4.1-mini`. Must support tool/function calling |
| `LLM_BASE_URL` | no | OpenAI-compatible endpoint; empty means OpenAI. Examples are in `.env.example` |
| `LLM_TEMPERATURE` | no | Default `0`; leave empty for models that reject `temperature` |

Variables already set in the environment take precedence over `.env`.

## Running

```bash
python agent.py --repo https://github.com/<owner>/<repo> --output report.json
```

Optional flags: `--max-steps N` (LLM turns, default 40), `--trace PATH` (tool-call trace, default
`<output>.trace.json`), `--quiet` (no step log on stderr).

The run is unattended. Progress (the agent's reasoning and each tool call) is logged to stderr.
On success the report is written and the exit code is `0`. On a fatal error (invalid URL, clone
failure, missing API key, LLM API failure after retries) the agent prints `ERROR: ...` and exits
with `1` without writing a report.

### Output

```json
{
  "repository": "https://github.com/example/demo-project",
  "violations": [
    {
      "rule_id": "SEC-001",
      "category": "security",
      "severity": "critical",
      "file": "config.py",
      "line": 17,
      "description": "Hardcoded API credential",
      "evidence": "Credential assigned directly in source code",
      "recommendation": "Use a secrets manager or environment variable"
    }
  ]
}
```

- `severity` is one of `critical`, `high`, `medium`, `low`.
- `category` is one of `repository_hygiene`, `dependency_management`, `code_quality`, `testing`,
  `ci_cd`, `security`, `configuration`, `collaboration`, `build_release`.
- `file` and `line` are `null` when they don't apply. An empty `violations` array is a valid result.
- Secret values are never written; they are masked as `[REDACTED_...]`.

`sample_report.json` is a real output of the agent and `sample_report.trace.json` is its tool-call trace.

## Architecture

```
agent.py   CLI: validate URL -> shallow clone into a temp dir -> run Auditor -> write report + trace
core.py    Auditor: the agent loop, tool dispatch, finding validation, self-review
llm.py     OpenAI-compatible chat-completions client (tool calling, retries, timeouts)
prompts.py System prompt: workflow, DevOps rule catalog, precision rules, severity guide
tools.py   Read-only inspection tools + their JSON schemas
report.py  Report schema validation, normalization and secret redaction
```

### Agent loop (Observe -> Reason -> Select tool -> Execute -> Interpret -> Report)

```
            +---------------------------------------------------------+
            |                                                         v
 system prompt + task --> LLM: short reasoning + tool call(s) --> dispatch tool
            ^                                                         |
            +------------- tool result appended to history <----------+
```

1. The LLM gets only the repository URL and the number of tracked files. It has to explore with
   tools; nothing is pre-scanned for it.
2. Each turn it writes a short observation and plan, then calls one or more tools. The result of
   every call goes back into the conversation, and the LLM chooses the next call from it, for
   example reading a workflow it saw in `list_files` or searching history for a secret it found.
3. Confirmed problems are recorded one at a time with `record_violation`. The agent validates
   each one and returns errors so the LLM can correct itself:
   - schema checks (rule id format, category, severity, required text fields);
   - the cited file must be tracked in the repository;
   - a finding with a line number must be in a file the agent actually read or saw in search
     results, so evidence is grounded in tool output;
   - duplicates (same rule, file and line) are rejected.
4. The first `finish` call triggers a self-review: the agent shows the LLM every recorded finding
   and a coverage checklist, and the LLM can withdraw unsupported findings or investigate gaps.
   The second `finish` ends the run.
5. Bad tool arguments, unknown tools, missing files and invalid regexes are returned to the LLM as
   `ERROR: ...` messages, not raised. Large tool outputs are truncated and old outputs are elided
   when the conversation gets long. If the step budget runs out, the findings recorded so far are
   reported.

### Tools

| Tool | Purpose |
|---|---|
| `list_files(directory)` | Tracked files (including dotfiles), sizes of large files, and tracked files that match `.gitignore` |
| `read_file(path, start_line, end_line)` | Line-numbered file contents, up to 300 lines per call |
| `search_code(pattern, file_glob, ignore_case)` | Regex search over all tracked text files, `path:line: text` |
| `git_log(max_count, path, search)` | Commit history with file stats, default/remote branches, authors; `search` runs `git log -G` to find secrets that were removed but remain in history |
| `check_syntax(path)` | Parses `.py` (via `ast`), YAML, JSON or TOML without executing anything |
| `record_violation(...)` | Records one finding (validated) |
| `finish(withdraw)` | Self-review, then end the audit |

### Reducing false positives

The system prompt (`prompts.py`) holds general DevOps knowledge, not answers for particular
repositories: a rule catalog for the nine inspection areas, a severity guide, and explicit
precision rules. Among them: equivalent alternatives are not defects (for example `pyproject.toml`
instead of `requirements.txt`, or GitLab CI instead of GitHub Actions), missing optional files
such as LICENSE or CONTRIBUTING are not reported, remote settings like branch protection are never
inferred, placeholders and `${{ secrets.X }}` are not leaks, there is one finding per root cause,
and a healthy repository may have no findings.

## Safety

- The target is shallow-cloned into a temporary directory that is deleted after the run, so the
  original repository is never modified.
- Repository code is never executed. There is no `pip install`, no test run and no build;
  `check_syntax` only parses files.
- The clone disables symlinks, hooks, LFS smudge filters and file-protocol submodules. Tool paths
  are resolved and rejected if they escape the clone.
- Secret-like values are masked in tool outputs before they reach the LLM, and again in the
  report, the trace and the logs.
- `git` and LLM calls have timeouts. The OpenAI SDK retries rate limits and server errors.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
ruff check .
```

The tests run offline. Tool tests use a temporary git repository with known defects. Agent tests
use a scripted fake LLM to check tool dispatch, finding validation and grounding, duplicate
rejection, the self-review, error recovery, the step budget, redaction and the CLI exit codes.
The same checks run in GitHub Actions (`.github/workflows/ci.yml`).

## Limitations

- Tests of the target repository are not executed (for safety), so failing tests are only reported
  when they can be shown statically.
- Remote GitHub settings (branch protection, secrets, environments) are out of reach by design.
- Results depend on the model. Stronger tool-calling models investigate more thoroughly.
