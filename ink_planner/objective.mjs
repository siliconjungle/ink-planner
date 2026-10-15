import fs from 'node:fs/promises';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
const [host,module,requestPath,configurationPath,previousPath,output]=process.argv.slice(2);
async function input(path){
 const bytes=await fs.readFile(path);
 if(bytes.length>4_000_000)throw Error('objective input byte budget');
 return {value:JSON.parse(bytes),sha256:createHash('sha256').update(bytes).digest('hex')};
}
const [request,configuration,previous]=await Promise.all([
 input(requestPath),input(configurationPath),previousPath==='-'?null:input(previousPath)]);
const {prepareInkObjective,objectiveDecision}=await import(pathToFileURL(join(host,'objective.mjs')));
const compiled=await import(pathToFileURL(module));
const token=await prepareInkObjective(compiled,configuration.value);
const decision=await objectiveDecision(token,request.value,previous?.value??null);
const receipt={schema:1,kind:'ink-authored-objective-decision-v1',decision,
 inputs:{adaptive_sha256:request.sha256,configuration_sha256:configuration.sha256,
   previous_goal_sha256:previous?.sha256??null,
   module_sha256:createHash('sha256').update(await fs.readFile(module)).digest('hex')},
 scope:'Untrusted offline forecast ranking; no application execution, proof admission, performance guarantee or migration commit.'};
await fs.writeFile(output,JSON.stringify(receipt,null,2)+'\n');
