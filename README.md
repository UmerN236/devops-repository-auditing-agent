# DevOps Repository Auditing Agent

[![CI](https://github.com/UmerN236/devops-repository-auditing-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/UmerN236/devops-repository-auditing-agent/actions/workflows/ci.yml)

A Python agent that inspects a public GitHub repository and reports evidence-backed DevOps
violations. An LLM chooses inspection tools, interprets their results, follows up on suspicious
findings, and reviews its report. The agent loop and dispatch are implemented directly in Python;
no agent or orchestration framework is used.

## Install

Requires **Python 3.10+**, Git on `PATH`, and an API key for a model that supports function calling.

```bash
git clone https://github.com/UmerN236/devops-repository-auditing-agent.git
cd devops-repository-auditing-agent
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

On Windows, activate with `.venv\Scripts\activate` and copy the example with `copy .env.example .env`.

## Configure a provider

Edit `.env` in the project directory. It is ignored by Git. Environment variables already set in
your shell take precedence over this file. Keep API keys out of reports, commits, and screenshots.

The default `.env.example` uses **OpenRouter free models**. Create a key at
[openrouter.ai/keys](https://openrouter.ai/keys) and set it as `LLM_API_KEY`:

```dotenv
LLM_API_KEY=your-OpenRouter-key
LLM_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free
LLM_FALLBACK_MODELS=google/gemma-4-31b-it:free,nvidia/nemotron-3-super-120b-a12b:free
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_TEMPERATURE=
LLM_REASONING_EFFORT=
LLM_TIMEOUT=90
```

`LLM_FALLBACK_MODELS` is OpenRouter-specific: if the primary model is rate-limited or unavailable,
OpenRouter routes the request to the next model in the list. Free models (ids ending in `:free`)
cost nothing but have per-minute and daily request limits; one audit uses roughly 15-40 requests.
Any OpenRouter model that supports tool calling works.
Fallback settings are ignored for other provider endpoints. Model fallback does not increase an
account's daily free-request allowance; check [OpenRouter's limits](https://openrouter.ai/docs/api/reference/limits)
when a run reports exhausted quota.

For **Gemini**, create a key in [Google AI Studio](https://aistudio.google.com/apikey):

```dotenv
LLM_API_KEY=your-Gemini-key
LLM_MODEL=gemini-3.8-flash
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
LLM_TEMPERATURE=
LLM_REASONING_EFFORT=
LLM_TIMEOUT=90
```

Google offers a free tier for eligible models/accounts, subject to request and token quotas.
Check your account's available models and limits. See Google's
[compatibility documentation](https://ai.google.dev/gemini-api/docs/openai) and
[pricing](https://ai.google.dev/gemini-api/docs/pricing).

For **OpenAI**, create a key in the [API dashboard](https://platform.openai.com/api-keys):

```dotenv
LLM_API_KEY=your-OpenAI-key
LLM_MODEL=gpt-5.4-mini
LLM_BASE_URL=
LLM_TEMPERATURE=
LLM_REASONING_EFFORT=medium
LLM_TIMEOUT=90
```

`OPENAI_API_KEY` is also accepted. If no model is configured, the default is `gpt-5.4-mini`.
Other OpenAI-compatible endpoints, including Groq, OpenRouter, and local Ollama servers, can be
selected with `LLM_BASE_URL` and an appropriate `LLM_MODEL`. Only set `LLM_REASONING_EFFORT` or
`LLM_TEMPERATURE` when the chosen model supports them. Blank values omit those parameters.
`LLM_TIMEOUT` is a positive per-request timeout in seconds; transient API failures are retried twice.

## Run

```bash
python agent.py --repo https://github.com/kennethreitz/samplemod --output report.json
```

The required interface is:

```bash
python agent.py --repo <github_repository_url> --output report.json
```

The process runs unattended. It clones the default branch into a temporary directory, inspects it,
writes the report, and removes the clone. It does not install dependencies or run code from the target.
Progress goes to stderr.

Optional arguments:

| Argument | Meaning |
|---|---|
| `--max-steps N` | Positive maximum number of LLM turns; default 40 |
| `--trace PATH` | Trace destination; default is the output path with suffix `.trace.json` |
| `--quiet` | Suppress per-step progress messages |

Exit code `0` means the inspection completed and the report was written. Fatal errors return `1`
with a useful message; invalid CLI arguments return `2`, and interruption returns `130`.
If the turn budget expires, the run returns `1`, saves an incomplete trace, and does not write a new
report. Increase `--max-steps` and rerun. Output and trace destinations must differ. Output directories
are created automatically, and each JSON file is replaced atomically.
If the provider fails after inspection begins, an incomplete trace is preserved for diagnosis.

## Report contract

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

Every finding contains all eight fields. Severity is `critical`, `high`, `medium`, or `low`.
Inapplicable file and line values are JSON `null`. A completed inspection can have an empty
`violations` array. Findings are sorted by severity and rule ID.

Inspection covers repository hygiene, dependency management, code quality, testing, CI/CD,
security, configuration, collaboration, and build/release practices. ML training, datasets,
experiment tracking, and model-validation practices are outside the audit's scope.

Generate the submission example and its trace with:

```bash
python agent.py --repo https://github.com/kennethreitz/samplemod --output sample_report.json --max-steps 15
```

The accompanying trace identifies the model, inspected commit, completion status, tool calls,
redacted arguments, and result previews. The sample must come from this real command, rather than
from a scripted test model or manually entered findings. The committed `sample_report.json` was
produced this way with `nvidia/nemotron-3-ultra-550b-a55b:free` through OpenRouter (15 LLM turns,
2 findings). Its self-review withdrew one draft finding. The recorded findings and withdrawals
reconstruct the committed report exactly.
New traces distinguish the configured `model` from `models_used` and record the responding model on
each turn, including fallback models selected by a gateway.

## Architecture

| Module | Responsibility |
|---|---|
| `agent.py` | CLI, URL validation, temporary clone, output and exit handling |
| `core.py` | Explicit LLM/tool loop, dispatch, evidence validation, duplicate rejection, self-review |
| `llm.py` | OpenAI-compatible SDK client, configuration checks, retries, provider tool metadata |
| `prompts.py` | General DevOps rules, investigation guidance, severity and precision rules |
| `tools.py` | Read-only repository tools and their function schemas |
| `search_worker.py` | Trusted subprocess that bounds regex search execution time |
| `report.py` | Finding schema validation, normalization, redaction and report assembly |
| `evaluate.py` | Real-model evaluation on disposable repositories with known defects |

The agent cycle is **Observe → Reason → Select Tool → Execute Tool → Interpret Results → Report**.

1. The LLM receives the repository URL and tracked-file count, then chooses what to inspect.
2. Every tool result is appended to the conversation. The next tool call depends on these results.
3. `record_violation` validates each finding. A cited file must be tracked and inspected; a cited
   line must have actually appeared in a tool result. Validation errors are returned to the LLM
   so it can investigate or correct its arguments. Exact rule/file/line duplicates are rejected.
4. The first `finish` request returns the recorded findings and a coverage/precision checklist.
   The agent can investigate further or withdraw findings. A subsequent `finish` completes the run.
5. Tool failures become observations rather than fabricated findings. Tool outputs are bounded,
   and older results are elided when conversation history grows large.

## Inspection tools

| Tool | Purpose |
|---|---|
| `list_files(directory)` | Discover tracked files, including dotfiles, large files and tracked ignored artifacts |
| `read_file(path, start_line, end_line)` | Read numbered lines from a tracked text file |
| `search_code(pattern, file_glob, ignore_case)` | Regex search with file/line evidence and a 10-second worker timeout |
| `git_log(max_count, path, search)` | Commit metadata; path/history searches include redacted patches, including deleted files |
| `check_syntax(path)` | Parse Python, YAML, JSON or TOML without executing target code |
| `record_violation(...)` | Validate and store a finding |
| `finish(withdraw)` | Review findings, optionally withdraw unsupported ones, then finish |

Missing optional files are not automatically violations. The prompt recognizes alternatives such
as `pyproject.toml`, tox, GitLab CI, or other equivalent tools. It excludes guesses about remote
branch protection and treats placeholders and environment-variable references as acceptable.
Repository content is treated as untrusted evidence, including instructions embedded in files.
Development/documentation dependencies need not appear in runtime package metadata, and an assertion
of a concrete return value is not treated as meaningless merely for lacking other assertions.

## Safety and limitations

- Targets are cloned into disposable directories and are never edited by inspection tools.
- No target tests, builds, scripts, package installers, or Python imports are executed.
  The regex worker runs only this project's trusted inspection code.
- Clone hooks and symlinks are disabled. System/global Git configuration is excluded during cloning,
  LFS smudging is disabled, and file-protocol submodules are blocked. Submodules are not initialized.
- Tool paths must stay within the clone. File reads are limited to tracked files; `.git` metadata
  cannot be read through `read_file`.
- Credential patterns, assignment values, and private-key blocks are masked before pagination,
  truncation, logging or trace serialization. Redaction is heuristic, so supported formats are
  regression-tested; this is not a comprehensive secret-scanning product.
- Binary files and files over 1 MB are not read/searched. Reads return at most 300 lines and 20,000
  characters; the agent can request additional ranges. Searches report omitted matches/skipped files.
- The clone contains up to 200 commits from the default branch. History output states whether it
  is shallow; absence of a match does not establish that the entire remote history is clean.
- Remote settings such as branch protection are unavailable. Runtime test failures are not claimed
  when only static evidence is available.
- Detection quality depends on the selected model and available API quota. Review the evidence in
  findings; benchmark results on small fixtures do not predict hidden-repository grades.

## Automated checks

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
ruff check .
```

Tests are offline and require no API key. They use temporary Git repositories, a scripted model for
control-flow tests, and an SDK mock transport for protocol tests. They cover dispatch and follow-up
observations, invalid arguments, evidence grounding, withdrawal and duplicates, empty reports,
error exits, incomplete audits, atomic output, historical patches, credential masking, path escapes,
regex timeouts, and provider tool metadata. GitHub Actions runs lint and tests on Python 3.10 and 3.12.

## Real-model evaluation

After configuring `.env`, run:

```bash
python evaluate.py --output evaluation_results.json
```

This invokes the real LLM on three disposable local Git repositories:

- `obvious`: missing documentation/ignore/lint/test coverage, tracked logs, a synthetic credential,
  and a machine-specific path.
- `subtle`: incorrect branch triggers, ignored test failures, broad permissions, shell interpolation
  of PR input, a machine-specific default path, and a synthetic credential removed from the latest tree.
- `healthy`: a small application with tests, bounded development dependencies, linting, working CI,
  environment-based configuration, and no injected defects.

These are explicitly local fixtures, not hidden instructor repositories. No fixture code is executed.
The expected defects are kept outside the agent modules and prompts. Each known rule/path can match
only once; extra or duplicate findings count as false positives. The results include precision,
recall, missed defects and unmatched findings, which should be reviewed for alternate valid rule
assignments. Undefined precision/recall denominators are reported as `null`.

Measured with `nvidia/nemotron-3-ultra-550b-a55b:free` (OpenRouter, 2026-10-25):

| Case | True positives | False positives | Missed | Precision | Recall |
|---|---|---|---|---|---|
| healthy | 0 | 0 | 0 | n/a | n/a |
| obvious | 7 | 0 | 0 | 1.00 | 1.00 |
| subtle | 5 | 1 | 1 | 0.83 | 0.83 |

These are strict rule/path scores. In the subtle case, the unmatched `SEC-004` finding and the
missed `SEC-001` expectation refer to the same historical secret-bearing file. Source review confirms
that all six injected defects were identified; the scoring mismatch is a valid alternate rule ID.
The raw scores remain unchanged. See [review notes](evaluation/review_notes.md),
[machine-readable results](evaluation/results.json), and [reports/traces](evaluation/artifacts/).
The healthy and obvious rows retain their earlier completed runs. The subtle row comes from the
updated history tool and an explicitly recorded final-review continuation.

Use `--case obvious`, `--case subtle`, or `--case healthy` for an individual case; a single-case
run updates that case's entry in the existing results file when the configured model matches.
Changing models starts a new set of results, so old scores are not attributed to a new model.
New case results include their measurement time and responding models. Reports/traces go
to `evaluation_runs/`, which is ignored by Git. For a public-repository check, use the normal CLI and
compare its report against the inspected revision recorded in its trace.

If a case runs out of turns immediately after the first `finish` request, its final self-review can
be completed from the saved trace without repeating the inspection:

```bash
python evaluate.py --case subtle --complete-review --output evaluation_results.json
```

This mode only retains or withdraws findings already recorded in the original run. It cannot add
findings or perform a new inspection. Saved observation previews may be truncated; the reviewer
must decline completion if further inspection is needed. Continuation is explicitly labeled in the
trace. If the inspection has not reached self-review, rerun it with a larger turn budget instead.
