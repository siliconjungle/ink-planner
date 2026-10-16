import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {browserHost} from '../ink_planner/browser_host.mjs';
for(const timeout_ms of [0,-1,1.5,3300001,NaN,Infinity])await assert.rejects(browserHost('/missing',[],{},()=>{}, {timeout_ms}),/bounded browser host/);
for(const executable of ['/usr/bin/false','/ink-missing-browser']){
 let error;try{await browserHost(executable,[],{},()=>{}, {timeout_ms:500});}catch(e){error=e;}
 assert(error);assert.equal(error.browser_cleanup.exited,true);assert.equal(error.browser_cleanup.profile_retained,false);await assert.rejects(fs.stat(error.browser_cleanup.profile),{code:'ENOENT'});
}
const browser=process.argv[2];if(browser){
 for(const timeout_ms of [1,2500]){
 let error;try{await browserHost(browser,[],{},h=>h.evaluate('new Promise(()=>{})'), {timeout_ms});}catch(e){error=e;}
 assert.match(error.message,/browser host time budget/);assert.equal(error.browser_cleanup.exited,true);assert.equal(error.browser_cleanup.profile_retained,false);await assert.rejects(fs.stat(error.browser_cleanup.profile),{code:'ENOENT'});
 }
 assert.equal(await browserHost(browser,[],{},h=>h.evaluate('2+3'),{timeout_ms:10000}),5);
}
console.log('PASS bounded whole-session host budget and missing/early-exit cleanup'+(browser?'; real Chrome startup/action cancellation, observed normal exit/profile retirement and successful call':''));
