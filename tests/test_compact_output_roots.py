import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ink_planner.semantic_search import selection_roots
class CompactOutputTests(unittest.TestCase):
 def test_nested_premise_use_and_duplicates(self):
  applications=[dict(law='outer',premises=[{'Congruence':[{'Use':dict(law='inner',premises=[{'Use':dict(law='deep')}])},'Normalize']}]),dict(law='inner')]
  self.assertEqual(selection_roots(applications),['deep','inner','outer'])
 def test_empty_selection_requires_no_knowledge_roots(self):self.assertEqual(selection_roots([]),[])
if __name__=='__main__':unittest.main()
