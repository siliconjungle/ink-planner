import json,os,sys,tempfile,time,unittest
from pathlib import Path
from ink_planner.recipe_streams import run

class RecipeStreams(unittest.TestCase):
    def jobs(self,root,scripts):
        jobs=[]
        for i,script in enumerate(scripts):
            output=root/f'{i}.jsonl'
            jobs.append(dict(command=[sys.executable,'-c',script,str(output)],env=dict(os.environ),output=output,
                cache=root/f'{i}.cache',stdout=root/f'{i}.stdout',stderr=root/f'{i}.stderr',key=str(i)))
        return jobs
    def test_streams_fast_family_before_slow_family_finishes(self):
        with tempfile.TemporaryDirectory()as tmp:
            jobs=self.jobs(Path(tmp),[
                "import sys,time;f=open(sys.argv[1],'w');f.write('slow-header\\n');f.flush();time.sleep(.5);f.write('slow-final\\n')",
                "import sys;open(sys.argv[1],'w').write('fast-header\\nfast-final\\n')"])
            events=list(run(jobs,2,time.monotonic()+5,3));rows=[(j['key'],e['raw'])for j,e in events if e['kind']=='line']
            self.assertLess(rows.index(('1',b'fast-final')),rows.index(('0',b'slow-final')))
            self.assertTrue(all(e['complete']for _,e in events if e['kind']=='done'))
    def test_deadline_closes_nested_processes_even_after_leader_exits(self):
        if os.name!='posix':self.skipTest('POSIX group lifecycle')
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);pidfile=root/'child.pid'
            script="import subprocess,sys;from pathlib import Path;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);Path(sys.argv[1]+'.pid').write_text(str(p.pid));open(sys.argv[1],'w').write('header\\n')"
            jobs=self.jobs(root,[script]);events=list(run(jobs,1,time.monotonic()+3,2));pid=int(Path(str(jobs[0]['output'])+'.pid').read_text())
            import subprocess
            # Dead descendants may briefly be zombies, but cannot keep working.
            status=subprocess.run(['ps','-o','stat=','-p',str(pid)],capture_output=True,text=True).stdout.strip()
            self.assertTrue(not status or status.startswith('Z'),status)
    def test_early_consumer_close_kills_worker(self):
        with tempfile.TemporaryDirectory()as tmp:
            jobs=self.jobs(Path(tmp),["import sys,time;f=open(sys.argv[1],'w');f.write('header\\n');f.flush();time.sleep(30)"])
            stream=run(jobs,1,time.monotonic()+10,5);next(stream);start=time.monotonic();stream.close()
            self.assertLess(time.monotonic()-start,2)
    def test_truncated_tail_and_failed_worker_are_not_complete(self):
        with tempfile.TemporaryDirectory()as tmp:
            jobs=self.jobs(Path(tmp),["import sys;open(sys.argv[1],'w').write('header\\npartial')", "import sys;open(sys.argv[1],'w').write('header\\n');sys.exit(7)"])
            done=[e for _,e in run(jobs,2,time.monotonic()+5,3)if e['kind']=='done']
            self.assertEqual(len(done),2);self.assertTrue(all(not e['complete']for e in done))
    def test_timeout_retains_complete_earlier_rows(self):
        with tempfile.TemporaryDirectory()as tmp:
            jobs=self.jobs(Path(tmp),["import sys,time;f=open(sys.argv[1],'w');f.write('header\\nproposal\\n');f.flush();time.sleep(30)"])
            events=list(run(jobs,1,time.monotonic()+5,.15))
            self.assertIn(b'proposal',[e['raw']for _,e in events if e['kind']=='line'])
            self.assertEqual(events[-1][1]['reason'],'proposal process time budget')

class ReapedGroup(unittest.TestCase):
    def test_os_refusal_on_reaped_group_does_not_abort_selection(self):
        if os.name!='posix':self.skipTest('POSIX lifecycle')
        from unittest.mock import Mock,patch
        from ink_planner.recipe_streams import stop
        process=Mock(pid=999999);process.poll.return_value=0
        with patch('os.killpg',side_effect=PermissionError):stop(process)
        process.kill.assert_not_called();process.wait.assert_called_once()
