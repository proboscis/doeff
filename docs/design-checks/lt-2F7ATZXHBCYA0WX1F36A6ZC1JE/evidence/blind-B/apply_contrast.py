import pathlib, sys
p = pathlib.Path(sys.argv[1]); s = p.read_text()
old = '''  (| (dfor #(k v) (.items base) :if (or (in k CHILD-ENV-ALLOWED) (any (gfor p CHILD-ENV-PREFIXES (.startswith k p)))) k v)
     extra declared worker))'''
new = '''  (| (dfor #(k v) (.items base) :if (or (in k CHILD-ENV-ALLOWED) (any (gfor p CHILD-ENV-PREFIXES (.startswith k p)))
                                        (is (EnvVar.environ-refusal {k v}) None)) k v)
     extra declared worker))'''
assert s.count(old) == 1
s = s.replace(old, new).replace('(import doeff_cluster.shared.intent.job_model [JobSpec])\n',
  '(import doeff_cluster.shared.intent.job_model [JobSpec])\n(import doeff_cluster.shared.intent.runtime_env_model [EnvVar])\n')
p.write_text(s)
