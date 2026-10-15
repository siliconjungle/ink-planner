"""Build a bounded offline adapter against emitted libraries' existing JSON ABI."""
import fcntl, hashlib, json, os, shutil, subprocess, time
from pathlib import Path

RUNNER=Path(__file__).with_name('measure_calls.rs.txt')

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
    directory=planner.directory/'native-measurements';(directory/'src').mkdir(parents=True,exist_ok=True)
    dependencies=['serde_json={version="=1.0.151",features=["float_roundtrip"]}'];lanes=[]
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
    request=directory/'request.json';request.write_text(json.dumps(dict(artifacts=artifacts,phases=phases,measurement=controls),allow_nan=False))
    executable=Path(environment['CARGO_TARGET_DIR'])/'release/ink-offline-calls'
    result=subprocess.run([str(executable),str(request)],capture_output=True,text=True,timeout=remaining())
    if result.returncode:raise ValueError('native offline measurement failed: '+result.stderr[-2000:])
    return json.loads(result.stdout),dict(build_seconds=build_seconds,lockfile_sha256=hashlib.sha256((directory/'Cargo.lock').read_bytes()).hexdigest(),
                                        executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
                                        scope='Exact dependency versions, offline build; existing generated JSON call and portable snapshot ABIs; no new host capability')
