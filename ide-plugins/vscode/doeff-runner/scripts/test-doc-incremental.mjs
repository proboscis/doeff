// 実際の Rust CLI と差分調停を使う。外部 HTTP だけを localhost に置く。
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { RustWorkspaceRunner } from '../out/lint/docWorkspaceProcess.js';
import { WorkspaceJudge } from '../out/lint/docWorkspace.js';
import { LintStore } from '../out/lint/store.js';
async function main() {
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'doc-incremental-')));
  execFileSync('git', ['init', '-q', root]);
  const cache = await fs.mkdtemp(path.join(os.tmpdir(), 'doc-incremental-cache-'));
  const binary = process.env.DOC_TEST_BINARY;
  assert(binary);
  let calls = 0;
  const server = http.createServer((req, res) => {
    req.resume(); req.on('end', () => {
      calls++;
      res.end(JSON.stringify({model:'incremental-test', answers: Object.fromEntries(['DOC001','DOC002','DOC003','DOC004'].map(r=>[r,{type:'noul',noul:r==='DOC001'?0.9:0.1}])),usage:{input_tokens:1}}));
    });
  });
  await new Promise(r => server.listen(0,'127.0.0.1',r));
  Object.assign(process.env, {JEV_BASE_URL:`http://127.0.0.1:${server.address().port}`, JEV_MODEL:'incremental-test',JEV_WIRE:'direct',JEV_API_KEY:'local',DOC_LINTER_CACHE_DIR:cache});
  const runner = new RustWorkspaceRunner(() => binary);
  const store = new LintStore();
  const judge = new WorkspaceJudge(runner, store);
  const events=[];
  const originalRun=runner.run.bind(runner);
  runner.run=(request, observe, signal)=>originalRun(request, e=>{events.push(e);observe(e)},signal);
  const run = async (request) => {
    events.length=0;
    const done=new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{off();reject(new Error('検査が完了しない'))},20000);
      const off=store.onDidChangeDocumentWorkspace(()=>{
        const p=store.docWorkspaces().get(root)?.progress;
        if (p?.phase==='failed') {clearTimeout(timer);off();reject(new Error(p.reason));}
        if (p?.phase==='complete'&&!p.pendingChanges) {clearTimeout(timer);off();resolve();}
      });
    });
    judge.submit(request);await done;
  };
  const file=path.join(root,'file-0.md');
  try {
    await Promise.all(Array.from({length:1000},(_,i)=>fs.writeFile(path.join(root,`file-${i}.md`),`文章${i}の用途を説明する。`)));
    const start=Date.now();await run({kind:'initial',root,documents:[]});
    assert.equal(calls,1000);assert.equal(store.violations().length,1000);
    const initialMs=Date.now()-start;
    const untouched=store.violationsIn(path.join(root,'file-999.md'))[0];
    const mark=calls;const changeStart=Date.now();
    await run({kind:'changed',root,paths:[file],documents:[{path:file,text:'編集した本文。'}]});
    assert.equal(calls-mark,1);assert.equal(events.find(e=>e.event==='index').snapshot.files.size,1);
    assert.equal(store.violations().length,1000);assert.equal(store.violationsIn(path.join(root,'file-999.md'))[0],untouched);
    const changedMs=Date.now()-changeStart;
    const mark2=calls;await run({kind:'changed',root,paths:[file],documents:[{path:file,text:'編集した本文。'}]});
    assert.equal(calls,mark2);assert.equal(events.filter(e=>e.event==='index').length,0);
    await fs.writeFile(path.join(root,'.gitignore'),'ignored.md\n');
    const ignored=path.join(root,'ignored.md');await fs.writeFile(ignored,'除外対象。');
    await run({kind:'changed',root,paths:[ignored],documents:[]});assert.equal(calls,mark2);
    const term=path.join(root,'terms.md'),consumer=path.join(root,'consumer.md');
    await run({kind:'changed',root,paths:[term,consumer],documents:[{path:term,text:'## 用語 {#term:env}\n\n元の説明。'},{path:consumer,text:'[用語](term:env)を利用する。'}]});
    const mark3=calls;
    await run({kind:'changed',root,paths:[term],documents:[{path:term,text:'## 用語 {#term:env}\n\n更新した説明。'}]});
    assert.equal(calls-mark3,2);assert.equal(events.find(e=>e.event==='index').snapshot.files.size,2);
    await fs.unlink(file);await run({kind:'changed',root,paths:[file],documents:[]});assert.equal(store.violationsIn(file).length,0);
    console.log(JSON.stringify({initialFiles:1000,initialMs,changedFiles:1,changedMs,repeatRequests:0,unrelatedResultsRetained:true,termAffectedFiles:2,root}));
  } finally {judge.dispose();server.closeAllConnections();await new Promise(r=>server.close(r));}
}
main().catch(e=>{console.error(e);process.exitCode=1});
