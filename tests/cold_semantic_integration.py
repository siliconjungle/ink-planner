"""A fresh semantic CLI must create its disposable index before discovery."""
import json,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'knowledge'),str(ROOT/'planner')]
from ink_knowledge import Store,semantic_entry
from ink_planner.semantic_search import main

def check(compiler):
 with tempfile.TemporaryDirectory(prefix='ink-cold-semantic-')as temporary:
  d=Path(temporary);s={'Value':'U32'};x=dict(sort=s,node={'Free':'x'});law=dict(schema=1,semantics='ink-conditional-laws-v1',params={'x':s},conditions=[],dependencies=[],**{'from':dict(sort=s,node={'Op':['binary:+',[x,dict(sort=s,node={'Word':0})]]}),'to':x},proof='Normalize')
  store=Store.publish(d/'knowledge',[semantic_entry(law)]);assert not (store.root/'index.sqlite').exists()
  source=d/'source.ink';source.write_text('module cold;fn run(x:u32)->u32{return x+0;}');core=d/'core.json'
  subprocess.run([str(compiler),'emit-core',str(source),'-o',str(core)],check=True,capture_output=True)
  original=list(sys.argv);sys.argv=['ink-plan','--compiler',str(compiler),'--core',str(core),'--snapshot',str(store.data/'snapshot.json'),'--budget','8','--time-seconds','10','-o',str(d/'selection.json')]
  try:main()
  finally:sys.argv=original
  assert (store.root/'index.sqlite').exists();selection=json.loads((d/'selection.json').read_bytes());assert selection['applications']
  subprocess.run([str(compiler),'check-selection',str(core),str(d/'selection.json')],check=True,capture_output=True)
  print('cold semantic discovery initialised index and replayed the selected proof')
if __name__=='__main__':check(Path(sys.argv[1]).resolve())
