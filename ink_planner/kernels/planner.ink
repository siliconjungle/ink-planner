module PlannerKernels;
// Ranking is an untrusted hint. Wrapping integer operations have exact semantics.
fn score(rows:List<u32>,features:List<u32>,weights:List<u32>)->List<u32>{
 return rows.map(fn(row)=>repeat(8,0,fn(i)=>fn(total)=>total+features.at_or(row*8+i,0)*weights.at_or(i,0)));
}
// A bounded straight-line word VM used to reject synthesis candidates on fixtures.
// Padded instructions are add-zero. 0=add, 1=multiply, 2=subtract.
fn evaluate(inputs:List<u32>,instructions:List<u32>)->List<u32>{
 return inputs.map(fn(x)=>repeat(256,x,fn(i)=>fn(v)=>choose(instructions.at_or(i*2,0)==1,v*instructions.at_or(i*2+1,0),choose(instructions.at_or(i*2,0)==2,v-instructions.at_or(i*2+1,0),v+instructions.at_or(i*2+1,0)))));
}
