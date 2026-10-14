"""LLVM is a proposal engine; malformed or inequivalent proposals fail closed."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from ink_planner.llvm_learning import export_ir, import_ir, discover, discover_facts, parameters
from producers.semantic_laws import value, var, word, op
from producers.author_word_identities import checked_law


class LLVMBridge(unittest.TestCase):
    def setUp(self):
        self.u = value('U32')
        self.x, self.y = var(self.u, 'x'), var(self.u, 'y')

    def test_export_does_not_assume_no_overflow_and_masks_shifts(self):
        source = op(self.u, 'call:wrapping_shl', self.x, self.y)
        text, params = export_ir(source)
        self.assertIn('and i32 %p1, 31', text)
        self.assertNotIn('nuw', text)
        self.assertNotIn('nsw', text)
        self.assertEqual(params, {'x':self.u, 'y':self.u})

    def test_import_rejects_unsupported_or_poisoning_constructs(self):
        for body in ('ret i32 poison', 'ret i32 undef', '%v = load i32, ptr null\nret i32 %v',
                     '%v = freeze i32 %p0\nret i32 %v',
                     '%v = call i32 @external(i32 %p0)\nret i32 %v',
                     'br label %next\nnext:\nret i32 %p0',
                     'ret i32 %p0\nret i32 0'):
            with self.subTest(body=body), self.assertRaises(ValueError):
                import_ir('define i32 @candidate(i32 %p0) {\n'+body+'\n}\n', {'x':self.u}, self.u)

    def test_flags_do_not_become_assumptions(self):
        source = 'define i32 @candidate(i32 %p0) {\n%v = add nuw nsw i32 %p0, 1\nret i32 %v\n}\n'
        result = import_ir(source, {'x':self.u}, self.u)
        self.assertEqual(result, op(self.u, 'binary:+', self.x, word('U32', 1)))

    def test_actual_llvm_and_independent_checker(self):
        compiler = Path(os.environ.get('INK_COMPILER', 'target/debug/ink')).resolve()
        clang = shutil.which(os.environ.get('CLANG', 'clang'))
        self.assertTrue(compiler.is_file(), 'INK_COMPILER must name the built checker')
        self.assertIsNotNone(clang, 'clang is required for this integration test')
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            source, core = d/'source.ink', d/'core.json'
            source.write_text('module learner_test;fn identity(x:u32)->u32{return x;}')
            subprocess.run([str(compiler),'emit-core',str(source),'-o',str(core)], check=True, capture_output=True)
            examples = [op(self.u,'binary:-',op(self.u,'binary:+',self.x,self.y),self.x),
                        op(self.u,'call:word_or',op(self.u,'call:word_and',self.x,self.y),
                           op(self.u,'call:word_and',self.x,op(self.u,'call:word_not',self.y))),
                        op(self.u,'call:wrapping_shl',self.x,self.y),
                        op(value('U64'),'call:wrapping_shr',var(value('U64'),'wide'),self.y)]
            # This checks real discovery and proof admission, not machine speed.
            # Keep a finite acquisition budget with room for slower CI CPUs;
            # deadline enforcement has its own deterministic producer tests.
            authoring_seconds = 60
            for i, original in enumerate(examples):
                case=d/str(i); case.mkdir()
                candidate, params, receipt = discover(original, clang, case)
                goal = dict(params=params,conditions=[],**{'from':original,'to':candidate})
                checked_law(compiler, core, goal, case, seconds=authoring_seconds)
                self.assertEqual(len(receipt['output_ir_sha256']),64)
                if i < 2:
                    self.assertNotEqual(original,candidate)
            # A plausible LLVM nowrap-derived result cannot be used for Ink's
            # wrapping inputs. Independently reconstructed SAT finds MAX.
            bad = dict(params={'x':self.u},conditions=[],
                       **{'from':op(value('Bool'),'binary:>',op(self.u,'binary:+',self.x,word('U32',1)),self.x),
                          'to':{'sort':value('Bool'),'node':{'Bool':True}}})
            with self.assertRaisesRegex(ValueError,'counterexample'):
                checked_law(compiler,core,bad,d,seconds=authoring_seconds)
            masked=op(self.u,'call:word_and',self.x,word('U32',255))
            bounded=op(value('Bool'),'binary:<',masked,word('U32',256))
            alternatives, receipt=discover_facts(bounded)
            self.assertEqual(receipt['facts']['minimum'],1)
            for _,candidate in alternatives:
                checked_law(compiler,core,dict(params=parameters(bounded),conditions=[],
                            **{'from':bounded,'to':candidate}),d,seconds=authoring_seconds)
            # Branch-derived facts are never external assumptions. Prove the
            # entire guarded replacement and reject removing the guard's scope.
            for kind in ('U32','U64'):
                s=value(kind);x=var(s,'x')
                guard=op(value('Bool'),'binary:<',x,word(kind,256))
                masked=op(s,'call:word_and',x,word(kind,255))
                guarded=op(s,'call:choose',guard,masked,x)
                alternatives,_=discover_facts(guarded)
                self.assertIn(('branch-scoped-facts',x),alternatives)
                checked_law(compiler,core,dict(params=parameters(guarded),conditions=[],
                            **{'from':guarded,'to':x}),d,seconds=authoring_seconds)
                outside=op(s,'call:choose',guard,x,masked)
                with self.assertRaisesRegex(ValueError,'counterexample'):
                    checked_law(compiler,core,dict(params=parameters(outside),conditions=[],
                                **{'from':outside,'to':x}),d,seconds=authoring_seconds)
            # Facts remain suggestions: replay correlated arithmetic and
            # modulo-count shift consequences through the ordinary checker.
            for kind in ('U32','U64'):
                s=value(kind);x=var(s,'x');k=var(value('U32'),'k')
                count=op(value('U32'),'call:word_or',
                         op(value('U32'),'call:word_and',k,word('U32',3)),word('U32',4))
                examples=[op(s,'call:word_and',op(s,'binary:*',x,x),word(kind,2)),
                          op(s,'call:word_and',op(s,'binary:+',x,x),word(kind,1)),
                          op(s,'call:word_and',op(s,'call:wrapping_shl',x,count),word(kind,15))]
                for original in examples:
                    alternatives,_=discover_facts(original)
                    self.assertTrue(alternatives)
                    for _,candidate in alternatives:
                        checked_law(compiler,core,dict(params=parameters(original),conditions=[],
                                    **{'from':original,'to':candidate}),d,seconds=authoring_seconds)


if __name__ == '__main__':
    unittest.main()
