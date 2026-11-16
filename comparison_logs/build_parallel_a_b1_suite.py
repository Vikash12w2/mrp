"""Build fresh parallel A/B1 reruns, excluding T14 and adding anchor follow-ups."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT/'comparison_logs/version_a_vs_b1/manifest.json'
FOLLOW=ROOT/'comparison_logs/version_b1_and_anchor_followup/manifest.json'
OUT=ROOT/'comparison_logs/version_a_b1_parallel'

def main():
    old=json.loads(OLD.read_text()); fm=json.loads(FOLLOW.read_text())
    cases=[c for c in old['cases'] if int(c['T'])!=14]
    jobs=[]; cid=1
    for c in cases:
        for version in ('version_a','version_b1'):
            jobs.append(dict(id=len(jobs)+1,case_id=f'base-{cid}',dataset=c['dataset'],workers=c['workers'],rechargers=c['rechargers'],T=c['T'],grid_side=c['grid_side'],tiles=c.get('tiles',1),anchors=c['anchors'],input=c.get('input',f"examples/{c['dataset']}"),version=version,planning_radius=6 if version=='version_a' else None,parallel=True,suite_kind='previous_a_b1_case'))
        cid+=1
    # Follow-up pairs use the new anchors; include only A and its paired B1, not duplicate B1 baseline jobs.
    for c in fm['followup_cases']:
        for version in ('version_a','version_b1'):
            jobs.append(dict(id=len(jobs)+1,case_id=f"follow-{c['source_job']}",dataset=c['dataset'],workers=c['workers'],rechargers=c['rechargers'],T=c['T'],grid_side=c['grid_side'],tiles=c['tiles'],anchors=c['anchors'],input=f"examples/{c['dataset']}",version=version,planning_radius=c['planning_radius'] if version=='version_a' else None,baseline_job_id=c['source_job'],baseline_coverage=c['baseline_coverage'],parallel=True,suite_kind='new_anchor_followup'))
    # Balance matched pairs using old observed wall time; keep each pair in one batch.
    batches=[[] for _ in range(6)]; loads=[0.]*6
    grouped={}
    for j in jobs:grouped.setdefault(j['case_id'],[]).append(j)
    for key,pair in sorted(grouped.items(),key=lambda kv:-max((float(c.get('baseline_wall_s',60)) for c in kv[1]),default=60)):
        batch=min(range(6),key=lambda b:loads[b]);batches[batch].extend(pair)
        loads[batch]+=sum(float(c.get('baseline_wall_s',60)) for c in pair)
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'manifest.json').write_text(json.dumps(dict(source_suite='version_a_vs_b1',followup_suite='version_b1_and_anchor_followup',T14_excluded=True,parallel=True,base_cases=len(cases),anchor_followup_cases=len(fm['followup_cases']),jobs=jobs,batch_count=6,estimated_batch_load_seconds=loads),indent=2))
    for i,b in enumerate(batches,1):
        d=OUT/f'batch{i:02d}';d.mkdir(exist_ok=True);(d/'jobs.json').write_text(json.dumps(b,indent=2))
    print(f'{len(cases)} base workloads -> {len(cases)*2} jobs; {len(fm["followup_cases"])} anchor cases -> {2*len(fm["followup_cases"])} jobs; total {len(jobs)}')
    print('Batch job counts:',[len(b) for b in batches])
    print('Follow-up cases:',[(c['source_job'],c['planning_radius'],c['anchors']) for c in fm['followup_cases']])
if __name__=='__main__':main()
