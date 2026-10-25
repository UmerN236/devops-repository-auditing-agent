"""The agent loop: the LLM observes tool results, reasons, picks the next tool, and records findings."""

import json

from prompts import FINISH_REVIEW, SYSTEM_PROMPT
from report import CATEGORIES, SEVERITIES, build_report, normalize_violation, redact, redact_data
from tools import TOOL_SCHEMAS, ToolError

MAX_TOOL_CHARS = 24_000      # cap on a single tool result sent to the LLM
MAX_HISTORY_CHARS = 400_000  # older tool results are elided beyond this

AGENT_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "record_violation",
            "description": "Record one confirmed violation. Returns its id, or an error to fix.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rule_id": {"type": "string", "description": "e.g. SEC-001 (see rule catalog)"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "severity": {"type": "string", "enum": SEVERITIES},
                    "file": {"type": "string", "description": "Repo-relative path; '' if not applicable"},
                    "line": {"type": "integer", "description": "1-based line; 0 if not applicable"},
                    "description": {"type": "string"},
                    "evidence": {"type": "string", "description": "Concrete observation; never secret values"},
                    "recommendation": {"type": "string"},
                },
                "required": ["rule_id", "category", "severity", "file", "line",
                             "description", "evidence", "recommendation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "End the audit. Optionally withdraw recorded findings that are not well supported.",
            "parameters": {
                "type": "object",
                "properties": {
                    "withdraw": {"type": "array", "items": {"type": "string"},
                                 "description": "Ids (e.g. 'V2') of findings to drop"},
                    "summary": {"type": "string"},
                },
            },
        },
    },
]


class Auditor:
    def __init__(self, llm, tools, repo_url, max_steps=40, log=None):
        self.llm = llm
        self.tools = tools
        self.repo_url = repo_url
        self.max_steps = max_steps
        self.log = log or (lambda msg: None)
        self.violations = {}  # id -> violation
        self._next_id = 0
        self.trace = []
        self.reviewed = False
        self.done = False
        self._turn = 0
        self._review_turn = None

    @classmethod
    def complete_saved_review(cls, llm, saved):
        """Finish an already-started self-review without repeating the inspection.

        Saved result previews may be truncated. The reviewer can only retain or withdraw
        findings accepted by the original run; it cannot create findings or claim a new scan.
        """
        if not isinstance(saved, dict) or saved.get('completed') is not False:
            raise ValueError('Review continuation requires an incomplete audit trace')
        if not isinstance(saved.get('steps'), list) or not isinstance(saved.get('repository'), str):
            raise ValueError('Invalid saved audit trace')
        auditor = cls(llm, None, saved['repository'])
        review_started = False
        for step in saved['steps']:
            for call in step.get('tool_calls', []):
                name, preview = call.get('tool'), call.get('result_preview', '')
                args = json.loads(call.get('arguments', '{}'))
                if name == 'record_violation' and preview.startswith('Recorded V'):
                    identifier = preview.split()[1].rstrip('.')
                    finding, error = normalize_violation(args, {args.get('file')})
                    if error:
                        raise ValueError(f'Invalid saved finding: {error}')
                    auditor.violations[identifier] = finding
                elif name == 'finish' and not preview.startswith('ERROR:'):
                    for identifier in args.get('withdraw') or []:
                        auditor.violations.pop(identifier, None)
                    review_started |= 'Before finishing, review your work.' in preview
        if not review_started:
            raise ValueError('The saved inspection has not reached self-review; run a complete inspection first')
        auditor.reviewed = True
        auditor._review_turn = max((s['step'] for s in saved['steps']), default=0)
        auditor._turn = auditor._review_turn + 1
        messages = [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': (
                'An inspection reached its turn limit immediately after requesting self-review. '
                'Complete only that outstanding review using the saved observations below. '
                'These are genuine saved tool-result PREVIEWS and may be truncated. Findings were '
                'validated and recorded during the original inspection. No new inspection has occurred. '
                'You may retain or withdraw existing finding IDs using finish. Do not add findings. '
                'Withdraw any finding that is unsupported or an acceptable alternative. If the saved '
                'evidence is insufficient to finish, explain the required inspection instead of calling finish.\n'
                + json.dumps({'findings': auditor.violations, 'observation_trace': saved['steps']})
            )},
        ]
        reply = llm.chat(messages, [AGENT_SCHEMAS[1]])
        calls = reply.get('tool_calls') or []
        if len(calls) != 1 or calls[0]['function']['name'] != 'finish':
            raise ValueError('Saved-evidence review needs further inspection; no completed report was produced')
        raw = calls[0]['function']['arguments']
        result = auditor.dispatch('finish', raw)
        if not auditor.done:
            raise ValueError(f'Saved-evidence review did not finish: {result}')
        updated = json.loads(json.dumps(saved))
        updated['steps'].append({
            'step': auditor._turn, 'phase': 'saved_trace_review',
            'model': getattr(llm, 'last_model', None) or llm.model,
            'thought': redact(reply.get('content') or ''),
            'tool_calls': [{'tool': 'finish', 'arguments': json.dumps(redact_data(json.loads(raw))),
                            'result_preview': redact(result[:500])}],
        })
        updated['completed'] = True
        updated['review_continuation'] = 'Review of saved findings and truncated observation previews; no new scan'
        updated['models_used'] = list(dict.fromkeys(s.get('model', saved['model']) for s in updated['steps']))
        return build_report(saved['repository'], list(auditor.violations.values())), updated

    # ----------------------------------------------------------------- dispatch
    def dispatch(self, name, raw_args):
        try:
            args = json.loads(raw_args or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except ValueError as e:
            return f"ERROR: invalid JSON arguments: {e}"
        try:
            if name == "record_violation":
                return self.record_violation(args)
            if name == "finish":
                return self.finish(args)
            if name in {s["function"]["name"] for s in TOOL_SCHEMAS}:
                return getattr(self.tools, name)(**args)
            return f"ERROR: unknown tool '{name}'"
        except ToolError as e:
            return f"ERROR: {e}"
        except (TypeError, ValueError) as e:
            return f"ERROR: bad arguments for {name}: {e}"
        except OSError as e:
            return f"ERROR: {name} failed: {e}"

    def record_violation(self, args):
        v, error = normalize_violation(args, set(self.tools.files()))
        if error:
            return f"ERROR: not recorded: {error}"
        if v["line"] is not None and v["file"] not in self.tools.inspected:
            return (f"ERROR: not recorded: you have not inspected {v['file']} yet. Read it (or find the "
                    "line with search_code) to confirm the line, then record again.")
        if v["file"] is not None and v["file"] not in self.tools.inspected:
            return f"ERROR: not recorded: read or search {v['file']} to inspect the evidence first."
        if v["line"] is not None and v["line"] not in self.tools.observed_lines.get(v["file"], set()):
            return (f"ERROR: not recorded: line {v['line']} of {v['file']} has not been shown by a tool. "
                    "Read the cited line, then record again.")
        for vid, other in self.violations.items():
            if (other["rule_id"], other["file"], other["line"]) == (v["rule_id"], v["file"], v["line"]):
                return f"ERROR: duplicate of {vid}; not recorded."
        self._next_id += 1
        vid = f"V{self._next_id}"
        self.violations[vid] = v
        return f"Recorded {vid}."

    def finish(self, args):
        withdraw = args.get('withdraw') or []
        if not isinstance(withdraw, list) or not all(isinstance(w, str) for w in withdraw):
            raise ValueError('withdraw must be a list of finding IDs')
        withdrawn = [w for w in withdraw if self.violations.pop(w, None)]
        if not self.reviewed:
            self.reviewed = True
            self._review_turn = self._turn
            listing = "\n".join(
                f"{vid}: [{v['rule_id']}/{v['severity']}] {v['file'] or '-'}:{v['line'] or '-'} {v['description']}"
                for vid, v in self.violations.items()
            ) or "(none)"
            note = f"Withdrew {withdrawn}.\n" if withdrawn else ""
            return note + FINISH_REVIEW.format(findings=listing)
        if self._review_turn == self._turn:
            return 'Read the self-review result before finishing: call finish again on your next turn.'
        self.done = True
        return f"Audit finished. Withdrew {withdrawn or 'nothing'}. {len(self.violations)} findings kept."

    # --------------------------------------------------------------------- loop
    def _compact(self, messages):
        """Elide the oldest tool outputs if the conversation grows too large."""
        total = sum(len(m.get("content") or "") for m in messages)
        for m in messages:
            if total <= MAX_HISTORY_CHARS:
                break
            if m["role"] == "tool" and len(m["content"]) > 200:
                total -= len(m["content"])
                m["content"] = "[older tool output elided to save space; re-run the tool if needed]"

    def run(self):
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"Audit the repository {self.repo_url}. It is cloned locally with "
                f"{len(self.tools.files())} tracked files. You have at most {self.max_steps} model turns, "
                "including self-review. Batch independent tool calls and confirmed findings where useful. "
                "Start by exploring it with the tools."
            )},
        ]
        schemas = TOOL_SCHEMAS + AGENT_SCHEMAS
        for step in range(1, self.max_steps + 1):
            self._turn = step
            if step == self.max_steps - 2:
                messages.append({"role": "user", "content": (
                    "Step budget almost exhausted. Record any remaining confirmed violations now, "
                    "then call finish.")})
            self._compact(messages)
            reply = self.llm.chat(messages, schemas)
            messages.append(reply)
            if reply.get("content"):
                self.log(f"[step {step}] thought: {redact(reply['content'].strip())[:300]}")
            entry = {"step": step, "model": getattr(self.llm, 'last_model', None) or self.llm.model,
                     "thought": redact(reply.get("content") or ""), "tool_calls": []}
            self.trace.append(entry)

            calls = reply.get("tool_calls") or []
            if not calls:
                messages.append({"role": "user", "content": (
                    "Continue the audit by calling a tool. Record findings with record_violation "
                    "and call finish when done.")})
                continue
            for call in calls:
                name, raw = call["function"]["name"], call["function"]["arguments"]
                result = redact(str(self.dispatch(name, raw)))
                if len(result) > MAX_TOOL_CHARS:
                    result = result[:MAX_TOOL_CHARS] + "\n... [output truncated]"
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
                try:
                    safe_args = json.dumps(redact_data(json.loads(raw)))
                except (ValueError, TypeError):
                    safe_args = "[invalid JSON arguments omitted]"
                first_line = (result.splitlines() or ["(empty result)"])[0]
                self.log(f"[step {step}] {name}({safe_args[:200]}) -> {first_line[:150]}")
                entry["tool_calls"].append({"tool": name, "arguments": safe_args,
                                            "result_preview": result[:500]})
            if self.done:
                break
        else:
            self.log("Step budget exhausted; reporting the findings recorded so far.")
        return build_report(self.repo_url, list(self.violations.values()))
