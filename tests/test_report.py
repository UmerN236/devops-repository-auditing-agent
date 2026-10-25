from report import FIELDS, build_report, normalize_violation, redact

GOOD = {
    "rule_id": "sec-001", "category": "Security", "severity": "CRITICAL", "file": "app/config.py",
    "line": 2, "description": "Hardcoded AWS key", "evidence": "AWS key assigned to AWS_KEY",
    "recommendation": "Use environment variables",
}


def test_redact_masks_real_secrets():
    assert "REDACTED" in redact('password = "s3cr3t-Value"')
    assert "REDACTED" in redact("DB_PASSWORD=hunter22x")
    assert "REDACTED" in redact("postgres://user:pa55word@db:5432/app")
    assert "REDACTED" in redact("key: " + "AKIA" + "ABCDEFGHIJKLMNOP")
    assert "s3cr3t" not in redact('"api_key": "s3cr3t-Value"')


def test_redact_keeps_placeholders_and_references():
    for text in ['password = "changeme"', 'API_KEY="your-api-key-here"', "token: ${{ secrets.TOKEN }}",
                 'api_key = os.environ["API_KEY"]', "password=password", "max_tokens = 1000"]:
        assert redact(text) == text


def test_normalize_valid_violation():
    v, err = normalize_violation(GOOD, {"app/config.py"})
    assert err is None
    assert (v["rule_id"], v["category"], v["severity"]) == ("SEC-001", "security", "critical")


def test_normalize_rejects_bad_fields():
    v, err = normalize_violation({**GOOD, "severity": "urgent", "category": "misc"}, {"app/config.py"})
    assert v is None and "severity" in err and "category" in err
    v, err = normalize_violation({**GOOD, "file": "nope.py"}, {"app/config.py"})
    assert v is None and "not tracked" in err


def test_normalize_null_file_and_line():
    v, err = normalize_violation({**GOOD, "file": "", "line": 0}, set())
    assert err is None and v["file"] is None and v["line"] is None


def test_normalize_redacts_text_fields():
    v, _ = normalize_violation({**GOOD, "evidence": 'secret = "abcd1234efgh"'}, {"app/config.py"})
    assert "abcd1234efgh" not in v["evidence"]


def test_build_report_orders_by_severity():
    low, _ = normalize_violation({**GOOD, "severity": "low", "rule_id": "CQ-001", "category": "code_quality"},
                                 {"app/config.py"})
    crit, _ = normalize_violation(GOOD, {"app/config.py"})
    report = build_report("https://github.com/a/b", [low, crit])
    assert report["repository"] == "https://github.com/a/b"
    assert [v["severity"] for v in report["violations"]] == ["critical", "low"]
    assert all(list(v) == FIELDS for v in report["violations"])
