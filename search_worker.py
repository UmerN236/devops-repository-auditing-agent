"""Bound regex execution in a separate process; only this trusted code is executed."""

import json
import re
import sys
from pathlib import Path

from report import redact


def search(request):
    regex = re.compile(request['pattern'], re.I if request['ignore_case'] else 0)
    matches, total, size, skipped = [], 0, 0, []
    for relative, absolute in request['files']:
        try:
            full = Path(absolute)
            if full.stat().st_size > request['max_file_bytes']:
                skipped.append(relative)
                continue
            data = full.read_bytes()
        except OSError:
            skipped.append(relative)
            continue
        if b'\0' in data[:8000]:
            skipped.append(relative)
            continue
        text = data.decode('utf-8', errors='replace')
        safe_lines = redact(text).splitlines()
        for number, line in enumerate(text.splitlines(), 1):
            if not regex.search(line):
                continue
            total += 1
            row = f'{relative}:{number}: {safe_lines[number - 1].strip()[:request["max_line_chars"]]}'
            if len(matches) < request['max_matches'] and size + len(row) < request['max_output_chars']:
                matches.append({'path': relative, 'line': number, 'text': row})
                size += len(row) + 1
    return {'total': total, 'matches': matches, 'skipped': skipped[:5], 'skipped_count': len(skipped)}


if __name__ == '__main__':
    json.dump(search(json.load(sys.stdin)), sys.stdout)
