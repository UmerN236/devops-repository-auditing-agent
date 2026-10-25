import pytest
from conftest import FAKE_KEY

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
    assert "ghp_" + "a" * 36 not in hist


def test_check_syntax(repo):
    tools = RepoTools(repo)
    assert "SYNTAX ERROR" in tools.check_syntax("broken.yml")
    assert "syntax OK" in tools.check_syntax("app/main.py")
    assert "syntax OK" in tools.check_syntax(".github/workflows/ci.yml")
