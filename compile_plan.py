#!/usr/bin/env python3
import os,sys
from pathlib import Path
root=Path(__file__).resolve().parent
sys.path[:0]=[str(root),str(root.parent/'knowledge'),str(root.parent/'python-site')]
os.environ['PYTHONPATH']=os.pathsep.join([*sys.path[:3],os.environ.get('PYTHONPATH','')])
from ink_planner.compiled_planning import main
if __name__ == "__main__": main()
