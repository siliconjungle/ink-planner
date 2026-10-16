"""Optional cache failure cannot invalidate a kernel-checked search output."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ink_knowledge import Store
from ink_planner import semantic_search,compiled_planning

INK=Path(os.environ.get('INK_COMPILER',Path(__file__).resolve().parents[2]/'target/debug/ink')).resolve()


@unittest.skipUnless(INK.is_file(),'requires the Ink compiler')
class CacheFailureCLI(unittest.TestCase):
    def test_semantic_search_keeps_checked_output_when_hint_cache_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'source.ink';source.write_text('module cache_failure; fn same(x:u32)->u32{return x;}')
            core=root/'core.json';emitted=subprocess.run([INK,'emit-core',source,'-o',core],capture_output=True,text=True)
            self.assertEqual(emitted.returncode,0,emitted.stderr)
            knowledge=root/'knowledge';Store.publish(knowledge,[])
            output=root/'selection.json';stdout=io.StringIO();stderr=io.StringIO()
            arguments=['ink-search','--compiler',str(INK),'--core',str(core),
                '--snapshot',str(knowledge/'store/snapshot.json'),'--knowledge-root',str(knowledge),
                '--cache-dir',str(root/'hints'),'--budget','0','--rounds','0','-o',str(output)]
            with patch('sys.argv',arguments),patch('ink_planner.plan_cache.save',return_value=False),contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
                semantic_search.main()
            self.assertIn('checked output retained',stderr.getvalue())
            self.assertIsInstance(json.loads(stdout.getvalue()),dict)
            checked=subprocess.run([INK,'check-selection',core,output],capture_output=True,text=True)
            self.assertEqual(checked.returncode,0,checked.stderr)

    def test_compiled_planner_keeps_checked_output_when_hint_cache_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'source.ink';source.write_text('module cache_failure; fn same(x:u32)->u32{return x;}')
            core=root/'core.json';emitted=subprocess.run([INK,'emit-core',source,'-o',core],capture_output=True,text=True)
            self.assertEqual(emitted.returncode,0,emitted.stderr)
            knowledge=root/'knowledge';Store.publish(knowledge,[])
            registry=root/'recipes.json';registry.write_text(json.dumps(dict(schema=1,semantics='ink-planning-recipes-v1',recipes=[])))
            output=root/'plan.json';stdout=io.StringIO();stderr=io.StringIO()
            arguments=['ink-plan','--compiler',str(INK),'--source',str(core),'--core',
                '--knowledge-root',str(knowledge),'--registry',str(registry),'--target','javascript',
                '--measurement-mode','off','--planning-effort','quick','--budget','0','--time-seconds','0','-o',str(output)]
            with patch('sys.argv',arguments),patch('ink_planner.compiled_cache.save',return_value=False),contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
                compiled_planning.main()
            self.assertIn('checked output retained',stderr.getvalue())
            self.assertEqual(json.loads(stdout.getvalue())['status'],'checked')
            checked=subprocess.run([INK,'check-plan',core,output,'--core'],capture_output=True,text=True)
            self.assertEqual(checked.returncode,0,checked.stderr)


if __name__=='__main__':unittest.main()
