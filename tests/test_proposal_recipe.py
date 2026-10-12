import tempfile,unittest
from pathlib import Path
from ink_planner.automatic_candidates import recipe_revision
class Recipe(unittest.TestCase):
 def test_nested_proof_data_and_relative_names_invalidate_recipe(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);compiler=root/'compiler';compiler.write_bytes(b'compiler');producer=root/'producer';producer.mkdir();(producer/'generate.py').write_bytes(b'code');data=producer/'data/key-order';data.mkdir(parents=True);proof=data/'a.json';proof.write_bytes(b'first')
   revision=lambda:recipe_revision(compiler,producer,30,300)
   initial=revision();proof.write_bytes(b'second');changed=revision();self.assertNotEqual(initial,changed)
   proof.rename(data/'b.json');self.assertNotEqual(changed,revision())
   unchanged=revision();(producer/'__pycache__').mkdir();(producer/'__pycache__/cached.pyc').write_bytes(b'ignored');self.assertEqual(unchanged,revision())
   self.assertNotEqual(unchanged,recipe_revision(compiler,producer,30,301))
if __name__=='__main__':unittest.main()
