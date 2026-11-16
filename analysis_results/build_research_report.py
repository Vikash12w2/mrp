"""Build a separate illustrated report from the existing three-version analysis."""
from pathlib import Path
import base64
import html
import json
import math
import re
import sys
from markdown_it import MarkdownIt

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Patch
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'analysis_results/three_versions'
OUT = ROOT / 'analysis_results/research_comparison'
NAMES = {'Centralized': 'Centralized SMT', 'A': 'Decentralized SMT', 'B1': 'Semi-decentralized SMT'}
COLORS = {'Centralized': '#505050', 'A': '#2678b2', 'B1': '#d67c18'}
METHODS = list(NAMES)


def table(frame):
    def fmt(v):
        if isinstance(v, float):
            return f'{v:.2f}' if math.isfinite(v) else '—'
        return str(v)
    return '| ' + ' | '.join(frame.columns) + ' |\n| ' + ' | '.join(['---'] * len(frame.columns)) + ' |\n' + '\n'.join('| ' + ' | '.join(fmt(v) for v in row) + ' |' for row in frame.itertuples(index=False, name=None))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    p = pd.read_csv(DATA / 'matched_base_cases.csv')
    p['number'] = p.case_id.str.split('-').str[-1].astype(int)
    p = p.sort_values('number')
    all_runs = pd.read_csv(DATA / 'all_runs.csv')
    common = pd.read_csv(DATA / 'common_success_cases.csv')
    summary = pd.read_csv(DATA / 'summary.csv')
    plt.rcParams.update({'font.family': 'serif', 'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False,
                         'axes.axisbelow': True, 'savefig.bbox': 'tight', 'pdf.fonttype': 42, 'ps.fonttype': 42})
    figs = []
    def save(fig, name):
        fig.savefig(OUT / f'{name}.png', dpi=220)
        fig.savefig(OUT / f'{name}.pdf')
        figs.append(fig)
    maps = list(p.dataset.unique())
    labels = lambda g: [f'{w}w;{r}r' + ('\n25×25' if grid == '25x25' else '') for w,r,grid in zip(g.workers,g.rechargers,g.grid)]
    fig, axes = plt.subplots(2,2,figsize=(12,7.5))
    for ax, dataset in zip(axes.flat,maps):
        g=p[p.dataset==dataset]; x=np.arange(len(g)); width=.24
        for j,m in enumerate(METHODS):
            ax.bar(x+(j-1)*width,g[f'{m}_wall_s'],width,color=COLORS[m],edgecolor='black',linewidth=.4,label=NAMES[m])
        ax.set(yscale='log',xticks=x,xticklabels=labels(g),title=dataset.replace('input-','').title(),ylabel='Pipeline wall time (s; log scale)')
        ax.grid(axis='y',alpha=.25)
    fig.legend(*axes.flat[0].get_legend_handles_labels(),loc='upper center',ncol=3,frameon=False)
    fig.tight_layout(rect=(0,0,1,.94)); save(fig,'fig01_computation_time')
    fig,axes=plt.subplots(1,2,figsize=(12,4.5))
    base=all_runs[all_runs.case_id.fillna('').str.startswith('base-')]
    # Centralized rows have no case_id; use the 22 matched rows for those bars.
    coverage=[100,base[base.method=='A'].coverage.mean(),base[base.method=='B1'].coverage.mean()]
    passed=[22,int(p.A_executable_pass.sum()),int(p.B1_executable_pass.sum())]
    axes[0].bar(range(3),coverage,color=list(COLORS.values()),edgecolor='black',linewidth=.5)
    axes[0].set(xticks=range(3),xticklabels=['Centralized\nSMT','Decentralized\nSMT','Semi-decentralized\nSMT'],ylim=(0,110),ylabel='Mean worker coverage (%)')
    for i,v in enumerate(coverage): axes[0].text(i,v+2,f'{v:.1f}%',ha='center')
    axes[1].bar(range(3),passed,color=list(COLORS.values()),edgecolor='black',linewidth=.5)
    axes[1].set(xticks=range(3),xticklabels=['Centralized\nSMT','Decentralized\nSMT','Semi-decentralized\nSMT'],ylim=(0,25),ylabel='Passes (out of 22)')
    for i,v in enumerate(passed): axes[1].text(i,v+.5,f'{v}/22',ha='center')
    fig.tight_layout(); save(fig,'fig02_coverage_acceptance')
    c=common.copy(); c['number']=c.case_id.str.split('-').str[-1].astype(int); c=c.sort_values('number')
    fig,ax=plt.subplots(figsize=(13,5)); x=np.arange(len(c)); width=.24
    for j,m in enumerate(METHODS):
        hard=c[f'{m}_hard_eff']; extra=c[f'{m}_soft_eff']-hard
        ax.bar(x+(j-1)*width,hard,width,color=COLORS[m],edgecolor='black',linewidth=.4)
        ax.bar(x+(j-1)*width,extra,width,bottom=hard,color=COLORS[m],alpha=.45,hatch='///',edgecolor='black',linewidth=.4)
    ax.set(xticks=x,xticklabels=[f'{r.dataset.replace("input-", "")}\n{r.workers}w;{r.rechargers}r;{r.grid}' for r in c.itertuples()],ylim=(0,100),ylabel='Worker efficiency (%)',title='Common planning successes only (13 matched scenarios)')
    ax.tick_params(axis='x',labelsize=8); ax.grid(axis='y',alpha=.25)
    handles=[Patch(facecolor=COLORS[m],label=NAMES[m]) for m in METHODS]+[Patch(facecolor='white',edgecolor='black',hatch='///',label='Recharge contribution (soft − hard)')]
    fig.legend(handles=handles,loc='upper center',ncol=4,frameon=False); fig.tight_layout(rect=(0,0,1,.9)); save(fig,'fig03_work_recharge_efficiency')
    fig,axes=plt.subplots(1,2,figsize=(12,4.6))
    for m in ['A','B1']:
        valid=p[f'{m}_executable_pass'].astype(bool)
        for good,marker in [(True,'o'),(False,'x')]:
            g=p[valid==good]
            if len(g): axes[0].scatter(g.Centralized_wall_s,g[f'{m}_wall_s'],marker=marker,color=COLORS[m],label=NAMES[m]+(' accepted' if good else ' rejected'))
        ratios=p[f'{m}_speedup_vs_central'].sort_values().to_numpy()
        axes[1].plot(np.arange(1,len(ratios)+1),ratios,marker='o',ms=3,color=COLORS[m],label=NAMES[m])
    lo=p[['Centralized_wall_s','A_wall_s','B1_wall_s']].min().min(); hi=p[['Centralized_wall_s','A_wall_s','B1_wall_s']].max().max()
    axes[0].plot([lo,hi],[lo,hi],'--',color='gray'); axes[0].set(xscale='log',yscale='log',xlabel='Centralized SMT wall time (s)',ylabel='Decomposed method wall time (s)',title='Matched runtime and acceptance'); axes[0].legend(fontsize=8)
    axes[1].axhline(1,color='gray',ls='--'); axes[1].set(yscale='log',xlabel='Rank within method (different scenario orders)',ylabel='Centralized / method wall time',title='Observed paired speed ratios'); axes[1].legend(fontsize=8)
    for ax in axes: ax.grid(alpha=.2)
    fig.tight_layout(); save(fig,'fig04_paired_runtime')
    follow=pd.read_csv(DATA/'anchor_followups.csv'); fig,axes=plt.subplots(1,2,figsize=(12,4.5)); cases=sorted(follow.case_id.unique()); x=np.arange(len(cases))
    for j,m in enumerate(['A','B1']):
        g=follow[follow.method==m].set_index('case_id').loc[cases]
        axes[0].bar(x+(j-.5)*.35,g.wall_s,.35,color=COLORS[m],label=NAMES[m])
        axes[1].bar(x+(j-.5)*.35,g.coverage,.35,color=COLORS[m])
        for k,(_,r) in enumerate(g.iterrows()):
            if not r.executable_pass: axes[1].text(k+(j-.5)*.35,r.coverage+2,'×',ha='center',color='red',fontsize=15)
    for ax in axes: ax.set(xticks=x,xticklabels=cases); ax.grid(axis='y',alpha=.2)
    axes[0].set(yscale='log',ylabel='Wall time (s; log scale)',title='Additional anchor layouts'); axes[0].legend(fontsize=8)
    axes[1].set(ylim=(0,112),ylabel='Worker coverage (%)',title='× marks failure of the executable screen')
    fig.tight_layout(); save(fig,'fig05_anchor_followups')
    fig,ax=plt.subplots(figsize=(11,5))
    for j,m in enumerate(['A','B1']):
        g=follow[follow.method==m].set_index('case_id').loc[cases]
        positions=x+(j-.5)*.35
        ax.bar(positions,g.hard_eff,.35,color=COLORS[m],edgecolor='black',linewidth=.5)
        ax.bar(positions,g.soft_eff-g.hard_eff,.35,bottom=g.hard_eff,
               color=COLORS[m],alpha=.45,hatch='///',edgecolor='black',linewidth=.5)
        for k,(_,r) in enumerate(g.iterrows()):
            if not r.executable_pass:
                ax.text(positions[k],r.soft_eff+2,'×',ha='center',color='red',fontsize=17)
    handles=[Patch(facecolor=COLORS[m],label=NAMES[m]) for m in ['A','B1']]
    handles.append(Patch(facecolor='white',edgecolor='black',hatch='///',label='Recharge contribution (soft − hard)'))
    ax.legend(handles=handles,loc='upper center',bbox_to_anchor=(.5,1.2),ncol=3,frameon=False,fontsize=9)
    ax.set(xticks=x,xticklabels=cases,ylim=(0,110),ylabel='Worker efficiency (%)',
           title='Six additional anchor layouts: hard and soft efficiency')
    ax.grid(axis='y',alpha=.2)
    fig.tight_layout(); save(fig,'fig06_anchor_efficiency')
    with PdfPages(OUT/'all_figures.pdf') as pdf:
        for fig in figs: pdf.savefig(fig,bbox_inches='tight'); plt.close(fig)
    s=summary[summary.scope=='Base suite'].copy(); s['method']=s.method.map(NAMES)
    s=s.rename(columns={'method':'Method','runs':'Cases','plan_pass':'Planning passes','executable_pass':'Passes','mean_coverage':'Mean coverage (%)','median_wall_s':'Median time (s)'})
    q=pd.read_csv(DATA/'common_success_summary.csv'); q['method']=q.method.map(NAMES)
    q=q.rename(columns={'method':'Method','common_cases':'Common cases','mean_hard_eff':'Hard efficiency (%)','mean_soft_eff':'Soft efficiency (%)','median_wall_s':'Median time (s)','mean_primitive_cost':'Cycle primitive cost'})
    per=p[['case_id','dataset','grid','workers','rechargers','Centralized_wall_s','A_wall_s','B1_wall_s','A_coverage','A_executable_pass','B1_executable_pass']].copy()
    per.columns=['Case','Map','Grid','Workers','Rechargers','Centralized time (s)','Decentralized time (s)','Semi-decentralized time (s)','Decentralized coverage (%)','Decentralized accepted','Semi-decentralized accepted']
    per.to_csv(OUT/'scenario_comparison.csv',index=False)
    report=f'''# Comparative study of centralized, decentralized and semi-decentralized SMT planning

## Abstract

This study compares three implementations of mobile recharger planning using 22 matched base scenarios and six additional anchor layouts. Semi-decentralized SMT achieves full worker coverage and passes the executable screen in all 28 tested layouts. Decentralized SMT passes in 18/28 layouts, including 13/22 base scenarios. On the base suite, median pipeline times are 84.10 s for centralized SMT, 30.30 s for decentralized SMT and 41.37 s for semi-decentralized SMT. On the 13 common planning successes, mean hard efficiency is 22.63%, 30.34% and 36.47%, respectively. These observations demonstrate a coverage–computation tradeoff within the tested suite; recorded notebook timings are not controlled hardware benchmarks.

## 1. Problem and methods

Worker robots repeatedly traverse prescribed loops and require mobile rechargers to replenish their batteries. Planning determines worker movement/wait/recharge decisions and recharger trajectories subject to the encoded energy, movement and collision constraints. SMT means satisfiability modulo theories; these implementations use Z3 to solve the encoded constraints.

| Name used in this report | Repository name | Assignment and planning |
| --- | --- | --- |
| Centralized SMT | centralized.py | One two-shot SMT planning problem contains all requested workers and rechargers. There is no decomposition into independently assigned recharger groups. This is the project's Python/Z3 port of the centralized approach. |
| Decentralized SMT | Version A | Rechargers negotiate worker assignments locally, then solve local SMT problems in radius-based planning regions. Overlapping regions are ordered. After alignment failures, a group can drop neighbor reservations and produce a fallback candidate. |
| Semi-decentralized SMT | Version B1 | A central Z3 grouping optimization assigns workers globally; each recharger then solves a local SMT problem in a group-specific region. Overlapping groups respect earlier plans. Bounded cycle retries and global regrouping handle planning failure. |

Both decomposed methods use a wavefront: groups in the same dependency level can plan concurrently; overlapping groups are ordered by priority. Thus semi-decentralized does not mean every local solve is sequential. Its centralized component is worker assignment, followed by local scheduling. Semi-decentralized planning preserves neighbor reservations instead of accepting an unconstrained fallback.

All three evaluated configurations use the **two-shot** method. Phase 1 selects a worker schedule and recharge appointments; phase 2 completes the recharger schedule for that selected solution and searches the allowed extended horizon. This decomposition is not a globally optimal joint scheduler. The optional phase-2 primitive-cost objective is disabled. No one-shot experiment is included here.

## 2. Experimental design and provenance

The new results are the 56 per-job JSON files in `mrp_backup/version_a_b1_parallel`: 28 decentralized and 28 semi-decentralized runs. The primary comparison uses 22 base scenarios, each matched to a completed collision-enabled centralized result in `mrp_backup/study-20261114-180038`. Matching keys are map, grid, worker count, recharger count and requested horizon. The six anchor followups are reported separately because they change the physical anchor layout. Base A/B1 pairs use the same saved anchors. All primary runs request T=8; scenario sizes include 19×19 and padded 25×25 grids. Padding extends the workspace boundary and does not establish scaling to independently generated larger maps.

The 22 base scenarios are the available matched completed suite, not a full factorial or a random sample of all possible instances. Each configuration has one observation, so uncertainty intervals and significance tests are not justified. A/B1 are configured for parallel planning, while centralized times come from earlier notebook sessions. Hardware, runtime load and CPU allocations are not controlled. Timings measure the recorded pipelines, including grouping and setup where implemented. Observed speed ratios should not be read as hardware-independent algorithmic speedups.

The centralized implementation is **not a new execution of the paper's original C++ binary**. The paper informs the two-shot formulation and the visual presentation, not the numerical data in this report. The scenario horizons and experiment matrix differ from the paper's figures.

## 3. Metrics and acceptance rules

Coverage is the fraction of requested workers served by successful plans. A stored SAT result for the decentralized method can still contain fallback or lack an executable coordinated mission. For centralized SMT, a returned SAT result with full coverage and no reported conflicts counts as a **pass**. All 22 centralized results pass. For the two decomposed methods, a pass uses the recorded executable acceptance checks: full coverage, no reported conflicts, no fallback groups, the executable flag and the verifier's 3000-step joint-period limit. These method-specific rules define acceptance in this report; no separate deployment-ready comparison is presented.

Let D be the sum of worker-horizon products across live groups, H their recorded hard waiting total and Q the recharge contribution counted by the implementation. Hard efficiency is 100(D−H)/D; soft efficiency is 100(D−H+Q)/D. The lower stacked bar represents hard efficiency; its hatched addition is soft minus hard, corresponding to the counted recharge contribution. Their sum is soft efficiency. These are schedule metrics, not independently measured physical utilization. Unequal group cycles require worker-horizon weighting.

Quality comparisons use the **same 13 scenarios where all three pass their planning checks**. Raw primitive cost is summed over each recorded cycle, so unequal cycle lengths prevent interpreting it directly as long-run energy consumption. Zero detected conflicts refers to the implemented offline verifier; trajectories are not rerun or physically validated in this study.

## 4. Coverage and completion

{table(s[['Method','Cases','Passes','Mean coverage (%)','Median time (s)']])}

![Figure 1. Coverage and acceptance](fig02_coverage_acceptance.png)

**Figure 1.** Left: mean requested-worker coverage over the 22 base scenarios. Right: passes under the method-specific acceptance rules in Section 3. Centralized and semi-decentralized SMT pass all 22 cases; decentralized SMT passes 13.

Decentralized SMT has six partial-coverage base results and three additional full-coverage results that use fallback. Thus its 16/22 full-coverage count reduces to 13/22 executable passes. Semi-decentralized SMT passes all 22 base cases. Including anchor followups, it passes 28/28 compared with 18/28 for decentralized SMT; one decentralized followup has full coverage but recorded conflicts. This distinction explains why coverage alone overstates coordinated success.

## 5. Computation time

![Figure 2. Computation time by scenario](fig01_computation_time.png)

**Figure 2.** Pipeline wall time for the three methods, separated by workspace. Labels give worker count (w) and recharger count (r); padded cases carry a 25×25 label. All other cases use 19×19 grids. The logarithmic vertical axis accommodates runtimes from seconds to hours. Partial and rejected plans remain visible and are identified in the scenario table and Figure 3.

![Figure 3. Paired timing](fig04_paired_runtime.png)

**Figure 3.** Left: each point compares one decomposed-method run against its matched centralized run; points below the diagonal finish sooner. Crosses mark rejected decentralized results. Right: sorted observed centralized/method time ratios; values above one favor the decomposed method. The sorting orders are independent for the two lines and do not identify the same case at each rank.

Decentralized SMT finishes sooner than centralized SMT in 21/22 base pairs, with median paired time ratio 2.85×. Semi-decentralized SMT finishes sooner in 13/22 pairs, with median ratio 1.20×. These all-case ratios include rejected decentralized plans. Restricting to the 13 common planning successes gives median paired ratios of 2.55× and 3.32×, respectively. The median of paired ratios differs from the ratio of aggregate median times.

Semi-decentralized planning has a substantial runtime tail: warehouse 6w/3r takes 7238.80 s (about 2.01 h), warehouse 3w/2r on the padded grid takes 3620.71 s, and warehouse 3w/2r on the original grid takes 1924.13 s. Its complete coverage is therefore accompanied by unpredictable computation on some cases. The aggregate results alone cannot attribute these costs to specific SMT constraints or prove that a particular retry policy causes them.

## 6. Efficiency on equally served scenarios

{table(q)}

![Figure 4. Work and recharge efficiency](fig03_work_recharge_efficiency.png)

**Figure 4.** Paper-inspired grouped stacked bars for the 13 common planning successes. Each scenario has centralized, decentralized and semi-decentralized bars in legend order. Solid height is hard efficiency; the hatched addition is the counted recharge contribution, so total height is soft efficiency. Missing/rejected scenarios are excluded rather than assigned zero efficiency.

On this matched subset, semi-decentralized SMT improves mean hard efficiency over decentralized SMT by 6.14 percentage points and over centralized SMT by 13.84 points. Mean soft efficiency is 43.27%, versus 36.89% and 26.82%. These are descriptive means over 13 selected successful cases. Different decompositions can select different phase-1 schedules and extended horizons; the result does not contradict a global optimality theorem because the evaluated two-shot centralized method offers no such joint guarantee.

## 7. Additional anchor layouts

![Figure 5. Anchor followups](fig05_anchor_followups.png)

**Figure 5.** Six additional saved anchor layouts. Left: recorded runtime; right: worker coverage, with a red cross marking failure of the executable screen. These layouts are additional scenarios, not timing repeats and not independent centralized comparisons.

Semi-decentralized SMT passes all six layouts. Decentralized SMT passes five; its remaining layout has full coverage but two reported conflicts. This supports checking coordinated acceptance after assignment and local planning even when every worker is nominally served.

![Figure 6. Anchor-layout efficiency](fig06_anchor_efficiency.png)

**Figure 6.** Hard and soft worker efficiency for all six additional anchor layouts, comparing decentralized SMT (blue) with semi-decentralized SMT (orange). Solid height is hard efficiency; the hatched recharge contribution extends the bar to soft efficiency. A red × marks the rejected decentralized candidate: its saved efficiency is shown for completeness, not as an accepted executable result. These six layouts are not included in Figure 4. No centralized bars are shown because matched centralized followup runs are unavailable.

## 8. Scenario results

All times below are seconds. Centralized SMT passes in every row. The full source paths, solver outcomes and diagnostic fields remain available in the existing audited CSV files.

{table(per)}

## 9. Conclusions and limitations

Centralized and semi-decentralized SMT both pass every matched base case. Semi-decentralized SMT additionally passes all six anchor followups. The decentralized method generally returns candidates quickly, but locality, alignment and fallback outcomes reduce complete coordinated acceptance. Both centralized and semi-decentralized SMT exhibit several long solves.

The evidence supports a tradeoff between computation and complete coordinated service. It does not establish general superiority on unseen maps, real-time operation, hardware-controlled speedups, or physical safety under delays and failures. Neither a complete assignment nor SAT alone guarantees executable operation. Followup experiments should repeat runs on fixed hardware and separate grouping, phase-1, phase-2, retry and setup costs before making causal performance claims.

'''
    (OUT/'COMPARATIVE_RESEARCH_REPORT.md').write_text(report,encoding='utf-8')
    render_html()
    print(OUT/'COMPARATIVE_RESEARCH_REPORT.md')


def render_html():
    """Export the current Markdown, preserving manual edits and embedded figures."""
    report = (OUT/'COMPARATIVE_RESEARCH_REPORT.md').read_text(encoding='utf-8')
    body = MarkdownIt('commonmark').enable('table').render(report)
    def embed(match):
        source = match.group(1)
        path = OUT/source
        if not path.is_file():
            return match.group(0)
        mime = 'image/png' if path.suffix.lower() == '.png' else 'image/jpeg'
        return 'src="data:' + mime + ';base64,' + base64.b64encode(path.read_bytes()).decode() + '"'
    body = re.sub(r'src="([^"]+)"', embed, body)
    body = body.replace('<table>', '<div class="table"><table>').replace('</table>', '</table></div>')
    css='body{max-width:1100px;margin:40px auto;padding:20px;font:16px/1.6 Georgia,serif;color:#222}h1,h2{line-height:1.25}h2{margin-top:2em;border-bottom:1px solid #bbb;padding-bottom:.3em}img{width:100%;height:auto}table{border-collapse:collapse;font:12px/1.4 Arial,sans-serif;width:100%}th,td{border-bottom:1px solid #ccc;padding:6px;text-align:right}th{background:#eee}.table{overflow:auto}@media print{body{margin:0;max-width:none;font-size:11pt}h2{break-after:avoid}img,tr{break-inside:avoid}.table{overflow:visible}table{font-size:8pt}}'
    (OUT/'COMPARATIVE_RESEARCH_REPORT.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>Comparative SMT Planning Study</title><style>'+css+'</style><body>'+body+'</body></html>',encoding='utf-8')


if __name__=='__main__':
    if '--html-only' in sys.argv:
        render_html()
        print(OUT/'COMPARATIVE_RESEARCH_REPORT.html')
    else:
        main()
