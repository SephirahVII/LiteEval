"""Aggregations with explicit denominators and conservative case-level comparisons."""
from collections import Counter
import json
import statistics

VALID = {'pass', 'fail'}

def case_outcome(run, scores, evaluator_ids):
    if run is None:
        return 'pending'
    if run['status']=='cancelled':return 'cancelled'
    if run['status'] in {'running', 'retrying'}:
        return 'pending'
    if run['status'] != 'succeeded':
        return 'unscored'
    by_id = {s['evaluator_revision_id']: s['outcome'] for s in scores}
    outcomes = [by_id.get(i, 'missing') for i in evaluator_ids]
    # A known failing criterion is sufficient to fail; missing scores cannot pass.
    if 'fail' in outcomes:
        return 'fail'
    return 'pass' if outcomes and all(v == 'pass' for v in outcomes) else 'unscored'

def summary(db, exp):
    ids = [r[0] for r in db.execute('SELECT evaluator_revision_id FROM experiment_evaluators WHERE experiment_id=?', (exp['id'],))]
    frozen = list(db.execute('SELECT case_id,revision FROM snapshot_cases WHERE snapshot_id=? ORDER BY ordinal', (exp['snapshot_id'],)))
    runs = [dict(r) for r in db.execute('SELECT * FROM runs WHERE experiment_id=?', (exp['id'],))]
    scores = [dict(r) for r in db.execute('SELECT s.* FROM scores s JOIN runs r ON r.id=s.run_id WHERE r.experiment_id=?', (exp['id'],))]
    by_case = {(r['case_id'],r['case_revision']): r for r in runs}
    per_run = {r['id']: [s for s in scores if s['run_id']==r['id']] for r in runs}
    cases = []
    for item in frozen:
        run = by_case.get((item[0],item[1]))
        cases.append({'case_id':item[0], 'revision':item[1], 'run_id':run['id'] if run else None,
                      'outcome':case_outcome(run, per_run.get(run['id'],[]) if run else [], ids)})
    if exp['status']=='cancelled':
        for case in cases:
            if case['outcome']=='pending':case['outcome']='cancelled'
    counts = dict(Counter(c['outcome'] for c in cases))
    for kind in ['pass','fail','unscored','pending','cancelled']: counts.setdefault(kind,0)
    metrics=[]
    for eid in ids:
        chosen=[s for s in scores if s['evaluator_revision_id']==eid]
        outcomes=Counter(s['outcome'] for s in chosen)
        numeric=[s['value'] for s in chosen if s['outcome'] in VALID and s['value'] is not None]
        valid=outcomes['pass']+outcomes['fail']
        name=db.execute('SELECT name FROM evaluator_revisions WHERE id=?',(eid,)).fetchone()[0]
        metrics.append({'evaluator_revision_id':eid,'name':name,
                        'counts':{k:outcomes[k] for k in ['pass','fail','abstain','error']},
                        'missing':len(frozen)-len(chosen),'scored_count':valid,
                        'pass_rate':outcomes['pass']/valid if valid else None,
                        'mean_score':statistics.mean(numeric) if numeric else None})
    latency=[r['latency_ms'] for r in runs if r['latency_ms'] is not None and r['status']=='succeeded']
    cost={}
    for r in runs:
        if r['cost'] is not None and r['currency']:
            cost.setdefault(r['currency'],[]).append(r['cost'])
    attempts=[dict(r) for r in db.execute('SELECT a.* FROM attempts a JOIN runs r ON r.id=a.run_id WHERE r.experiment_id=?',(exp['id'],))]
    attempt_cost={}
    usage_totals={};usage_samples=0
    for attempt in attempts:
        usage=json.loads(attempt['usage_json'])
        if usage:
            usage_samples+=1
            for k in ('input_tokens','output_tokens','prompt_tokens','completion_tokens'):
                if type(usage.get(k)) is int:usage_totals[k]=usage_totals.get(k,0)+usage[k]
        if attempt['cost'] is not None and attempt['currency']:
            attempt_cost.setdefault(attempt['currency'],[]).append(attempt['cost'])
    total=len(frozen); scored=counts['pass']+counts['fail']
    return {'total_cases':total,'completed_runs':sum(r['status'] not in {'running','retrying'} for r in runs),
            'run_status_counts':dict(Counter(r['status'] for r in runs)), 'case_counts':counts,
            'success_rate':counts['pass']/total if total else None,
            'scored_pass_rate':counts['pass']/scored if scored else None,
            'scoring_coverage':scored/total if total else None,
            'execution_success_rate':sum(r['status']=='succeeded' for r in runs)/total if total else None,
            'latency':{'mean_ms':statistics.mean(latency) if latency else None,'sample_count':len(latency)},
            'agent_usage':{'totals':usage_totals,'attempt_sample_count':usage_samples},
            'attempt_cost_by_currency':{k:{'total':sum(v),'sample_count':len(v)} for k,v in attempt_cost.items()},
            'cost_by_currency':{k:{'total':sum(v),'mean':statistics.mean(v),'sample_count':len(v)} for k,v in cost.items()},
            'metrics':metrics,'cases':cases}

def compare(store,left_id,right_id):
    with store.connect() as db:
        left=store.required(db,'experiments',left_id);right=store.required(db,'experiments',right_id)
        if left['project_id'] != right['project_id']:
            raise ValueError('Experiments belong to different projects')
        summaries=[summary(db,e) for e in (left,right)]
        hashes=[db.execute('SELECT snapshot_hash FROM dataset_snapshots WHERE id=?',(e['snapshot_id'],)).fetchone()[0] for e in (left,right)]
        evals=[set(r[0] for r in db.execute('SELECT evaluator_revision_id FROM experiment_evaluators WHERE experiment_id=?',(e['id'],))) for e in (left,right)]
        if any(e['status']!='completed' for e in (left,right)) or hashes[0]!=hashes[1] or evals[0]!=evals[1]:
            return {'comparable':False,'reason':'Both experiments must be completed with identical Case revisions and evaluator revisions'}
        runs=[[dict(r) for r in db.execute('SELECT * FROM runs WHERE experiment_id=?',(e['id'],))] for e in (left,right)]
        all_scores=[{(r['case_id'],r['case_revision'],s['evaluator_revision_id']):dict(s)
                     for r in rs for s in db.execute('SELECT * FROM scores WHERE run_id=?',(r['id'],))} for rs in runs]
        by_case=[{(r['case_id'],r['case_revision']):r for r in rs} for rs in runs]
        frozen=list(db.execute('SELECT case_id,revision FROM snapshot_cases WHERE snapshot_id=? ORDER BY ordinal',(left['snapshot_id'],)))
    metric_counts=Counter();case_counts=Counter();details=[]
    for row in frozen:
        key=(row[0],row[1]); changes=[]
        for eid in sorted(evals[0]):
            scores=[s.get((*key,eid)) for s in all_scores]
            a,b=[s['outcome'] if s else 'missing' for s in scores]
            change='unscored' if a not in VALID or b not in VALID else 'same' if a==b else 'improved' if a=='fail' else 'regressed'
            metric_counts[change]+=1
            changes.append({'evaluator_revision_id':eid,'left':a,'right':b,'change':change,
                            'value_delta':scores[1]['value']-scores[0]['value'] if all(s and s['value'] is not None for s in scores) else None})
        # Any regression wins; incomplete evidence is never reported as an improvement.
        states={c['change'] for c in changes}
        state='regressed' if 'regressed' in states else 'unscored' if 'unscored' in states else 'improved' if 'improved' in states else 'same'
        case_counts[state]+=1
        details.append({'case_id':key[0],'case_revision':key[1],'change':state,
                        'left_run_id':by_case[0].get(key,{}).get('id'),'right_run_id':by_case[1].get(key,{}).get('id'),
                        'mixed': 'improved' in states and 'regressed' in states,'metrics':changes})
    names=['improved','regressed','same','unscored']
    return {'comparable':True,'counts':{k:case_counts[k] for k in names},
            'metric_pair_counts':{k:metric_counts[k] for k in names}, 'details':details,
            'left_summary':summaries[0],'right_summary':summaries[1],
            'policy':'any_regression_wins; missing/error/abstain prevent improvement'}
