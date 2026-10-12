"""Optional bounded loopback adapter to compiled Ink planner kernels.

Scores affect ordering only. Malformed output, timeout or unavailable service
retains local ranking. The endpoint runs the ordinary JS/Wasm/WebGPU host API.
"""
import json
import urllib.request
from urllib.parse import urlparse


class Scorer:
    def __init__(self, endpoint, weights=(1024,128,256,16,32,0,0,0), timeout=5):
        parsed=urlparse(endpoint)
        if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost','::1') or parsed.username or parsed.password:
            raise ValueError('self-host scorer must be a loopback HTTP endpoint')
        if len(weights)!=8 or any(type(x) is not int or not 0<=x<=65535 for x in weights) or not 0<timeout<=30:raise ValueError('self-host score contract')
        self.endpoint=endpoint;self.weights=tuple(weights);self.timeout=timeout;self.receipts=[]
    def score(self, features):
        if len(features)>65536:raise ValueError('self-host candidate budget')
        packed=[]
        for row in features:
            if len(row)>8:raise ValueError('self-host feature width')
            values=[min(65535,max(0,round(x*1024))) for x in row]+[0]*(8-len(row));packed.extend(values)
        # Host-private tuple and fresh captures avoid caller mutation/aliasing.
        request=urllib.request.Request(self.endpoint,data=json.dumps(dict(operation='score',features=packed,weights=self.weights)).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=self.timeout) as response:
            raw=response.read(4_000_001)
        if len(raw)>4_000_000:raise ValueError('self-host response budget')
        reply=json.loads(raw);values=reply['values']
        if not isinstance(values,list) or len(values)!=len(features) or any(type(x) is not int or not 0<=x<1<<32 for x in values):raise ValueError('self-host score output')
        # Exact comparison is deliberately retained during this opt-in rollout.
        # It checks the transport/kernel contract, not mathematical proof validity.
        expected=[sum(x*w for x,w in zip(packed[i:i+8],self.weights))&0xffffffff for i in range(0,len(packed),8)]
        if values!=expected:raise ValueError('self-host score disagreement')
        self.receipts.append({k:reply[k] for k in ('backend','counters') if k in reply})
        return values

    def evaluate(self, inputs, instructions):
        if len(inputs)>1048576 or len(instructions)!=512 or any(type(x) is not int or not 0<=x<1<<32 for x in [*inputs,*instructions]):raise ValueError('self-host word VM inputs')
        request=urllib.request.Request(self.endpoint,data=json.dumps(dict(operation='evaluate',inputs=inputs,instructions=instructions)).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=self.timeout) as response:raw=response.read(16_000_001)
        if len(raw)>16_000_000:raise ValueError('self-host response budget')
        reply=json.loads(raw);values=reply['values']
        if not isinstance(values,list) or len(values)!=len(inputs) or any(type(x) is not int or not 0<=x<1<<32 for x in values):raise ValueError('self-host word VM output')
        self.receipts.append({k:reply[k] for k in ('backend','counters') if k in reply});return values
