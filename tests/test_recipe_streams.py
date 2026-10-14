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
            # Completion diagnostics are persisted inside canonical receipts.
            from decimal import Decimal
            from ink_knowledge import canonical
            done=[e for _,e in events if e['kind']=='done']
            self.assertTrue(all(isinstance(e['elapsed_seconds'],str)and Decimal(e['elapsed_seconds'])>=0 for e in done))
            canonical(dict(notes=done))
    def test_dispatch_interleaves_stages_before_repeating_a_slow_stage(self):
        with tempfile.TemporaryDirectory()as tmp:
            jobs=self.jobs(Path(tmp),[
                "import sys,time;f=open(sys.argv[1],'w');f.write('header\\n');f.flush();time.sleep(1)",
                "import sys,time;f=open(sys.argv[1],'w');f.write('header\\n');f.flush();time.sleep(1)",
                "import sys;open(sys.argv[1],'w').write('header\\nphysical-proposal\\n')"])
            for job in jobs[:2]:job['recipe']={'stage':'rewrite'}
            jobs[2]['recipe']={'stage':'physical'}
            events=list(run(jobs,2,time.monotonic()+.5,3))
            self.assertIn(('2',b'physical-proposal'),[(j['key'],e['raw'])for j,e in events if e['kind']=='line'])
            from ink_planner.recipe_streams import interleave_stages
            self.assertEqual([j['key']for j in interleave_stages(jobs)],['0','2','1'])
            self.assertEqual([j['key']for j in interleave_stages([{k:v for k,v in j.items()if k!='recipe'}for j in jobs])],['0','1','2'])
    def test_deadline_closes_nested_processes_even_after_leader_exits(self):
        if os.name!='posix':self.skipTest('POSIX group lifecycle')
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);pidfile=root/'child.pid'
            script="import subprocess,sys;from pathlib import Path;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);Path(sys.argv[1]+'.pid').write_text(str(p.pid));open(sys.argv[1],'w').write('header\\n')"
            jobs=self.jobs(root,[script]);events=list(run(jobs,1,time.monotonic()+3,2));pid=int(Path(str(jobs[0]['output'])+'.pid').read_text())
            import subprocess
            # Dead descendants may briefly be zombies, but cannot keep working.
            # Group SIGKILL delivery is asynchronous, particularly for a child
            # briefly waiting in the macOS kernel. Require eventual death within
            # a bounded interval; accepting a live sleeping child would hide a
            # regression that only killed the already exited group leader.
            deadline=time.monotonic()+1
            while True:
                status=subprocess.run(['ps','-o','stat=','-p',str(pid)],capture_output=True,text=True).stdout.strip()
                if not status or status.startswith('Z')or time.monotonic()>=deadline:break
                time.sleep(.01)
            self.assertTrue(not status or status.startswith('Z'),status)
    def test_database_priority_finishes_before_later_jobs_launch(self):
        with tempfile.TemporaryDirectory()as tmp:
            jobs=self.jobs(Path(tmp),[
                "import sys;open(sys.argv[1],'w').write('later\\n')",
                "import sys,time;time.sleep(.15);open(sys.argv[1],'w').write('earlier\\n')"])
            jobs[1]['recipe']={'stage':'maintained','priority':-10}
            stream=run(jobs,2,time.monotonic()+5,3)
            try:
                job,event=next(stream)
                self.assertEqual((job['key'],event['raw']),('1',b'earlier'))
                self.assertFalse(jobs[0]['stdout'].exists())
                events=list(stream)
                self.assertTrue(any(j['key']=='0'and e.get('raw')==b'later'for j,e in events))
            finally:stream.close()
    def test_priority_timeout_does_not_starve_later_jobs(self):
        with tempfile.TemporaryDirectory()as tmp:
            jobs=self.jobs(Path(tmp),[
                "import time;time.sleep(30)",
                "import sys;open(sys.argv[1],'w').write('later\\n')"])
            jobs[0]['recipe']={'priority':-10}
            events=list(run(jobs,2,time.monotonic()+3,.15))
            self.assertEqual(events[0][1]['reason'],'proposal process time budget')
            self.assertIn(('1',b'later'),[(j['key'],e.get('raw'))for j,e in events])
    def test_priority_groups_preserve_stage_round_robin(self):
        from ink_planner.recipe_streams import interleave_stages
        jobs=[dict(key=str(i),recipe=dict(stage=stage,priority=level))for i,(stage,level)in enumerate([
            ('a',0),('a',-1),('a',-1),('b',-1),('b',0),('a',0)])]
        self.assertEqual([j['key']for j in interleave_stages(jobs)],['1','3','2','0','4','5'])
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
            from ink_knowledge import canonical
            canonical(dict(notes=[events[-1][1]]))

class ReapedGroup(unittest.TestCase):
    def test_os_refusal_on_reaped_group_does_not_abort_selection(self):
        if os.name!='posix':self.skipTest('POSIX lifecycle')
        from unittest.mock import Mock,patch
        from ink_planner.recipe_streams import stop
        process=Mock(pid=999999);process.poll.return_value=0
        with patch('os.killpg',side_effect=PermissionError):stop(process)
        process.kill.assert_not_called();process.wait.assert_called_once()
