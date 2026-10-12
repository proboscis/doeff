// probe.js <窓の秒> <窓の数> <名> — service に似た負荷(2 ms 眠り → 約 0.5 ms の計算)を走らせ、main thread の run queue の待ちを
// /proc の schedstat で窓ごとに出す(読むだけ・card ki-757b489f8a14)。窓は壁の時計の <窓の秒> の倍数に揃える(別の cgroup の probe と並べるため)。
const fs = require('fs');
const win = +process.argv[2] || 30, rounds = +process.argv[3] || 10, name = process.argv[4] || 'probe';
const ss = () => fs.readFileSync(`/proc/self/task/${process.pid}/schedstat`, 'utf8').trim().split(' ').map(Number);
const prio = () => { const s = fs.readFileSync('/proc/self/stat', 'utf8'); const f = s.slice(s.lastIndexOf(')') + 2).split(' '); return `nice=${f[16]} policy=${f[38]}`; };
let N = 20000; // 約 0.5 ms の計算に合わせる
(function calibrate() { const t = process.hrtime.bigint(); let x = 0; for (let i = 0; i < 1e6; i++) x += i % 7; const ns = Number(process.hrtime.bigint() - t); N = Math.max(1000, Math.round(1e6 * 500000 / ns)); })();
const work = () => { let x = 0; for (let i = 0; i < N; i++) x += i % 7; return x; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  console.log(`${name} start ${new Date().toISOString()} ${prio()} N=${N}`);
  for (let r = 0; r < rounds; r++) {
    await sleep(win * 1000 - (Date.now() % (win * 1000)) + 20);
    const t0 = Date.now(), a = ss();
    while (Date.now() - t0 < (win - 2) * 1000) { await sleep(2); work(); }
    const b = ss(), load = fs.readFileSync('/proc/loadavg', 'utf8').split(' ')[0];
    const run = (b[0] - a[0]) / 1e9, wait = (b[1] - a[1]) / 1e9, n = b[2] - a[2];
    console.log(`${name} ${new Date(t0).toISOString()} load ${load} cpu ${run.toFixed(2)}s wait ${wait.toFixed(2)}s ratio ${(100 * wait / (run + wait)).toFixed(0)}% per-switch ${(1e6 * wait / Math.max(n, 1)).toFixed(0)}us`);
  }
})();
