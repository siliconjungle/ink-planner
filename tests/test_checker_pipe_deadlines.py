import subprocess,sys,time,unittest
from types import SimpleNamespace
from ink_planner.compiled_planning import Planner

class CheckerPipeDeadlines(unittest.TestCase):
    def planner(self,script):
        p=Planner.__new__(Planner);p.options=SimpleNamespace(check_timeout=.15)
        p.session=subprocess.Popen([sys.executable,'-u','-c',script],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        p.response_buffer=bytearray();self.addCleanup(p.close);return p
    def test_partial_line_does_not_escape_deadline(self):
        p=self.planner("import sys,time;sys.stdout.write('{');sys.stdout.flush();time.sleep(10)")
        start=time.monotonic()
        with self.assertRaisesRegex(ValueError,'time budget'):p.receive()
        self.assertLess(time.monotonic()-start,2)
    def test_blocked_large_request_does_not_escape_deadline(self):
        p=self.planner('import time;time.sleep(10)');start=time.monotonic()
        with self.assertRaisesRegex(ValueError,'time budget'):p.send(b'x'*1_000_000,start+.15)
        self.assertLess(time.monotonic()-start,2)
    def test_multiple_buffered_lines_and_large_response_remain_exact(self):
        p=self.planner("import sys,json;print(json.dumps({'data':'x'*200000}),flush=True);print(json.dumps({'ready':True}),flush=True)")
        p.options.check_timeout=2
        self.assertEqual(p.receive(),dict(data='x'*200000));self.assertEqual(p.receive(),dict(ready=True))
