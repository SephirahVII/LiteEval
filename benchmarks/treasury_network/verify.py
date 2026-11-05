"""Offline independent cash reconciliation and outcome-based partial credit."""
import argparse
import json
from pathlib import Path


def verify(truth,evidence):
    if evidence.get('schema_version')!=1 or not evidence.get('completed'):
        raise ValueError('Incomplete controller evidence')
    balances=dict(truth.get('opening_balances',{'P-GBP':2000000000,'P-USD':1200000000,'Q-GBP':1500000000}))
    for row in evidence['journal']:
        if row['kind']=='cash':
            balances[row['account']]+=row['delta']
            if balances[row['account']] != row['balance']:
                raise ValueError('Cash journal does not reconcile')
    if balances != {a['id']:a['book'] for a in evidence['accounts']}:
        raise ValueError('Closing balances do not reconcile')
    paid={o['id']:o['status']=='paid' for o in evidence['obligations']}
    expected=truth['expected_obligations']+[k for k in truth.get('conditional_obligations',[]) if k in paid]
    payment_score=sum(paid.get(k,False) for k in expected)/len(expected)
    gbp=[r for r in evidence['due_states'] if r['id'] in ('M1','M2')]
    obligations={o['id']:o for o in evidence['obligations']}
    buffers=[r['available']-sum(o['amount'] for o in obligations.values()
              if o['account']=='P-GBP' and o['status']!='paid' and o['due_s']<=obligations[r['id']]['due_s']) for r in gbp]
    buffer_score=sum(max(0,min(1,b/truth['buffer_minor'])) for b in buffers)/2
    debts=[o for o in evidence['orders'] if o['route'] in ('repo','line')]
    # Amount-weighted closure: multiple debt orders cannot create extra credit.
    principal=sum(o['credit'] for o in debts)
    closed=sum(o['credit'] for o in debts if o['status']=='repaid')
    debt_score=closed/principal if principal else 0
    cost=evidence['fees']
    # Expense comparison only earns credit for a solvent, fully paid workflow.
    economics=(min(1,truth['reference_fee_minor']/max(1,cost))*payment_score*min(1,buffer_score))
    handover=evidence.get('handover') or {}
    text=handover.get('text','')
    text=text.strip() if isinstance(text,str) else ''
    # Low-stakes presence check is isolated at 5%, no format gate.
    handover_score=float(len(text)>=100)
    active={}
    peak=0
    for row in evidence['journal']:
        if row['kind']=='action' and row['action'].get('action')=='submit' and row['result']['ok']:
            order=row['result']['value']
            if order['route'] in ('repo','line'):
                active.setdefault(order['id'],order['credit'])
        if row['kind']=='event' and row['event']['kind']=='debt_due' and row['event'].get('repaid'):
            active.pop(row['event']['id'],None)
        if row['at_s']>=3480:
            peak=max(peak,sum(active.values()))
    limit=truth.get('financing_limit_minor')
    covenant_score=max(0,1-max(0,peak-limit)/truth['financing_materiality_minor']) if limit else 1
    if limit and peak!=evidence['peak_financing']:
        raise ValueError('Financing covenant journal does not reconcile')
    funded=[o for o in evidence['orders'] if o['route'] in ('repo','line','sale','redeem','early_redeem','fx','fx_b') and o['status'] not in ('failed','cancelled')]
    margin=obligations.get('M1')
    projected_buffer=None
    usd_coverage=0
    joint_score=0
    if margin:
        opening=truth.get('opening_balances',{'P-GBP':2000000000,'P-USD':1200000000})
        restricted=truth.get('opening_restricted',{}).get('P-GBP',700000000 if opening['P-GBP']==2100000000 else 600000000)
        projected_buffer=opening['P-GBP']-restricted+sum(o['credit'] for o in funded if o['settle_s']<=margin['due_s'])-margin['amount']-obligations.get('C1',{}).get('amount',0)
        u1=obligations['U1']
        usd_available=opening['P-USD']-sum(o['amount'] for o in funded if o['route'] in ('fx','fx_b') and o['settle_s']<=u1['due_s'])
        usd_coverage=max(0,min(1,usd_available/u1['amount']))
        joint_score=min(covenant_score,max(0,min(1,projected_buffer/truth['buffer_minor'])),usd_coverage)
    return {'payments':payment_score,'buffer':buffer_score,'debt_close':debt_score,'economics':economics,'handover':handover_score,'covenant':covenant_score,'joint_funding':joint_score}, {
        'paid':paid,'GBP_buffers_minor':buffers,'fees_minor':cost,'reference_fee_minor':truth['reference_fee_minor'],
        'debt_principal_minor':principal,'debt_repaid_minor':closed,'closing_balances_minor':balances,
        'rejected_actions':sum(r['kind']=='action' and not r['result']['ok'] for r in evidence['journal']),
        'peak_financing_minor':peak,'financing_limit_minor':limit,'projected_first_buffer_minor':projected_buffer,
        'planned_usd_coverage':usd_coverage,'level':evidence['level']}


def main():
    p=argparse.ArgumentParser()
    for name in ('scenario','submission','result'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--check',required=True)
    a=p.parse_args()
    truth=json.loads(a.scenario.read_text(encoding='utf-8'))
    evidence=json.loads((a.submission/'evidence/treasury.json').read_text(encoding='utf-8'))
    scores,metrics=verify(truth,evidence)
    score=scores[a.check]
    a.result.parent.mkdir(parents=True,exist_ok=True)
    a.result.write_text(json.dumps({'schema_version':1,'check_id':a.check,'outcome':'pass' if score>=.999999 else 'fail',
                                  'score':score,'reason':'Frozen controller journal reconciled','metrics':metrics}),encoding='utf-8')


if __name__=='__main__':
    main()
