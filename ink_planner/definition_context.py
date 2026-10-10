"""Untrusted applicability prefilter. The core independently checks every binding.

This prevents a valid contextual law for another module from making the whole
bounded view unusable. It confers no proof authority and installs no rewrite.
"""


def applicable(law, program, subject):
    context = law.get('definition_context')
    if context is None:
        return True
    if not isinstance(context, dict) or context.get('schema') != 1:
        return False
    functions = {f['name']: f for f in program['functions']}
    for field in ('records', 'enums'):
        actual = program.get(field, {})
        if any(actual.get(n) != v for n, v in context.get(field, {}).items()):
            return False
    for name, bound in context.get('definitions', {}).items():
        actual = functions.get(name)
        definition = bound.get('definition', {})
        if actual is None or actual['params'] != definition.get('params') \
                or actual['result'] != definition.get('result') \
                or subject['functions'].get(name) != definition.get('body'):
            return False
    return True


def applicable_roots(store, roots, program, subject):
    """Dependency contexts must also be applicable. Cycles stay checker errors."""
    cache = {}

    def visit(identity, active):
        if identity in cache:
            return cache[identity]
        if identity in active:
            return False
        entry = store.object(identity)
        ok = applicable(entry['payload'], program, subject) and all(
            visit(dep, active | {identity}) for dep in entry['dependencies'])
        cache[identity] = ok
        return ok

    return [identity for identity in sorted(roots) if visit(identity, set())]
