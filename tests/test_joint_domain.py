"""Exact support geometry and the real shared Wasm profitability consumer."""
import itertools
import json
import math
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import unittest
from fractions import Fraction
from unittest.mock import patch
from ink_planner import joint_domain


def inside(domain, values):
    for field in ['equalities','inequalities']:
        for row in domain[field]:
            value=Fraction(int(row[0]))+sum(int(a)*Fraction(v) for a,v in zip(row[1:],values))
            if (field=='equalities' and value) or (field=='inequalities' and value>0):return False
    return True


def make(points):
    names=['input_elements','iterations','input_bytes','dispatches','output_capacity_elements'][:len(points[0])]
    return joint_domain.derive(names,[dict(zip(names,p)) for p in points])


class JointDomain(unittest.TestCase):
    def test_affine_relations_singletons_intervals_and_fractional_inputs(self):
        for points,yes,no in [
            ([(10,10),(20,20),(30,30)],[(10,10),(15,15),(30,30)],[(10,30),(30,10),(5,5),(31,31)]),
            ([(2,4,6)],[(2,4,6)],[(2,4,7)]),
            ([(0,),(10,),(5,)],[(0,),(1,),(10,)],[(11,),(-1,)]),
            ([(0.25,0.5),(0.5,1.0)],[(0.375,0.75)],[(0.375,math.nextafter(0.75,1.0))])]:
            domain=make(points)
            for point in yes:self.assertTrue(inside(domain,point),(points,point,domain))
            for point in no:self.assertFalse(inside(domain,point),(points,point,domain))

    def test_triangle_cube_and_full_five_dimensional_simplex(self):
        triangle=make([(0,0),(10,0),(0,10),(2,2),(5,5)])
        self.assertTrue(inside(triangle,(2,3)))
        self.assertFalse(inside(triangle,(9,9)))  # In box and affine span, outside hull.
        for dimension in [3,4,5]:
            cube=make(list(itertools.product([0,2],repeat=dimension))+[(1,)*dimension])
            for point in itertools.product([0,1,2],repeat=dimension):self.assertTrue(inside(cube,point))
            self.assertFalse(inside(cube,(*(1,)*(dimension-1),3)))
        simplex=make([(0,)*5]+[tuple(10 if i==j else 0 for i in range(5)) for j in range(5)])
        self.assertTrue(inside(simplex,(2,)*5));self.assertFalse(inside(simplex,(3,)*5))

    def test_random_two_dimensional_clouds_against_independent_monotone_chain(self):
        def cross(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
        rng=random.Random(73841)
        for _ in range(20):
            points=sorted(set((rng.randrange(10),rng.randrange(10)) for _ in range(15)))
            lower=[];upper=[]
            for target,ordered in [(lower,points),(upper,reversed(points))]:
                for point in ordered:
                    while len(target)>1 and cross(target[-2],target[-1],point)<=0:target.pop()
                    target.append(point)
            hull=lower[:-1]+upper[:-1];domain=make(points)
            for point in itertools.product(range(11),repeat=2):
                expected=all(cross(a,b,point)>=0 for a,b in zip(hull,hull[1:]+hull[:1]))
                self.assertEqual(inside(domain,point),expected,(points,point))
            self.assertEqual(domain,make(list(reversed(points))))

    def test_work_limits_reject_instead_of_widening(self):
        with patch.object(joint_domain,'MAX_WORK',1),self.assertRaisesRegex(ValueError,'work budget'):
            make([(0,0),(1,0),(0,1)])
        with patch.object(joint_domain,'MAX_INTEGER_DIGITS',1),self.assertRaisesRegex(ValueError,'integer budget'):
            make([(123,123)])

    def test_three_dimensional_clouds_against_independent_facet_enumeration(self):
        def dot(a,b):return sum(x*y for x,y in zip(a,b))
        def minus(a,b):return tuple(x-y for x,y in zip(a,b))
        def cross(a,b):return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
        rng=random.Random(91037)
        for _ in range(10):
            points=sorted(set(tuple(rng.randrange(5) for _ in range(3)) for _ in range(15)))
            planes=[]
            for a,b,c in itertools.combinations(points,3):
                normal=cross(minus(b,a),minus(c,a))
                if not any(normal):continue
                bound=dot(normal,a);sides=[dot(normal,p)-bound for p in points]
                if all(v<=0 for v in sides):planes.append((normal,bound))
                elif all(v>=0 for v in sides):planes.append((tuple(-v for v in normal),-bound))
            domain=make(points)
            for point in itertools.product(range(5),repeat=3):
                self.assertEqual(inside(domain,point),all(dot(n,point)<=b for n,b in planes),(points,point))
            # Embed the same cloud in five coordinates with two exact relations.
            embedded=make([(*p,p[0]+2*p[1],p[1]+p[2]) for p in points])
            for p in points:self.assertTrue(inside(embedded,(*p,p[0]+2*p[1],p[1]+p[2])))
            p=points[0];self.assertFalse(inside(embedded,(*p,p[0]+2*p[1]+1,p[1]+p[2])))

    def test_real_fitted_profiles_reject_joint_extrapolation_in_wasm(self):
        from ink_knowledge import Store
        from ink_planner.cost_profiles import fit
        project=Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=Store.publish(root/'knowledge',[]);rows=[]
            for backend in ['cpu','gpu']:
                for n in [10,20,30]:
                    o=dict(schema=1,program='0'*64,entries=[],plan='1'*64,
                        target=dict(hardware='fixture',driver='fixture',toolchain='fixture',backend=backend,bridge='packed'),
                        workload=dict(shape={'features':{'input_elements':n,'iterations':n}},distribution='fixture',concurrency=1,residency='captured-owned'),
                        metrics=dict(complete_call_ns=[str(n*(100 if backend=='cpu' else 10))],peak_bytes=None))
                    rows.append(dict(scope='joint',observation=store.observe(o)))
            _,profile=fit(store,rows,'fixture')
            self.assertEqual(profile['schema'],3)
            cases=[]
            for n,k,expected in [(10,10,'gpu'),(15,15,'gpu'),(30,30,'gpu'),(10,30,'cpu'),(30,10,'cpu'),(15,math.nextafter(15,16),'cpu')]:
                request=dict(options=dict(policy='adaptive',target_key='fixture',cost_profile=profile),context=dict(scope='joint',boundary='captured-owned',features=dict(input_elements=n,iterations=k)),eligible=['cpu','gpu'])
                cases.append(dict(request=request,expected=expected))
            # Exact arithmetic also handles subnormals and coefficients much
            # wider than a machine integer, without epsilon-based admission.
            support=make([(0,0),(1,1)])
            for field in ['equalities','inequalities']:
                support[field]=[[str(int(x)*10**200) for x in row] for row in support[field]]
            tiny_profile=dict(schema=3,target_key='fixture',models={'joint':dict(
                boundary='captured-owned',domains={'input_elements':[0,1],'iterations':[0,1]},
                backends={backend:dict(intercept_ns=price,terms={},support=support)
                          for backend,price in [('cpu',100),('gpu',1)]})})
            for n,k,expected in [(0,0,'gpu'),(math.ulp(0.0),math.ulp(0.0),'gpu'),(0,math.ulp(0.0),'cpu')]:
                cases.append(dict(request=dict(options=dict(policy='adaptive',target_key='fixture',cost_profile=tiny_profile),
                    context=dict(scope='joint',boundary='captured-owned',features=dict(input_elements=n,iterations=k)),eligible=['cpu','gpu']),expected=expected))
            for path,name in [('abi/selection.wasm','ink-selection.wasm'),('abi/src/selection.mjs','selection.mjs')]:shutil.copyfile(project/path,root/name)
            (root/'cases.json').write_text(json.dumps(cases))
            (root/'run.mjs').write_text("import assert from 'node:assert/strict';import {readFile} from 'node:fs/promises';import {selectionRequest} from './selection.mjs';for(const c of JSON.parse(await readFile(new URL('./cases.json',import.meta.url))))assert.equal((await selectionRequest(c.request)).backend,c.expected);")
            result=subprocess.run(['node',str(root/'run.mjs')],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)


if __name__=='__main__':unittest.main()
