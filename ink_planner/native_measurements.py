"""Build a bounded offline adapter against emitted libraries' existing JSON ABI."""
import fcntl, hashlib, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path

RUNNER=Path(__file__).with_name('measure_calls.rs.txt')

def run_observed(command,*,timeout):
    """Reap this exact child with its OS peak, without a global child high-water.

    The peak covers the complete host launch, all variants, input/output and
    adapter storage. It is a conservative common bound, not per-plan allocation.
    Unknown OS units decline rather than supplying missing or guessed evidence.
    File-backed output avoids a full pipe blocking the child before wait4.
    """
    if not hasattr(os,'wait4')or sys.platform not in ('darwin','linux'):
        raise ValueError('native peak RSS acquisition requires Darwin or Linux wait4')
    if timeout<=0:raise subprocess.TimeoutExpired(command,timeout)
    deadline=time.monotonic()+timeout
    with tempfile.TemporaryFile()as output,tempfile.TemporaryFile()as errors:
        child=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=output,stderr=errors)
        try:
            while True:
                pid,status,usage=os.wait4(child.pid,os.WNOHANG)
                if pid:
                    child.returncode=os.waitstatus_to_exitcode(status)
                    break
                remaining=deadline-time.monotonic()
                if remaining<=0:raise subprocess.TimeoutExpired(command,timeout)
                time.sleep(min(.02,remaining))
        finally:
            if child.returncode is None:
                try:child.kill()
                except ProcessLookupError:pass
                _,status,_=os.wait4(child.pid,0)
                child.returncode=os.waitstatus_to_exitcode(status)
        output.seek(0);errors.seek(0)
        peak=int(usage.ru_maxrss)*(1 if sys.platform=='darwin'else 1024)
        if peak<=0:raise ValueError('missing or invalid native whole-host peak RSS')
        return subprocess.CompletedProcess(command,child.returncode,output.read().decode(),errors.read().decode()),peak

def measure(planner,artifacts,phases,controls,deadline=None):
    deadline=deadline or time.monotonic()+planner.options.measure_timeout
    # Cargo's shared output executable is protected through execution, not just
    # compilation. A concurrent build cannot replace the measured program.
    with (planner.root/'offline-native.lock').open('a')as lock:
        while True:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.monotonic()>=deadline:raise ValueError('native measurement lock time budget')
                time.sleep(.05)
        return _measure(planner,artifacts,phases,controls,deadline)

def _measure(planner,artifacts,phases,controls,deadline):
    def remaining():
        seconds=deadline-time.monotonic()
        if seconds<=0:raise ValueError('offline measurement time budget')
        return seconds
    cargo=shutil.which('cargo')
    if not cargo:raise ValueError('native offline acquisition requires Cargo')
    directory=Path(tempfile.mkdtemp(prefix='native-measurements-',dir=planner.directory));(directory/'src').mkdir()
    dependencies=['serde_json={version="=1.0.151",features=["float_roundtrip"]}','sha2="=0.10.9"'];lanes=[]
    for i,artifact in enumerate(artifacts):
        name=f'variant_{i}'
        dependencies.append(f'{name}={{package={json.dumps(artifact["package"])},path={json.dumps(artifact["path"])}}}')
        lanes.append('''{
            let start=Instant::now();
            let state=Rc::new(RefCell::new(__NAME__::State::new()));
            let setup_ns=start.elapsed().as_nanos();
            let caller=state.clone();let observer=state.clone();
            lanes.push(Lane{measurement:artifacts[__INDEX__]["measurement"].as_str().unwrap().to_owned(),
                call:Box::new(move |function,args|caller.borrow_mut().call_json(function,args)),
                snapshot:Box::new(move ||observer.borrow().checkpoint()),setup_ns});
        }'''.replace('__NAME__',name).replace('__INDEX__',str(i)))
    (directory/'Cargo.toml').write_text('[package]\nname="ink-offline-calls"\nversion="0.1.0"\nedition="2021"\n[workspace]\n[dependencies]\n'+'\n'.join(dependencies)+'\n')
    (directory/'src/main.rs').write_text(RUNNER.read_text().replace('__LANES__','\n'.join(lanes)))
    environment=dict(os.environ,CARGO_BUILD_JOBS='2',CARGO_TARGET_DIR=str(planner.root/'offline-native-target'))
    started=time.monotonic()
    result=subprocess.run([cargo,'build','--release','--offline','--manifest-path',str(directory/'Cargo.toml')],env=environment,capture_output=True,text=True,timeout=remaining())
    if result.returncode:raise ValueError('offline adapter build failed: '+result.stderr[-3000:])
    build_seconds=str(time.monotonic()-started)
    request=directory/'request.json';prepared=dict(artifacts=artifacts,phases=phases,measurement=controls)
    executable=Path(environment['CARGO_TARGET_DIR'])/'release/ink-offline-calls'
    processes=controls['processes']if controls else getattr(planner.options,'measure_processes',3)
    if type(processes)is not int or not 1<=processes<=8:raise ValueError('measurement process count')
    reports=[]
    for process in range(processes):
        payload=dict(prepared,process_index=process)
        raw=json.dumps(payload,allow_nan=False).encode()
        request.write_bytes(raw)
        request_sha256=hashlib.sha256(raw).hexdigest()
        result,peak=run_observed([str(executable),str(request),request_sha256],timeout=remaining())
        if result.returncode:raise ValueError('native offline measurement failed: '+result.stderr[-2000:])
        report=json.loads(result.stdout)
        for row in report['rows']:row['peak_rss_bytes']=peak
        reports.append(report)
    from .process_measurements import combine
    return combine(reports,artifacts,fingerprints=True),dict(project_path=str(directory),build_seconds=build_seconds,lockfile_sha256=hashlib.sha256((directory/'Cargo.lock').read_bytes()).hexdigest(),
                                        executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
                                        scope='Exact dependency versions, offline build; existing generated JSON call and portable snapshot ABIs; OS wait4 peak RSS for each complete host launch, including all variants and adapter; not per-candidate heap')
