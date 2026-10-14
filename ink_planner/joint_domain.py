"""Exact, bounded offline convex-hull authoring for untrusted cost models.

The resulting integer halfspaces describe observed joint support, not confidence
in timing predictions. No theorem/application authority follows from this data.
"""
from fractions import Fraction
from itertools import combinations
from math import gcd, lcm

MAX_FACETS = 4096
MAX_WORK = 250_000
MAX_INTEGER_DIGITS = 256


def rref(rows, width):
    rows = [list(row) for row in rows]
    pivots = []
    for col in range(width):
        index = next((i for i in range(len(pivots), len(rows)) if rows[i][col]), None)
        if index is None:
            continue
        at = len(pivots)
        rows[at], rows[index] = rows[index], rows[at]
        divisor = rows[at][col]
        rows[at] = [x/divisor for x in rows[at]]
        for i in range(len(rows)):
            if i != at and rows[i][col]:
                factor = rows[i][col]
                rows[i] = [x-factor*y for x,y in zip(rows[i], rows[at])]
        pivots.append(col)
        if len(pivots) == len(rows):
            break
    return rows[:len(pivots)], pivots


def nullspace(rows, width):
    reduced, pivots = rref(rows, width)
    result = []
    for free in range(width):
        if free in pivots:
            continue
        vector = [Fraction(0)]*width
        vector[free] = Fraction(1)
        for row,pivot in zip(reduced,pivots):
            vector[pivot] = -row[free]
        result.append(vector)
    return result


def integer_row(row, equality=False):
    denominator = lcm(*(x.denominator for x in row))
    values = [x.numerator*(denominator//x.denominator) for x in row]
    common = gcd(*values)
    if not common:
        raise ValueError('zero joint-domain constraint')
    values = [x//common for x in values]
    if equality and next(x for x in values if x) < 0:
        values = [-x for x in values]
    if any(len(str(abs(x))) > MAX_INTEGER_DIGITS for x in values):
        raise ValueError('joint-domain integer budget')
    return tuple(values)


def derive(names, features):
    """Full convex hull, including exact affine relations for degenerate data.

    Incremental beneath-beyond construction runs in the affine span. A strict
    interior point from the initial simplex orients every facet; coplanar faces
    can stay triangulated and are deduplicated when emitting halfspaces.
    Complexity limits reject authoring rather than silently widening the domain.
    """
    if not 1 <= len(names) <= 5 or len(set(names)) != len(names) or not 1 <= len(features) <= 4096:
        raise ValueError('joint-domain input budget')
    points = sorted(set(tuple(Fraction(f[n]) for n in names) for f in features))
    origin = points[0]
    differences = [tuple(x-y for x,y in zip(p,origin)) for p in points[1:]]
    reduced, axes = rref(differences, len(names))
    equalities = []
    for normal in nullspace(reduced,len(names)):
        equalities.append(integer_row([-sum(a*x for a,x in zip(normal,origin)),*normal],True))
    dimension = len(axes)
    if not dimension:
        return encode(names,equalities,[])
    projected = [tuple(p[i] for i in axes) for p in points]
    initial = [0]
    basis = []
    for i,p in enumerate(projected[1:],1):
        difference = [x-y for x,y in zip(p,projected[0])]
        trial,_ = rref([*basis,difference],dimension)
        if len(trial) > len(basis):
            initial.append(i);basis=trial
        if len(initial) == dimension+1:
            break
    interior = [sum(projected[i][j] for i in initial)/len(initial) for j in range(dimension)]
    work = 0

    def plane(face):
        nonlocal work
        work += 1
        if work > MAX_WORK:
            raise ValueError('joint-domain work budget')
        candidates = nullspace([[Fraction(1),*projected[i]] for i in face],dimension+1)
        if len(candidates) != 1:
            raise ValueError('degenerate joint-domain facet')
        row = candidates[0]
        inside = row[0]+sum(a*x for a,x in zip(row[1:],interior))
        if not inside:
            raise ValueError('joint-domain interior degeneracy')
        return integer_row([-x for x in row] if inside > 0 else row)

    faces = {tuple(face):plane(face) for face in combinations(initial,dimension)}
    for index,p in enumerate(projected):
        if index in initial:
            continue
        visible = []
        for face,row in faces.items():
            work += 1
            if work > MAX_WORK:
                raise ValueError('joint-domain work budget')
            if row[0]+sum(a*x for a,x in zip(row[1:],p)) > 0:
                visible.append(face)
        if not visible:
            continue
        horizon = {}
        for face in visible:
            for ridge in combinations(face,dimension-1):
                horizon[ridge] = horizon.get(ridge,0)+1
            del faces[face]
        for ridge,count in horizon.items():
            if count == 1:
                face = tuple(sorted((*ridge,index)))
                faces[face] = plane(face)
        if len(faces) > MAX_FACETS:
            raise ValueError('joint-domain facet budget')
    inequalities = []
    for row in set(faces.values()):
        full = [row[0],*([0]*len(names))]
        for i,coefficient in zip(axes,row[1:]):
            full[i+1] = coefficient
        inequalities.append(tuple(full))
    # Independently check every emitted facet contains every training point.
    for row in inequalities:
        for p in points:
            work += 1
            if work > MAX_WORK:
                raise ValueError('joint-domain work budget')
            if row[0]+sum(a*x for a,x in zip(row[1:],p)) > 0:
                raise ValueError('joint-domain construction failed')
    return encode(names,equalities,inequalities)


def encode(names,equalities,inequalities):
    if len(set(equalities))+len(set(inequalities)) > MAX_FACETS:
        raise ValueError('joint-domain constraint budget')
    return dict(features=list(names),
                equalities=[[str(x) for x in row] for row in sorted(set(equalities))],
                inequalities=[[str(x) for x in row] for row in sorted(set(inequalities))])
