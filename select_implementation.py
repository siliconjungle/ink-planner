#!/usr/bin/env python3
from pathlib import Path
import os, sys
root = os.environ.get('INK_KNOWLEDGE_ROOT')
if root: sys.path.insert(0, root)
elif (Path(__file__).resolve().parent.parent/'knowledge/ink_knowledge').is_dir():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent/'knowledge'))
from ink_planner.implementation_selection import main
if __name__ == '__main__': main()
