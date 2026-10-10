"""Discover individual typed laws for the existing general semantic query engine.

The compiler independently checks the assembled view; paths and hashes confer
no theorem authority. Existing inline views preserve their explicit root set.
"""
import hashlib
import json
from pathlib import Path
from database_query import MAX_FILES, MAX_BYTES
from semantic_laws import SEMANTICS


def discover(root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError('semantic database root must be a directory')
    paths = []
    for pattern in ('**/objects/*.json', '**/catalogue.json'):
        for path in root.glob(pattern):
            if len(paths) >= MAX_FILES:
                raise ValueError('semantic database discovery file count limit')
            paths.append(path)
    objects, roots, total = {}, set(), 0

    def add(id_, law):
        if law.get('schema') != 1 or law.get('semantics') != SEMANTICS:
            raise ValueError('incompatible discovered semantic law')
        if len(id_) != 64 or any(c not in '0123456789abcdef' for c in id_):
            raise ValueError('invalid semantic law identity')
        if id_ in objects and objects[id_] != law:
            raise ValueError('conflicting semantic law identity')
        # Presentation order/whitespace cannot change matching or scheduling.
        # The compiler parses the typed wire object and checks its canonical
        # identity; generic JSON sorting is not a substitute for that check.
        objects[id_] = json.loads(json.dumps(law, sort_keys=True))
        if len(objects) > 1024:
            raise ValueError('semantic database active-view count limit')

    for path in sorted(set(paths)):
        if not path.resolve().is_relative_to(root):
            raise ValueError('semantic database entry escapes the configured root')
        with path.open('rb') as f:
            raw = f.read(16_000_001 if path.name == 'catalogue.json' else 4_000_001)
        total += len(raw)
        if len(raw) > (16_000_000 if path.name == 'catalogue.json' else 4_000_000) or total > MAX_BYTES:
            raise ValueError('semantic database discovery byte limit')
        value = json.loads(raw)
        if value.get('semantics') != SEMANTICS:
            continue
        if path.name == 'catalogue.json':
            if value['schema'] != 1:
                raise ValueError('incompatible semantic catalogue')
            for id_, law in value['objects'].items():
                add(id_, law)
            roots.update(value['roots'])
        else:
            add(path.stem, value)
            roots.add(path.stem)
    if not roots <= objects.keys():
        raise ValueError('missing discovered semantic root')
    catalogue = dict(schema=1, semantics=SEMANTICS, roots=sorted(roots),
                     objects=dict(sorted(objects.items())))
    if len(json.dumps(catalogue, separators=(',', ':')).encode()) > 16_000_000:
        raise ValueError('semantic database active-view byte limit')
    snapshot = dict(schema=1, semantics='ink-semantic-database-snapshot-v1',
                    roots=sorted(roots), entries=sorted(objects))
    digest = hashlib.sha256(json.dumps(snapshot, separators=(',', ':')).encode()).hexdigest()
    return catalogue, dict(snapshot_sha256=digest, snapshot=snapshot,
                           entries=len(objects), rewrite_roots=len(roots))
