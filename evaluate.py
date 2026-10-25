"""Run real-LLM audits on disposable known-defect repositories and measure results."""

import argparse
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from agent import log, write_json
from core import Auditor
from evaluation.cases import EXPECTED, build_case, git
from llm import LLM, LLMError
from tools import RepoTools, ToolError


def score(violations, expected):
    """Match each known rule/path once; unmatched/duplicate findings count as false positives."""
    remaining = list(expected)
    matched, extras = [], []
    for finding in violations:
        key = (finding['rule_id'], finding['file'])
        if key in remaining:
            remaining.remove(key)
            matched.append(key)
        else:
            extras.append(key)
    tp, fp, fn = len(matched), len(extras), len(remaining)
    return {'true_positives': tp, 'false_positives': fp, 'false_negatives': fn,
            'precision': tp / (tp + fp) if tp + fp else None,
            'recall': tp / (tp + fn) if tp + fn else None,
            'matched': matched, 'unmatched_findings': extras, 'missed': remaining}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=['all', *EXPECTED], default='all')
    parser.add_argument('--max-steps', type=int, default=40)
    parser.add_argument('--output', type=Path, default=Path('evaluation_results.json'))
    parser.add_argument('--runs-dir', type=Path, default=Path('evaluation_runs'))
    args = parser.parse_args(argv)
    if args.max_steps < 1:
        parser.error('--max-steps must be positive')
    load_dotenv(Path(__file__).resolve().parent / '.env')
    try:
        llm = LLM()
        result = {'model': llm.model, 'run_at': datetime.now(timezone.utc).isoformat(),
                  'method': 'Rule/path matching on local synthetic fixtures; human review of unmatched findings required.',
                  'cases': {}}
        for name in EXPECTED if args.case == 'all' else [args.case]:
            with tempfile.TemporaryDirectory(prefix='audit-eval-') as temp:
                root = build_case(Path(temp) / name, name)
                auditor = Auditor(llm, RepoTools(root), f'fixture://{name}', max_steps=args.max_steps, log=log)
                report = auditor.run()
                write_json(args.runs_dir / f'{name}.trace.json', {
                    'repository': f'fixture://{name}', 'revision': git(root, 'rev-parse', 'HEAD'),
                    'model': llm.model, 'completed': auditor.done, 'steps': auditor.trace})
                if not auditor.done:
                    raise LLMError(f'{name}: incomplete audit; increase --max-steps')
                write_json(args.runs_dir / f'{name}.json', report)
                metrics = score(report['violations'], EXPECTED[name])
                result['cases'][name] = metrics
                write_json(args.output, result)
                log(f'{name}: TP={metrics["true_positives"]} FP={metrics["false_positives"]} '
                    f'FN={metrics["false_negatives"]}')
        return 0
    except (LLMError, ToolError, OSError) as error:
        log(f'ERROR: {error}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
