module generic_batching;
enum Error{Failed,}
record Row{amount:u64,label:String,}
state First:Table<u64,Row> = Table.empty();
state Second:Table<u64,u64> = Table.empty();
query total()->u64 reads(First,Second){return sum(First.values().filter(fn(row)=>row.label=="kept").map(fn(row)=>row.amount))+sum(Second.values());}
change erase(first:u64,second:u64)->Result<Unit,Error> writes(First,Second){First.remove(first);First.remove(second);Second.remove(first);Second.remove(second);return Ok(());}
