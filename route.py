#!/usr/bin/env python3
from pathlib import Path
import os, sys
# The dependency is a real Python package; installed callers import it directly.
# Source distributions supply their explicitly pinned sibling checkout.
root=os.environ.get("INK_KNOWLEDGE_ROOT")
if root:sys.path.insert(0,root)
elif (Path(__file__).resolve().parent.parent/"knowledge/ink_knowledge").is_dir():
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent/"knowledge"))
_site=Path(__file__).resolve().parent.parent/'python-site'
sys.path.insert(0,str(_site))
os.environ['PYTHONPATH']=os.pathsep.join([str(_site),os.environ.get('PYTHONPATH','')])
from ink_planner.route_selection import main
if __name__=="__main__":main()
