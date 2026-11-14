import sys,re,subprocess,os; sys.path.insert(0,'/mnt/user-data/outputs')
from mrp.model import load_instance
from mrp import twoshot as ts
import os; EX=os.environ['REPO_EXAMPLES']  # .../approach_two-shot/examples (run the repo once first so my_output/ exists)
inst=load_instance(EX+'/input-warehouse',n_workers=2,n_rech=1,T=14)
p=ts.phase1(inst,ts.Options(collisions=False))
T=14; V={}
for i in p['wt']:
    for t in range(1,T+1):
        V[f'wtraj_{i}_{t}']=p['wt'][i][t-1]; V[f'wx_{i}_{t}']=p['wx'][i][t-1]; V[f'wy_{i}_{t}']=p['wy'][i][t-1]; V[f'ch_{i}_{t}']=p['ch'][i][t-1]
    for t in range(1,T):
        wp=p['wp'][i][t-1]; V[f'wprim_{i}_{t}']=wp; V[f'rechassigned_{i}_{t}']=p['ra'][i][t-1]
        V[f'waitcount_{i}_{t}']=0 if wp==1 else 1; V[f'rechcount_{i}_{t}']=1 if wp==2 else 0
for t in range(1,T+1): V[f'rx_1_{t}']=p['rx'][1][t-1]; V[f'ry_1_{t}']=p['ry'][1][t-1]
for t in range(1,T): V[f'rprim_1_{t}']=p['rp'][1][t-1]
print("my phase 1 (2w/1r,T=14): wait",p['wait'],"| recharge steps:",[(i,t+1) for i in p['wp'] for t,x in enumerate(p['wp'][i]) if x==2])
# ---- B2: my phase-1 solution pinned into the REPO's constraints.smt2
src=[l for l in open(EX+'/constraints.smt2') if not re.match(r"\((minimize|check-sat|get-)",l.strip())]
pins=''.join(f"(assert (= {n} {v}))\n" for n,v in V.items())
open('/tmp/b2.smt2','w').write(''.join(src)+pins+"(check-sat)\n(get-value (total_wait))\n")
out=subprocess.run(['/usr/local/bin/z3','/tmp/b2.smt2'],capture_output=True,text=True,timeout=250).stdout.split('\n')
print("B2 my phase-1 solution under the repo's constraints.smt2:",out[0],out[1:2])
# ---- A(ii): repo's phase 2 on MY phase-1 solution
order=[n for n,_ in re.findall(r"\(\((\w+) (-?\d+)\)\)",open(EX+'/my_output/z3_output_2_14').read())]
tmpl=open(EX+'/my_output/z3_output_2_14').read()
TD=dict(re.findall(r"\(\((\w+) (-?\d+)\)\)",tmpl))
lines=["sat","(objectives",f" (total_wait {p['wait']})",")"]
for n in order:
    if n in V: lines.append(f"(({n} {V[n]}))")
    elif n=='total_rech': lines.append(f"((total_rech {sum(1 for i in p['wp'] for x in p['wp'][i] if x==2)}))")
    else: lines.append(f"(({n} {TD[n]}))")
open('/tmp/fake_phase1.txt','w').write("\n".join(lines)+"\n")
open('/tmp/bin/z3','w').write('#!/bin/bash\ncase "$*" in *constraints.smt2*) cat /tmp/fake_phase1.txt;; *) exec /usr/local/bin/z3 "$@";; esac\n'); os.chmod('/tmp/bin/z3',0o755)
env=dict(os.environ,PATH='/tmp/bin:'+os.environ['PATH'])
subprocess.run(['rm','-f']+[EX+'/my_output/'+f for f in os.listdir(EX+'/my_output')])
r=subprocess.run(['../src/tool_z3'],cwd=EX,env=env,capture_output=True,text=True,timeout=250).stdout
print("A(ii) repo's own phase 2 on MY phase-1 solution:",[l for l in r.split('\n') if 'EXTENDED' in l or 'hard EFF' in l or 'soft EFF' in l])
# ---- mine on the same p1
halts,ins=ts._halts_and_instances(inst,p); start=max(h[0] for h in halts.values())+max(h[2] for h in halts.values())
for M in range(start,start+12):
    if ts.phase2(inst,ts.Options(collisions=False),p,halts,ins,M): print("      my phase 2 on my phase-1 solution: T' =",M); break
os.remove('/tmp/bin/z3')
