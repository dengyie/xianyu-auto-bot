#!/usr/bin/env python3
"""Trim unused module-level imports via LINE-BASED rebuild (no byte splicing)."""
import ast, io, re, subprocess
from pathlib import Path

PY = str(Path('.venv/Scripts/python').resolve())

def pyflakes_unused(path):
    out = subprocess.run([PY, '-m', 'pyflakes', str(path)], capture_output=True, text=True).stdout
    unused = set()
    for m in re.finditer(r":\d+:\d+: '([\w.]+)' imported but unused", out):
        unused.add(m.group(1))
    return unused

def trim(path, keep=frozenset()):
    src = io.open(path, encoding='utf-8').read()
    removed_total = 0
    for _ in range(4):
        unused = pyflakes_unused(path)
        if not unused:
            return removed_total
        tree = ast.parse(src)
        lines = src.splitlines(keepends=True)
        # collect top-level import statements with line ranges + kept names
        stmts = []  # (start_line, end_line, rebuilt_line or None)
        for stmt in tree.body:
            if not isinstance(stmt, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(stmt, ast.ImportFrom) and stmt.module == '__future__':
                continue
            drop_idx = set()
            if isinstance(stmt, ast.ImportFrom):
                mod = stmt.module or ''
                for i, a in enumerate(stmt.names):
                    if a.name == '*':
                        continue
                    binding = a.asname or a.name
                    if (binding in unused or f"{mod}.{a.name}" in unused) and binding not in keep:
                        drop_idx.add(i)
            else:
                for i, a in enumerate(stmt.names):
                    binding = a.asname or a.name.split('.')[0]
                    if (binding in unused or a.name in unused) and binding not in keep:
                        drop_idx.add(i)
            kept = [a for i, a in enumerate(stmt.names) if i not in drop_idx]
            if isinstance(stmt, ast.ImportFrom):
                rebuilt = 'from ' + (stmt.module or '') + ' import ' + ', '.join(
                    (a.name + ' as ' + a.asname) if a.asname else a.name for a in kept) if kept else None
            else:
                rebuilt = 'import ' + ', '.join(
                    (a.name + ' as ' + a.asname) if a.asname else a.name for a in kept) if kept else None
            stmts.append((stmt.lineno, stmt.end_lineno, rebuilt))
            removed_total += len(drop_idx)
        if not stmts:
            return removed_total
        # rebuild line-wise
        out, skip_until = [], 0
        for i, ln in enumerate(lines, 1):
            if i <= skip_until:
                continue
            hit = next((s for s in stmts if s[0] == i), None)
            if hit:
                _, end, rebuilt = hit
                skip_until = end
                if rebuilt is not None:
                    out.append(rebuilt + '\n')
                # drop one following blank line to avoid double blanks
                if end < len(lines) and lines[end].strip() == '':
                    skip_until = end + 1
                continue
            out.append(ln)
        src = re.sub(r'\n{4,}', '\n\n\n', ''.join(out))
        io.open(path, 'w', encoding='utf-8', newline='').write(src)
    return removed_total

if __name__ == '__main__':
    import sys
    ext = set()
    for pat in ['tests/**/*.py', 'run_web_only.py', 'XianyuAutoAsync.py', 'ai_reply_engine.py',
                'cookie_manager.py', 'xianyu_*.py', 'app/**/*.py', 'utils/*.py', 'db_manager/*.py']:
        for p in Path('.').glob(pat):
            if p.name != 'reply_server.py' and p.is_file():
                try:
                    ext |= set(re.findall(r"\breply_server\.(\w+)", io.open(p, encoding='utf-8', errors='replace').read()))
                except Exception:
                    pass
    print('keep (external refs):', len(ext))
    n = trim(Path('reply_server.py'), keep=frozenset(ext))
    print('reply_server removed:', n)
    for p in sorted(Path('app/api/routers').glob('*.py')):
        if p.name == '__init__.py':
            continue
        n = trim(p)
        if n:
            print(f'{p.name} removed:', n)
