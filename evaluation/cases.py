"""Small repositories with known defects, independent of the production prompts."""

import subprocess
from pathlib import Path

HEALTHY_FILES = {
    'README.md': '# Calculator\n\nPython 3.10+. No third-party runtime dependencies.\n'
                 'Install development tools: `pip install -r requirements-dev.txt`.\n'
                 'Run `python app.py`, `pytest -q`, and `ruff check .`.\n'
                 'Configuration uses APP_DATA_DIR and APP_DEBUG environment variables.\n',
    '.gitignore': '.env\n.venv/\n__pycache__/\n*.py[cod]\n.pytest_cache/\n.ruff_cache/\n*.log\n',
    'requirements.txt': '# Standard library only.\n',
    'requirements-dev.txt': 'pytest==9.1.1\nruff==0.17.2\n',
    'pyproject.toml': '[tool.ruff]\nline-length = 130\ntarget-version = "py310"\n'
                      '[tool.pytest.ini_options]\npythonpath = ["."]\n',
    'app.py': 'def add(a, b):\n    return a + b\n\n\nif __name__ == "__main__":\n    print(add(2, 3))\n',
    'config.py': 'import os\n\nDEBUG = os.getenv("APP_DEBUG", "0") == "1"\n'
                 'DATA_DIR = os.getenv("APP_DATA_DIR", "./data")\n',
    'tests/test_app.py': 'from app import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n'
                         '    assert add(-2, 2) == 0\n',
    '.github/workflows/ci.yml': 'name: CI\non:\n  push:\n    branches: [main]\n  pull_request:\n'
                               'permissions:\n  contents: read\njobs:\n  test:\n    runs-on: ubuntu-latest\n'
                               '    steps:\n      - uses: actions/checkout@v4\n'
                               '      - uses: actions/setup-python@v5\n'
                               '        with:\n          python-version: "3.12"\n'
                               '      - run: pip install -r requirements-dev.txt\n'
                               '      - run: ruff check .\n      - run: pytest -q\n',
}

# Not real credentials. These are injected only into disposable evaluation repositories.
SYNTHETIC_TOKEN = 'ghp_' + 'Z7rV2nQ8pL4sT6mW1xC9dF3hJ5kN0bA2vE4u'

EXPECTED = {
    'healthy': [],
    'obvious': [
        ('HYG-001', None), ('HYG-002', None), ('HYG-003', 'debug.log'),
        ('CQ-001', None), ('TEST-001', None), ('SEC-001', 'config.py'), ('CFG-001', 'config.py'),
    ],
    'subtle': [
        ('CI-001', '.github/workflows/ci.yml'), ('CI-002', '.github/workflows/ci.yml'),
        ('SEC-002', '.github/workflows/ci.yml'), ('SEC-003', '.github/workflows/ci.yml'),
        ('CFG-001', 'config.py'), ('SEC-001', None),
    ],
}


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True,
                          text=True, timeout=30).stdout.strip()


def build_case(root, name):
    """Create a disposable Git repository; no target code is run."""
    if name not in EXPECTED:
        raise ValueError(f'Unknown evaluation case: {name}')
    root = Path(root)
    root.mkdir(parents=True)
    files = dict(HEALTHY_FILES)
    if name == 'obvious':
        for path in ['README.md', '.gitignore', 'pyproject.toml', 'requirements-dev.txt',
                     'tests/test_app.py', '.github/workflows/ci.yml']:
            files.pop(path)
        files['debug.log'] = 'Application started\n'
        files['config.py'] = f'API_KEY = "{SYNTHETIC_TOKEN}"\nDATA_DIR = "/Users/developer/project/data"\n'
    elif name == 'subtle':
        files['config.py'] = files['config.py'].replace('"./data"', '"/Users/developer/project/data"')
        workflow = files['.github/workflows/ci.yml']
        workflow = workflow.replace('branches: [main]', 'branches: [release]')
        workflow = workflow.replace('permissions:\n  contents: read', 'permissions: write-all')
        workflow = workflow.replace('      - run: pytest -q', '      - run: pytest -q\n        continue-on-error: true')
        workflow += '      - run: echo "${{ github.event.pull_request.title }}"\n'
        files['.github/workflows/ci.yml'] = workflow
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
    git(root, 'init', '-q', '-b', 'main')
    git(root, 'config', 'user.name', 'Evaluation Fixture')
    git(root, 'config', 'user.email', 'fixture@example.invalid')
    git(root, 'config', 'commit.gpgsign', 'false')
    if name == 'subtle':
        (root / 'integration.env').write_text(f'API_TOKEN="{SYNTHETIC_TOKEN}"\n', encoding='utf-8')
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', 'Initial calculator implementation')
    if name == 'subtle':
        (root / 'integration.env').unlink()
        git(root, 'add', '-A')
        git(root, 'commit', '-qm', 'Remove local integration configuration')
    return root
