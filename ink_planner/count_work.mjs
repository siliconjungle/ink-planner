// Deterministic target-work counts for instrumented JavaScript artifacts.
// Usage: node count_work.mjs REQUEST.json
// REQUEST: {artifacts:[{measurement,path}], calls:[{function,args}]}
// Each artifact exports __work() (iterations since the previous read). No
// timing is taken; counts depend only on the emitted code and the inputs.
import fs from 'node:fs';
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {artifacts, calls} = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const rows = [], rejected = [];
let expected = null;
for (const a of artifacts) {
  try {
    const mod = await import(pathToFileURL(a.path));
    if (typeof mod.call !== 'function' || typeof mod.__work !== 'function') throw Error('artifact has no instrumented pure-call entries');
    const counts = [], values = [];
    for (const c of calls) {
      const args = structuredClone(c.args);
      mod.__work();
      values.push(mod.call(c.function, args));
      counts.push(String(mod.__work()));
      assert.deepStrictEqual(args, c.args, 'candidate mutated its input');
    }
    if (expected === null) expected = values;
    else assert.deepStrictEqual(values, expected, 'candidate output differs from the baseline');
    rows.push({measurement: a.measurement, iterations: counts});
  } catch (e) { rejected.push({measurement: a.measurement, reason: String(e).slice(0, 500)}); }
}
console.log(JSON.stringify({schema: 1, node: process.version, rows, rejected}));
