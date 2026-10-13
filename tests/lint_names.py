"""Tiny pyflakes substitute (pyflakes is not installable offline): reports names that are used but never defined
(typos / forgotten imports after a refactor) and imports that are never used."""
import ast, builtins, symtable, sys

def check(path):
    src = open(path).read()
    top = symtable.symtable(src, path, "exec")
    tree = ast.parse(src)
    defined = set(dir(builtins)) | {"__file__", "__name__"}
    imported = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names: imported[(a.asname or a.name).split(".")[0]] = n.lineno
        elif isinstance(n, ast.ImportFrom):
            for a in n.names: imported[a.asname or a.name] = n.lineno
    for s in top.get_symbols():
        if s.is_assigned() or s.is_imported() or s.is_namespace(): defined.add(s.get_name())
    problems = []
    def walk(t):
        for s in t.get_symbols():
            if t.get_type() != "module" and s.is_global() and s.is_referenced() and s.get_name() not in defined:
                problems.append(f"{path}: undefined name '{s.get_name()}' in {t.get_name()}()")
        for c in t.get_children(): walk(c)
    walk(top)
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.value.id for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    for name, line in imported.items():
        if name not in used and not src.splitlines()[line - 1].rstrip().endswith(("noqa", "re-exported)")) and "noqa" not in src.splitlines()[line - 1]:
            problems.append(f"{path}:{line}: unused import '{name}'")
    return problems

if __name__ == "__main__":
    allp = [p for f in sys.argv[1:] for p in check(f)]
    print("\n".join(allp) or "no problems")
    sys.exit(1 if allp else 0)
