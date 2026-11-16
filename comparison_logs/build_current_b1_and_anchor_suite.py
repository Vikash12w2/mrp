"""Build B1 baseline and anchor-adjusted A/B1 suites, excluding requested T14 jobs."""
import json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent))
from mrp.model import load_instance
from mrp.benchmark import tiled
OLD=ROOT/'mrp_backup/study-20261114-180038'
CUR=ROOT/'mrp_backup/version_a_vs_b1'
OUT=ROOT/'comparison_logs/version_b1_and_anchor_followup'

def eligible(inst, w, a, radius):
    return all(max(abs(c[0]-a[0]),abs(c[1]-a[1]))<=radius for c in w.loop)

def choose(inst, radius, n_rech):
    workers=inst.workers; free=[(x,y) for x in range(inst.xmax+1) for y in range(inst.ymax+1) if inst.free((x,y))]
    sets=[{i for i,w in enumerate(workers) if eligible(inst,w,a,radius)} for a in free]
    # Backtracking set cover; candidate anchors are ranked by how many uncovered loops they contain.
    best=None
    # symmetric anchor combinations can be pruned by allowing all indices, with memoization
    seen=set()
    def search(chosen, covered):
        nonlocal best
        if len(covered)==len(workers):
            if len(chosen)<=n_rech: best=list(chosen); return True
            return False
        if len(chosen)>=n_rech:return False
        key=(tuple(sorted(chosen)),tuple(sorted(covered)))
        if key in seen:return False
        seen.add(key); target=min(set(range(len(workers)))-covered)
        opts=[j for j,s in enumerate(sets) if target in s and j not in chosen]
        opts.sort(key=lambda j:(-len(sets[j]-covered),free[j]))
        for j in opts:
            if search(chosen+[j],covered|sets[j]):return True
        return False
    if not search([],set()):return None
    chosen=[free[j] for j in best]
    # Retain the configured robot count even when fewer than R robots suffice
    # geometrically. Extra rechargers park on distinct free cells.
    for cell in free:
        if len(chosen) >= n_rech:
            break
        if cell not in chosen:
            chosen.append(cell)
    if len(chosen) != n_rech:
        raise RuntimeError(f'Could not place all configured rechargers: got {len(chosen)}, need {n_rech}')
    return chosen

def main():
    # Unique prior decentralized configurations, excluding every T14 case.
    cases={}
    for p in OLD.glob('[0-9][0-9][0-9][0-9].result.json'):
        r=json.loads(p.read_text()); ds=r['input'].replace('\\','/').split('/')[-1]
        if r.get('approach')!='decentralized' or r.get('T')==14 or ds not in {'input-artfloor','input-warehouse','input-random-20','input-random-30'}:continue
        key=(ds,int(r['workers']),int(r['rechargers']),int(r['T']),int(r['grid'].split('x')[0]),int(r.get('tiles',1)))
        if key not in cases or (r.get('variant')=='base'):cases[key]=r
    b1_jobs=[]; jid=1
    for key,r in sorted(cases.items()):
        b1_jobs.append(dict(id=jid,case_id=f'base-{jid}',dataset=key[0],workers=key[1],rechargers=key[2],T=key[3],grid_side=key[4],tiles=key[5],anchors={str(k):v for k,v in r['anchors'].items()},input=f'examples/{key[0]}',version='version_b1',suite_kind='previous_case_b1'))
        jid+=1
    # Prior current A incomplete coverage cases, excluding T14, plus one-run B1 matches at same new anchors.
    failures=[]
    for p in CUR.rglob('*.result.json'):
        r=json.loads(p.read_text())
        if r.get('version')!='version_a' or int(r.get('T',0))==14 or float(r.get('coverage') or 100)>=100:continue
        # Recover case definition/anchors from original suite manifest.
        man=json.loads((ROOT/'comparison_logs/version_a_vs_b1/manifest.json').read_text())
        job=next(j for j in man['jobs'] if j['id']==int(r['job_id']))
        case=dict(dataset=job['dataset'],workers=job['workers'],rechargers=job['rechargers'],T=job['T'],grid_side=job['grid_side'],tiles=job.get('tiles',1))
        src=ROOT/'examples'/case['dataset']; avail=len(list(src.glob('rob*_traj.txt')))
        inst=tiled(load_instance(str(src),min(avail,case['workers']),case['rechargers'],case['T']),case['tiles'],case['workers'])
        inst=__import__('dataclasses').replace(inst,xmax=case['grid_side']-1,ymax=case['grid_side']-1)
        anchors=choose(inst,6,case['rechargers']); radius=6
        if anchors is None: anchors=choose(inst,8,case['rechargers']); radius=8
        if anchors is None: raise RuntimeError(f'No covering placement for {case}')
        failures.append(dict(source_job=int(r['job_id']),baseline_coverage=r['coverage'],**case,anchors={str(i+1):list(a) for i,a in enumerate(anchors)},planning_radius=radius))
    follow=[]
    for c in failures:
        for version in ('version_a','version_b1'):
            follow.append(dict(id=jid,case_id=f"follow-{c['source_job']}",dataset=c['dataset'],workers=c['workers'],rechargers=c['rechargers'],T=c['T'],grid_side=c['grid_side'],tiles=c['tiles'],anchors=c['anchors'],input=f"examples/{c['dataset']}",version=version,planning_radius=c['planning_radius'],baseline_job_id=c['source_job'],baseline_coverage=c['baseline_coverage'],suite_kind='new_anchor_followup'))
            jid+=1
    # distribute each case pair intact using balanced historical A runtimes, six batches
    alljobs=b1_jobs+follow; batches=[[] for _ in range(6)]; loads=[0.]*6
    grouped={}
    for j in alljobs:grouped.setdefault(j['case_id'],[]).append(j)
    for cid, pair in sorted(grouped.items(),key=lambda kv:-max((float(j.get('baseline_wall_s',60)) for j in kv[1]),default=60)):
        k=min(range(6),key=lambda z:loads[z]);batches[k].extend(pair);loads[k]+=sum(60 for _ in pair)
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'manifest.json').write_text(json.dumps(dict(T14_excluded=True,b1_baseline_cases=len(b1_jobs),anchor_followup_cases=len(failures),jobs=alljobs,followup_cases=failures,batch_count=6),indent=2))
    for i,b in enumerate(batches,1):d=OUT/f'batch{i:02d}';d.mkdir(exist_ok=True);(d/'jobs.json').write_text(json.dumps(b,indent=2))
    print('B1 previous non-T14 cases:',len(b1_jobs));print('Anchor followup workloads:',len(failures),'paired jobs:',len(follow));print('Total jobs:',len(alljobs));print('Batches:',[len(x) for x in batches]);print('Followup radii:',{r:sum(c['planning_radius']==r for c in failures) for r in (6,8)})
    for c in failures:print(c['source_job'],c['dataset'],f"W{c['workers']}/R{c['rechargers']} T{c['T']}",c['planning_radius'],c['anchors'])
if __name__=='__main__':main()
