import json

import pytest
from conftest import FAKE_KEY, FakeLLM, call

import agent
from core import Auditor
from tools import RepoTools

SECRET = dict(rule_id="SEC-001", category="security", severity="critical", file="app/config.py", line=2,
              description="Hardcoded AWS key", evidence="AWS access key assigned to AWS_KEY",
              recommendation="Load it from the environment or a secrets manager")
GATE = dict(rule_id="CI-002", category="ci_cd", severity="high", file=".github/workflows/ci.yml", line=6,
            description="Test failures ignored", evidence="`pytest || true` always succeeds",
            recommendation="Remove `|| true` so failing tests fail the job")


def run(repo, turns, **kw):
    llm = FakeLLM(turns)
    auditor = Auditor(llm, RepoTools(repo), "https://github.com/x/y", **kw)
    return auditor.run(), auditor, llm


def tool_results(llm):
    return [m["content"] for m in llm.requests[-1] if m["role"] == "tool"]


def test_full_loop_records_and_reports(repo):
    report, auditor, llm = run(repo, [
        [call("list_files")],
        [call("read_file", path="app/config.py"), call("search_code", pattern=r"\|\| true")],
        [call("record_violation", **SECRET), call("record_violation", **GATE)],
        [call("finish")],            # first finish triggers the self-review
        [call("finish")],
    ])
    assert [v["rule_id"] for v in report["violations"]] == ["SEC-001", "CI-002"]
    assert "Before finishing, review your work" in tool_results(llm)[-1]
    assert auditor.done
    assert [c["tool"] for s in auditor.trace for c in s["tool_calls"]][:3] == ["list_files", "read_file", "search_code"]
    assert FAKE_KEY not in json.dumps(auditor.trace)
    json.dumps(report)


def test_tool_results_reach_the_llm(repo):
    _, _, llm = run(repo, [[call("read_file", path="README.md")], [call("finish")], [call("finish")]])
    assert any("# Demo" in m["content"] for m in llm.requests[1] if m["role"] == "tool")


def test_rejects_line_finding_in_uninspected_file(repo):
    report, _, llm = run(repo, [[call("record_violation", **SECRET)], [call("finish")], [call("finish")]])
    assert report["violations"] == []
    assert "have not inspected" in llm.requests[1][-1]["content"]


@pytest.mark.parametrize('line', [2, 99999])
def test_rejects_lines_outside_observed_range(repo, line):
    report, _, llm = run(repo, [
        [call('read_file', path='app/config.py', start_line=1, end_line=1)],
        [call('record_violation', **{**SECRET, 'line': line})],
        [call('finish')], [call('finish')],
    ])
    assert report['violations'] == []
    assert 'has not been shown' in llm.requests[2][-1]['content']


def test_empty_findings_report_is_valid(repo):
    report, auditor, _ = run(repo, [[call('list_files')], [call('finish')], [call('finish')]])
    assert report == {'repository': 'https://github.com/x/y', 'violations': []}
    assert auditor.done


def test_rejects_duplicates_and_supports_withdraw(repo):
    report, _, llm = run(repo, [
        [call("read_file", path="app/config.py")],
        [call("record_violation", **SECRET), call("record_violation", **SECRET)],
        [call("finish", withdraw=["V1"])],
        [call("finish")],
    ])
    assert "duplicate of V1" in llm.requests[2][-1]["content"]
    assert report["violations"] == []


def test_invalid_input_is_reported_back_not_fatal(repo):
    report, _, llm = run(repo, [
        [{"name": "read_file", "args": "{not json"}],
        [call("read_file", path="nope.txt"), call("no_such_tool"), call("read_file", bogus=1)],
        [call("record_violation", **{**SECRET, "severity": "urgent"})],
        [call("finish")], [call("finish")],
    ])
    assert "invalid JSON" in llm.requests[1][-1]["content"]
    errors = [m["content"] for m in llm.requests[2][-3:]]
    assert "not found" in errors[0] and "unknown tool" in errors[1] and "bad arguments" in errors[2]
    assert "severity" in llm.requests[3][-1]["content"]
    assert report["violations"] == []


def test_text_reply_gets_nudged(repo):
    _, _, llm = run(repo, ["I think the repo is fine.", [call("finish")], [call("finish")]])
    assert "Continue the audit by calling a tool" in llm.requests[1][-1]["content"]


def test_step_budget_returns_partial_report(repo):
    turns = [[call("read_file", path="app/config.py")], [call("record_violation", **SECRET)]]
    turns += [[call("list_files")]] * 10
    report, auditor, llm = run(repo, turns, max_steps=5)
    assert len(llm.requests) == 5 and not auditor.done
    assert len(report["violations"]) == 1
    assert any("budget almost exhausted" in (m.get("content") or "") for m in llm.requests[-1])


# ------------------------------------------------------------------- CLI tests
@pytest.fixture
def cli(monkeypatch, repo):
    monkeypatch.setattr(agent, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(agent, "clone", lambda url, dest: __import__("shutil").copytree(repo, dest))


def test_cli_end_to_end(cli, monkeypatch, tmp_path):
    turns = [[call("read_file", path="app/config.py")], [call("record_violation", **SECRET)],
             [call("finish")], [call("finish")]]
    monkeypatch.setattr(agent, "LLM", lambda: FakeLLM(turns))
    out = tmp_path / "out" / "report.json"
    assert agent.main(["--repo", "https://github.com/x/y.git", "--output", str(out), "--quiet"]) == 0
    report = json.loads(out.read_text())
    assert report["repository"] == "https://github.com/x/y"
    assert report["violations"][0]["rule_id"] == "SEC-001"
    assert json.loads(out.with_suffix(".trace.json").read_text())["steps"]


def test_cli_rejects_invalid_url(cli, tmp_path, capsys):
    assert agent.main(["--repo", "not-a-url", "--output", str(tmp_path / "r.json")]) == 1
    assert "Invalid repository URL" in capsys.readouterr().err
    assert not (tmp_path / "r.json").exists()


def test_cli_missing_api_key(cli, monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert agent.main(["--repo", "https://github.com/x/y", "--output", str(tmp_path / "r.json")]) == 1
    assert "API key" in capsys.readouterr().err


def test_cli_clone_failure(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(agent, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(agent, "LLM", lambda: FakeLLM([]))
    monkeypatch.setattr(agent.subprocess, "run", lambda *a, **k: type("P", (), {"returncode": 128, "stderr": "not found"})())
    assert agent.main(["--repo", "https://github.com/x/missing", "--output", str(tmp_path / "r.json")]) == 1
    assert "Could not clone" in capsys.readouterr().err


def test_cli_exhaustion_is_not_reported_as_success(cli, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(agent, 'LLM', lambda: FakeLLM([[call('list_files')]]))
    out = tmp_path / 'report.json'
    assert agent.main(['--repo', 'https://github.com/x/y', '--output', str(out), '--max-steps', '1']) == 1
    assert not out.exists()
    assert json.loads(out.with_suffix('.trace.json').read_text())['completed'] is False
    assert 'Audit incomplete' in capsys.readouterr().err


def test_cli_rejects_colliding_paths_and_invalid_budget(tmp_path, capsys):
    out = str(tmp_path / 'report.json')
    common = ['--repo', 'https://github.com/x/y', '--output', out]
    assert agent.main(common + ['--trace', out]) == 1
    assert 'different paths' in capsys.readouterr().err
    assert agent.main(common + ['--max-steps', '0']) == 1
    assert 'at least 1' in capsys.readouterr().err


def test_cli_creates_trace_parent(cli, monkeypatch, tmp_path):
    monkeypatch.setattr(agent, 'LLM', lambda: FakeLLM([[call('finish')], [call('finish')]]))
    out, trace = tmp_path / 'out.json', tmp_path / 'nested' / 'trace.json'
    assert agent.main(['--repo', 'https://github.com/x/y', '--output', str(out), '--trace', str(trace)]) == 0
    assert json.loads(out.read_text())['violations'] == []
    assert json.loads(trace.read_text())['completed'] is True
