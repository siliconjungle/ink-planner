import json, shutil, subprocess, unittest
from pathlib import Path
from ink_planner.value_transfer import transfer

class ExactPackets(unittest.TestCase):
    def test_javascript_preserves_integer_limbs_negative_zero_and_own_keys(self):
        node=shutil.which('node')
        if not node:self.skipTest('Node required')
        value={'__proto__':[2**64-1,-2**100,0,-0.0], 'Int':'-123456789012345678901234567890', 'integer':['integer','0']}
        module=(Path(__file__).resolve().parents[1]/'ink_planner/value_transfer.mjs').as_uri()
        script="""import assert from 'node:assert/strict';import {unpackTransfer}from __MODULE__;
const v=unpackTransfer(__PACKET__);
assert.equal(Object.getPrototypeOf(v),Object.prototype);assert.equal(Object.hasOwn(v,'__proto__'),true);
assert.deepEqual(v.__proto__.slice(0,3),[18446744073709551615n,-(1n<<100n),0]);assert(Object.is(v.__proto__[3],-0));
assert.equal(v.Int,'-123456789012345678901234567890');assert.deepEqual(v.integer,['integer','0']);
for(const bad of [['integer','01'],['integer','-0'],['integer','1.0'],['null',0],['bool',1],['number','Infinity'],['object',[['x',['null']],['x',['null']]]]])assert.throws(()=>unpackTransfer(bad));
let deep=['null'];for(let i=0;i<130;i++)deep=['array',[deep]];assert.throws(()=>unpackTransfer(deep),/budget/);
""".replace('__MODULE__',json.dumps(module)).replace('__PACKET__',json.dumps(transfer(value)))
        result=subprocess.run([node,'--input-type=module','-e',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
