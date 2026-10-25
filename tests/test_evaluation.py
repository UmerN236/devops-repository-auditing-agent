import json

import pytest

from evaluate import previous_cases, score
from evaluation.cases import EXPECTED, SYNTHETIC_TOKEN, build_case
from llm import LLMError
from tools import RepoTools


def test_metrics_penalize_duplicates_and_false_positives():
    finding = {'rule_id': 'SEC-001', 'file': 'config.py'}
    metrics = score([finding, finding, {'rule_id': 'HYG-001', 'file': None}],
                    [('SEC-001', 'config.py'), ('TEST-001', None)])
    assert metrics['true_positives'] == 1 and metrics['false_positives'] == 2
    assert metrics['false_negatives'] == 1
    assert metrics['precision'] == pytest.approx(1 / 3) and metrics['recall'] == 0.5


def test_healthy_metrics_do_not_invent_a_recall_score():
    metrics = score([], [])
    assert metrics['precision'] is None and metrics['recall'] is None
    assert metrics['false_positives'] == 0


def test_results_are_not_relabelled_as_a_different_model(tmp_path):
    path = tmp_path / 'results.json'
    cases = {'healthy': {'false_positives': 0}}
    path.write_text(json.dumps({'model': 'old-model', 'cases': cases}))
    assert previous_cases(path, 'old-model') == cases
    assert previous_cases(path, 'new-model') == {}


def test_invalid_existing_results_are_a_useful_error(tmp_path):
    path = tmp_path / 'results.json'
    path.write_text('{broken json')
    with pytest.raises(LLMError, match='Invalid evaluation results file'):
        previous_cases(path, 'model')


@pytest.mark.parametrize('name', EXPECTED)
def test_fixtures_have_real_git_history_and_safe_tool_output(tmp_path, name):
    root = build_case(tmp_path / name, name)
    tools = RepoTools(root)
    files = tools.files()
    assert files and 'app.py' in files
    assert 'syntax OK' in tools.check_syntax('app.py')
    assert SYNTHETIC_TOKEN not in tools.search_code('.*')
    if name == 'subtle':
        assert 'integration.env' not in files
        history = tools.git_log(search='ghp_')
        assert 'API_TOKEN' in history and SYNTHETIC_TOKEN not in history
    if name == 'healthy':
        assert '.github/workflows/ci.yml' in files and 'tests/test_app.py' in files
