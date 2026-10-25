"""Report schema validation and secret redaction."""

import re

SEVERITIES = ["critical", "high", "medium", "low"]
CATEGORIES = [
    "repository_hygiene", "dependency_management", "code_quality", "testing", "ci_cd",
    "security", "configuration", "collaboration", "build_release",
]
FIELDS = ["rule_id", "category", "severity", "file", "line", "description", "evidence", "recommendation"]
RULE_ID = re.compile(r"^[A-Z]{2,5}-\d{3}$")

# Well-known token formats.
_TOKENS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"sk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"), "[REDACTED_API_KEY]"),
    (re.compile(r"(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{10,}"), "[REDACTED_STRIPE_KEY]"),
    (re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}"), "[REDACTED_SLACK_TOKEN]"),
    (re.compile(r"AIza[0-9A-Za-z_-]{35}"), "[REDACTED_GOOGLE_KEY]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "[REDACTED_JWT]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "[REDACTED_PRIVATE_KEY_HEADER]"),
]
# Credentials in URLs: scheme://user:password@host
_URL_CREDS = re.compile(r"(://[^/\s:@]+:)([^@\s/]+)(@)")
_SECRET_NAME = r"[\w.-]*?(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|auth[_-]?key)"
# name = "value"  /  "name": "value"  /  name: 'value'
_QUOTED = re.compile(rf"(?i)\b({_SECRET_NAME})\b([\"']?\s*[:=]\s*)([\"'])([^\"'\n]{{4,}})\3")
# NAME=value / name: value (unquoted, .env and YAML style); needs letters and digits.
_UNQUOTED = re.compile(rf"(?i)\b({_SECRET_NAME})\b(\s*[:=]\s*)([A-Za-z0-9_+/=@!*.-]{{8,}})(?=\s*(?:#|$))", re.M)
_PLACEHOLDER = re.compile(
    r"(?i)your|example|change.?me|placeholder|dummy|sample|fake|replace|redacted|xxx|\*\*\*|<|\$|\{\{|%\(|^(.)\1+$"
)


def _looks_real(value):
    return not _PLACEHOLDER.search(value)


def redact(text):
    """Mask likely secret values while keeping enough context to reason about them."""
    if not text:
        return text
    for pattern, repl in _TOKENS:
        text = pattern.sub(repl, text)
    text = _URL_CREDS.sub(r"\1[REDACTED]\3", text)
    text = _QUOTED.sub(
        lambda m: m.group(0) if not _looks_real(m.group(4))
        else f"{m.group(1)}{m.group(2)}{m.group(3)}[REDACTED_SECRET]{m.group(3)}", text)
    text = _UNQUOTED.sub(
        lambda m: m.group(0) if not (_looks_real(m.group(3)) and re.search(r"\d", m.group(3))
                                     and re.search(r"[A-Za-z]", m.group(3)))
        else f"{m.group(1)}{m.group(2)}[REDACTED_SECRET]", text)
    return text


def normalize_violation(args, repo_files):
    """Validate one violation proposed by the agent.

    Returns (violation, None) on success or (None, error_message) so the agent
    can correct itself.
    """
    errors = []
    v = {k: args.get(k) for k in FIELDS}

    v["rule_id"] = str(v["rule_id"] or "").strip().upper()
    if not RULE_ID.match(v["rule_id"]):
        errors.append("rule_id must look like 'SEC-001'")
    v["category"] = str(v["category"] or "").strip().lower()
    if v["category"] not in CATEGORIES:
        errors.append(f"category must be one of {CATEGORIES}")
    v["severity"] = str(v["severity"] or "").strip().lower()
    if v["severity"] not in SEVERITIES:
        errors.append(f"severity must be one of {SEVERITIES}")
    for key in ("description", "evidence", "recommendation"):
        v[key] = redact(str(v[key] or "").strip())
        if not v[key]:
            errors.append(f"{key} is required")

    file = v["file"]
    if file in ("", "null", "None"):
        file = None
    if file is not None:
        file = str(file).strip().lstrip("/").removeprefix("./")
        if file not in repo_files:
            errors.append(f"file '{file}' is not tracked in the repository; use '' for repository-wide "
                          "or missing-file findings")
    v["file"] = file

    line = v["line"]
    if line in ("", "null", "None"):
        line = None
    if line is not None:
        try:
            line = int(line)
        except (TypeError, ValueError):
            errors.append("line must be an integer or null")
            line = None
        if line is not None and (line < 1 or file is None):
            line = None
    v["line"] = line

    return (None, "; ".join(errors)) if errors else (v, None)


def build_report(repo_url, violations):
    order = {s: i for i, s in enumerate(SEVERITIES)}
    ordered = sorted(violations, key=lambda v: (order[v["severity"]], v["rule_id"], v["file"] or ""))
    return {"repository": repo_url, "violations": [{k: v[k] for k in FIELDS} for v in ordered]}
