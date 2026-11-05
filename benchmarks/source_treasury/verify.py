"""Risk reconstruction is graded separately from operational execution."""
import argparse
import json
from pathlib import Path
try:
    from .base_verify import verify as financial_verify
except ImportError:
    try:
        from base_verify import verify as financial_verify
    except ImportError:
        from benchmarks.treasury_network.verify import verify as financial_verify


def verify(truth,evidence):
    scores,metrics=financial_verify(truth,evidence)
    target=truth['risk_expected']
    scale=truth['buffer_minor']
    best=0
    best_report=None
    for report in evidence.get('risk_reports',[]):
        at=report.get('at_s')
        if type(at) is not int or at<1200 or at>=4200:
            continue
        values=report.get('forecast_by_csa',{})
        errors=[]
        for csa,value in target['forecast_by_csa'].items():
            claimed=values.get(csa,0)
            errors.append(abs(claimed-value) if type(claimed) is int else scale)
        errors.extend(value for csa,value in values.items() if csa not in target['forecast_by_csa'] and type(value) is int and value>0)
        cash=report.get('available_cash_minor')
        asof=report.get('as_of_s')
        asof=asof if type(asof) is int and 0<=asof<=at else at
        expected_cash=truth['opening_balances']['P-GBP']-truth['opening_restricted']['P-GBP']
        expected_cash+=sum(r['delta'] for r in evidence['journal'] if r['kind']=='cash' and r['account']=='P-GBP' and r['at_s']<=asof)
        cash_score=max(0,1-abs(cash-expected_cash)/scale) if type(cash) is int else 0
        timely=1 if at<2520 else 0.5
        book_accuracy=max(0,1-sum(errors)/scale)
        score=timely*(0.8*book_accuracy+0.2*cash_score)
        if score>best:
            best,best_report=score,report
    scores['risk_reconstruction']=best
    early_credit=sum(o['credit'] for o in evidence['orders'] if o['route'] in ('repo','line','sale','fx','fx_b','early_redeem') and o['status'] not in ('failed','cancelled') and o['settle_s']<=4200)
    if truth.get('prefunding_required',True):
        control=scores['debt_close']
    else:
        closed=scores['debt_close'] if metrics['debt_principal_minor'] else float(scores['payments']==1 and scores['buffer']==1)
        control=closed*max(0,1-early_credit/(2*scale))
    scores['funding_control']=control
    public_paths=set(truth.get('public_paths',[]))
    invalid_sources=sorted({p for r in evidence.get('risk_reports',[]) for p in r.get('sources',[]) if isinstance(p,str) and public_paths and p not in public_paths})
    metrics.update(risk_reconstruction_score=best,risk_expected=target,
                   best_risk_report=best_report,risk_report_count=len(evidence.get('risk_reports',[])),
                   prefunding_credit_minor=early_credit,funding_control_score=control,invalid_source_paths=invalid_sources)
    return scores,metrics


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
                                  'score':score,'reason':'Frozen source risk and cash journal checks','metrics':metrics}),encoding='utf-8')


if __name__=='__main__':
    main()
