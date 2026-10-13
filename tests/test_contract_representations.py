"""Both representation domains replay against the already checked core image."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from ink_planner.implementation_selection import check_package,emission_args

class ContractRepresentations(unittest.TestCase):
 def test_v1_and_v2_bind_exact_core_including_contract_evidence(self):
  with tempfile.TemporaryDirectory()as temporary:
   staged=Path(temporary)/'request.json'
   for semantics in ('ink-source-representation-v1','ink-source-representation-v2'):
    request=dict(semantics=semantics,representation='1'*64,input_core_sha256='2'*64)
    receipt=dict(status='checked',representation='1'*64,source='2'*64)
    with patch('ink_planner.implementation_selection.invoke',return_value=json.dumps(receipt))as invoke:
     check_package('compiler','original.ink','checked.core.json',request,staged,30)
     self.assertEqual(invoke.call_args.args[1],['check-representation','checked.core.json',staged,'--core'])
    with patch('ink_planner.implementation_selection.invoke',return_value=json.dumps({**receipt,'source':'3'*64})):
     with self.assertRaises(ValueError):check_package('compiler','original.ink','checked.core.json',request,staged,30)
    self.assertEqual(emission_args(semantics,'source','request','javascript','output')[:1],['emit-representation'])

if __name__=='__main__':unittest.main()
