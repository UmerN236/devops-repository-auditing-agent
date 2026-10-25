import pytest
from conftest import FAKE_KEY, git

import tools as tool_module
from tools import RepoTools, ToolError


def test_list_files_includes_dotfiles_and_ignored_tracked(repo):
    out = RepoTools(repo).list_files()
    assert ".github/workflows/ci.yml" in out
    assert ".gitignore" in out
    assert "Tracked files that match .gitignore rules" in out and "debug.log" in out
    assert ".git/" not in out


def test_list_files_subdirectory(repo):
    out = RepoTools(repo).list_files("app")
    assert "app/config.py" in out and "README.md" not in out


def test_read_file_numbers_lines_and_redacts(repo):
    tools = RepoTools(repo)
    out = tools.read_file("app/config.py")
    assert "1: DEBUG = True" in out
    assert "3: DATA = \"/Users/alice/data\"" in out
    assert FAKE_KEY not in out and "REDACTED" in out
    assert "app/config.py" in tools.inspected


def test_read_file_range(repo):
    out = RepoTools(repo).read_file("app/config.py", start_line=2, end_line=2)
    assert "2: AWS_KEY" in out and "1: DEBUG" not in out


def test_redaction_precedes_truncation_and_pagination(repo):
    secret = 'VioletCedar3421River'
    (repo / 'long.py').write_text('#' * 278 + ' password = "' + secret + '"\n')
    (repo / 'key.pem').write_text('-----BEGIN PRIVATE KEY-----\nQm9keVNlY3JldA==\n-----END PRIVATE KEY-----\n')
    git(repo, 'add', 'long.py', 'key.pem')
    tools = RepoTools(repo)
    assert secret[:7] not in tools.read_file('long.py')
    assert secret[:7] not in tools.search_code('password')
    middle = tools.read_file('key.pem', start_line=2, end_line=2)
    assert 'Qm9keVNlY3JldA==' not in middle and 'REDACTED_PRIVATE_KEY' in middle


def test_read_file_errors(repo):
    tools = RepoTools(repo)
    with pytest.raises(ToolError, match="not found"):
        tools.read_file("missing.py")
    with pytest.raises(ToolError, match="escapes"):
        tools.read_file("../../etc/passwd")


def test_search_code(repo):
    tools = RepoTools(repo)
    out = tools.search_code(r"/Users/\w+")
    assert "app/config.py:3:" in out
    assert "app/config.py" in tools.inspected
    assert "No matches" in tools.search_code("continue-on-error")
    assert "ci.yml:6:" in tools.search_code(r"\|\| true", file_glob=".github/workflows/*")
    with pytest.raises(ToolError, match="invalid regex"):
        tools.search_code("(unclosed")


def test_git_log_and_history_search(repo):
    tools = RepoTools(repo)
    out = tools.git_log()
    assert "Default branch: main" in out and "initial project" in out and "Tester" in out
    hist = tools.git_log(search="ghp_")
    assert "add secret" in hist
    assert 'token =' in hist and 'REDACTED_GITHUB_TOKEN' in hist
    assert 'old_secret.txt' in hist and 'Shallow history: false' in hist
    assert "ghp_" + "a" * 36 not in hist


def test_check_syntax(repo):
    tools = RepoTools(repo)
    assert "SYNTAX ERROR" in tools.check_syntax("broken.yml")
    assert "syntax OK" in tools.check_syntax("app/main.py")
    assert "syntax OK" in tools.check_syntax(".github/workflows/ci.yml")


def test_refuses_untracked_metadata_and_symlink_escape(repo, tmp_path):
    tools = RepoTools(repo)
    with pytest.raises(ToolError, match='tracked file not found'):
        tools.read_file('.git/config')
    outside = tmp_path / 'outside.txt'
    outside.write_text('outside content')
    (repo / 'escape.txt').symlink_to(outside)
    git(repo, 'add', 'escape.txt')
    tools = RepoTools(repo)
    with pytest.raises(ToolError, match='escapes'):
        tools.search_code('outside')


def test_pathological_regex_is_terminated(repo, monkeypatch):
    (repo / 'long.txt').write_text('a' * 30000 + '!')
    git(repo, 'add', 'long.txt')
    monkeypatch.setattr(tool_module, 'SEARCH_TIMEOUT', 0.3)
    with pytest.raises(ToolError, match='search timed out'):
        RepoTools(repo).search_code('(a+)+$', file_glob='long.txt')


def test_output_limit_does_not_mark_unshown_lines_as_observed(repo):
    (repo / 'large.py').write_text(('x = ' + '1' * 290 + '\n') * 300)
    git(repo, 'add', 'large.py')
    tools = RepoTools(repo)
    out = tools.read_file('large.py')
    assert 'file continues' in out
    assert 300 not in tools.observed_lines['large.py']
