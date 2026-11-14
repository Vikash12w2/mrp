import sys,re; sys.path.insert(0,'/mnt/user-data/outputs')
from mrp.model import load_instance
from mrp import twoshot as ts
import os; EX=os.environ['REPO_EXAMPLES']  # .../approach_two-shot/examples (run the repo once first so my_output/ exists)
txt=open(EX+'/my_output/z3_output_2_14').read()
repo_vals=[(n,int(v)) for n,v in re.findall(r"\(\((\w+) (-?\d+)\)\)",txt)]
rech_steps={n.replace('wprim','rechassigned') for n,v in repo_vals if n.startswith('wprim') and v==2}
fixed={n:v for n,v in repo_vals if not n.startswith('rechassigned') or n in rech_steps}
inst=load_instance(EX+'/input-warehouse',n_workers=2,n_rech=1,T=14); o=ts.Options(collisions=False)
p=ts.phase1(inst,o,fixed=fixed)
halts,ins=ts._halts_and_instances(inst,p)
print("A(i) halts (mine, from repo's phase-1):",halts,"| instances:",ins)
start=max(h[0] for h in halts.values())+max(h[2] for h in halts.values())
for M in range(start,start+10):
    out=ts.phase2(inst,o,p,halts,ins,M); print("  my phase 2 on the repo's phase-1 solution: M=%d ->"%M,"SAT" if out else "UNSAT",flush=True)
    if out: break
