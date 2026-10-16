"""Actual child lifecycle/RSS evidence, independent of compiler cost ranking."""
import hashlib,json,os,shutil,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from ink_planner.native_measurements import run_observed,measure

@unittest.skipUnless(sys.platform in ('darwin','linux')and hasattr(os,'wait4'),'native RSS units supported on Darwin/Linux')
class NativeProcessMeasurements(unittest.TestCase):
    def test_complete_process_output_and_touched_allocation_peak(self):
        # Every byte is touched; this tests actual reported resident memory and
        # byte units rather than an uncommitted address-space reservation.
        code="import sys;x=bytearray(48*1024*1024);x[:]=b'z'*len(x);print(len(x));print('diagnostic',file=sys.stderr)"
        result,peak=run_observed([sys.executable,'-c',code],timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),str(48*1024*1024))
        self.assertEqual(result.stderr.strip(),'diagnostic')
        self.assertGreaterEqual(peak,48*1024*1024)

    def test_failed_child_retains_status_and_diagnostics(self):
        result,peak=run_observed([sys.executable,'-c',"import sys;print('invalid output',file=sys.stderr);sys.exit(7)"],timeout=15)
        self.assertEqual(result.returncode,7)
        self.assertIn('invalid output',result.stderr)
        self.assertGreater(peak,0)

    def test_peak_belongs_to_this_child_not_previous_children(self):
        # Start with a fresh observer. On Linux a fork/exec child's peak can
        # include the observer's resident pages before exec. The full test
        # process may already exceed this allocation; that conservative launch
        # peak is not evidence that wait4 returned a previous child's usage.
        script='''import json,sys
from ink_planner.native_measurements import run_observed
large,large_peak=run_observed([sys.executable,'-c','x=bytearray(96*1024*1024);print(len(x))'],timeout=15)
small,small_peak=run_observed([sys.executable,'-c','print(1)'],timeout=15)
assert large.returncode==0,large.stderr
assert small.returncode==0,small.stderr
print(json.dumps([large_peak,small_peak]))
'''
        env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        result=subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,text=True,timeout=40)
        self.assertEqual(result.returncode,0,result.stderr)
        large_peak,small_peak=json.loads(result.stdout)
        self.assertGreaterEqual(large_peak,96*1024*1024)
        # An accumulated RUSAGE_CHILDREN maximum would return the large peak
        # again, falsely attaching that allocation to this later small child.
        self.assertLess(small_peak,large_peak//2)

    def test_large_output_cannot_block_child_before_reaping(self):
        result,peak=run_observed([sys.executable,'-c',"import sys;sys.stdout.write('x'*1048576);sys.stderr.write('y'*1048576)"],timeout=15)
        self.assertEqual(result.returncode,0)
        self.assertEqual(result.stdout,'x'*1048576)
        self.assertEqual(result.stderr,'y'*1048576)
        self.assertGreater(peak,0)

    def test_timeout_kills_and_reaps_this_exact_child(self):
        with tempfile.TemporaryDirectory()as tmp:
            path=Path(tmp)/'pid'
            code="import os,pathlib,time;pathlib.Path("+repr(str(path))+").write_text(str(os.getpid()));time.sleep(30)"
            with self.assertRaises(subprocess.TimeoutExpired):
                run_observed([sys.executable,'-c',code],timeout=1)
            self.assertTrue(path.exists(),'test child must enter its workload before the deadline')
            pid=int(path.read_text())
            with self.assertRaises(ProcessLookupError):os.kill(pid,0)
            with self.assertRaises(ChildProcessError):os.waitpid(pid,os.WNOHANG)

    @unittest.skipUnless(shutil.which('cargo'),'native adapter needs Cargo')
    def test_actual_adapter_rejects_rounded_word_counterexample_and_keeps_memory(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);directory=root/'planning';directory.mkdir()
            artifacts=[]
            for name,body in [('baseline','args[0].clone()'),('equivalent','args[0].clone()'),
                              ('wrong','if function=="unsigned"{serde_json::json!(args[0].as_u64().unwrap()+1)}else{args[0].clone()}')]:
                crate=root/name;(crate/'src').mkdir(parents=True)
                (crate/'Cargo.toml').write_text('[package]\nname="'+name+'"\nversion="0.1.0"\nedition="2021"\n[workspace]\n[dependencies]\nserde_json={version="=1.0.151",features=["float_roundtrip"]}\n')
                (crate/'src/lib.rs').write_text('''use serde_json::Value;
pub struct State;
impl State {
 pub fn new()->Self{Self}
 pub fn checkpoint(&self)->Result<Vec<u8>,String>{Ok(vec![])}
 pub fn call_json(&mut self,function:&str,args:&Value)->Result<Value,String>{Ok('''+body+''')}
}''')
                artifacts.append(dict(measurement=name,package=name,path=str(crate)))
            # These first two outputs collapse under the old f64 comparison.
            self.assertEqual(float(2**53),float(2**53+1))
            phases={'unsigned':[dict(function='unsigned',args=[2**53],expected=2**53)],
                    'signed':[dict(function='signed',args=[-2**63],expected=-2**63)],
                    'floating':[dict(function='floating',args=[-0.0],expected=-0.0)]}
            fingerprints=[]
            for calls in phases.values():
                for call in calls:
                    # Transport fixtures only; real acquisition gets these
                    # fingerprints from the source-bound reference decoder.
                    call['input_sha256']=hashlib.sha256(json.dumps([call['function'],call['args']]).encode()).hexdigest()
                    fingerprints.append(call['input_sha256'])
            planner=SimpleNamespace(root=root,directory=directory,options=SimpleNamespace(measure_timeout=120,measure_processes=2))
            report,build=measure(planner,artifacts,phases,dict(processes=2,rounds=9,minimum_batch_ms=1,maximum_calls_per_sample=1))
            self.assertEqual(report['processes'],2)
            self.assertEqual({row['measurement']for row in report['rows']},{'baseline','equivalent'})
            self.assertEqual({row['measurement']for row in report['rejected']},{'wrong'})
            self.assertEqual({row['process']for row in report['rows']},{0,1})
            self.assertEqual(report['input_fingerprints'],sorted(fingerprints))
            self.assertTrue(all(type(row['peak_rss_bytes'])is int and row['peak_rss_bytes']>0 for row in report['rows']))
            self.assertIn('wait4',build['scope'])
            # Exercise numeric edge cases in the exact runner source as well.
            env=dict(os.environ,CARGO_TARGET_DIR=str(root/'offline-native-target'))
            project=Path(build['project_path'])
            result=subprocess.run(['cargo','test','--release','--offline','--manifest-path',str(project/'Cargo.toml')],env=env,capture_output=True,text=True,timeout=120)
            self.assertEqual(result.returncode,0,result.stderr[-3000:])
            self.assertIn('2 passed',result.stdout)
            executable=root/'offline-native-target/release/ink-offline-calls'
            request=project/'request.json'
            good_hash=hashlib.sha256(request.read_bytes()).hexdigest()
            # Changes after preparation cannot receive costs under the old
            # source-bound fingerprints, even if the new outputs would match.
            changed=json.loads(request.read_text());changed['phases']['unsigned'][0]['args']=[2**53+2]
            changed['phases']['unsigned'][0]['expected']=2**53+2
            request.write_text(json.dumps(changed))
            result=subprocess.run([str(executable),str(request),good_hash],capture_output=True,text=True,timeout=15)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('request hash mismatch',result.stderr)
            changed['phases']['unsigned'][0].pop('input_sha256')
            request.write_text(json.dumps(changed))
            result=subprocess.run([str(executable),str(request),hashlib.sha256(request.read_bytes()).hexdigest()],capture_output=True,text=True,timeout=15)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('reference input fingerprint required',result.stderr)

if __name__=='__main__':unittest.main()
