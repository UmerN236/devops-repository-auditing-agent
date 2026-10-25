# Evaluation evidence review

The reports, traces and raw results in this directory are outputs of actual LLM runs on disposable
local Git repositories. They are not instructor repositories or hidden answer keys. The auditing
agent does not import the fixture definitions or expected results.

## Measured runs

- The healthy case completed with no findings. Its earlier completed report and trace are retained.
- The obvious case completed with all seven expected rule/path matches and no unmatched findings.
  Its earlier completed report and trace are retained.
- The subtle case found six issues. Its 12-turn inspection reached the first self-review request.
  One additional LLM request completed review using the saved findings and observation previews.
  The final trace labels that turn `saved_trace_review` and records that no new scan occurred.
  No findings were added or withdrawn in that continuation.

The subtle case initially exceeded a separate 15-turn attempt before the history-tool description
was clarified. That incomplete attempt is not counted as a completed evaluation. It remains in the
ignored local run directory for diagnosis.

## Subtle-case rule-label mismatch

The expected historical-credential defect is labeled `SEC-001` with no current file or line, because
the file was deleted from the latest tree. The agent instead reported `SEC-004` for the same
secret-bearing `integration.env` file in history. Both rules appear in the general security catalog.
The evidence cites the addition and deletion commits and the redacted token assignment.

The other five findings match the expected rule/path pairs:

| Finding | Evidence location |
|---|---|
| CI misses pushes to the default branch | `.github/workflows/ci.yml:4` |
| Excessive workflow permissions | `.github/workflows/ci.yml:6` |
| Test failures are ignored | `.github/workflows/ci.yml:18` |
| Untrusted PR title enters a shell script | `.github/workflows/ci.yml:19` |
| Machine-specific default data path | `config.py:4` |

Strict rule/path matching therefore reports 5 true positives, 1 unmatched finding, and 1 missed
expectation. Human source review identifies that unmatched/missed pair as one correctly detected
defect with an alternate rule label. The raw metrics have not been edited to replace them with a
perfect score. These small fixture results do not predict performance on hidden repositories.

## Submission sample

The root `sample_report.json` is a fresh completed audit of the public `kennethreitz/samplemod`
repository. It used 15 model turns and retained two findings: missing CI test execution and an
assertion that only checks `True`. One draft finding was withdrawn during self-review. The report
was verified against the accepted `record_violation` calls and withdrawals in its trace; its
findings were not edited by hand.
