import json
import subprocess

import pytest

FAKE_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"  # split so this file is not itself flagged

FILES = {
    "README.md": "# Demo\n",
    ".gitignore": "*.log\n",
    "app/config.py": f'DEBUG = True\nAWS_KEY = "{FAKE_KEY}"\nDATA = "/Users/alice/data"\n',
    "app/main.py": "import os\n\nprint(os.getenv('HOME'))\n",
    ".github/workflows/ci.yml": "on: [push]\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n"
                                "      - run: pytest || true\n",
    "broken.yml": "a: [1, 2\n",
    "debug.log": "tracked log\n",
}


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "Tester")
    (root / "old_secret.txt").write_text('token = "ghp_' + "a" * 36 + '"\n')
    git(root, "add", "-A")
    git(root, "commit", "-qm", "add secret")
    (root / "old_secret.txt").unlink()
    for path, content in FILES.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(content)
    git(root, "add", "-A", "-f")
    git(root, "commit", "-qm", "initial project")
    return root


def call(name, **args):
    return {"name": name, "args": args}


class FakeLLM:
    """Replays a scripted list of turns. Each turn is a list of tool calls (or a plain string)."""

    model = "fake-model"

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    def chat(self, messages, tools):
        self.requests.append([dict(m) for m in messages])
        turn = self.turns.pop(0) if self.turns else [call("finish")]
        if isinstance(turn, str):
            return {"role": "assistant", "content": turn}
        return {
            "role": "assistant",
            "content": "Reasoning about next step.",
            "tool_calls": [
                {"id": f"c{len(self.requests)}_{i}", "type": "function",
                 "function": {"name": c["name"],
                              "arguments": c["args"] if isinstance(c["args"], str) else json.dumps(c["args"])}}
                for i, c in enumerate(turn)
            ],
        }
