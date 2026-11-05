"""Synthetic source documents and independent author-side reconstruction."""
import csv
import io
import json

M=100_000_000


def csv_text(rows):
    stream=io.StringIO(newline='')
    writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
    writer.writeheader();writer.writerows(rows)
    return stream.getvalue()


def sources(retained=False,scenario='stress'):
    if scenario not in ('stress','quiet','hedged'):
        raise ValueError('Unknown source scenario')
    trades=[]
    def trade(id,csa,mtm,delta,entity='P',status='open',product='rates_swap',revision=1):
        trades.append({'delivery_id':f'D{len(trades)+1}','trade_id':id,'entity':entity,'csa_id':csa,
                       'currency':'GBP','product':product,'status':status,'revision':revision,
                       'effective_s':-1800,'close_s':99999 if status=='open' else -60,
                       'mtm_minor':mtm*M,'dv01_minor_per_bp':delta,'risk_factor':'GBP-RATES'})
    trade('T101','RHO',-18,-60_000_000)
    trade('T102','RHO',5,0,revision=1)
    trade('T102','RHO',5 if retained else 6,-16_000_000,revision=2)
    # Redelivery of the current revision is not another position.
    trades.append({**trades[-1],'delivery_id':'D-REPLAY-102'})
    trade('T201','TAU',-11,-48_000_000)
    trade('T202','TAU',9,0)
    trade('T301','SIGMA',-8,-20_000_000)
    trade('T302','SIGMA',4,0)
    trade('T401','OMEGA',6,0)
    trade('F211','TAU',8,0,product='fx_forward')
    for id,csa,delta in [('T105','RHO',76_000_000),('T205','TAU',48_000_000),('T305','SIGMA',20_000_000)]:
        trade(id,csa,0,delta if scenario=='hedged' else 0)
    for i in range(24):
        trade(f'Q{i:03}',('RHO','TAU','SIGMA')[i%3],-2-i,-12_000_000,entity='Q')
    for i in range(80):
        trade(f'H{i:03}',('RHO','TAU','SIGMA','OMEGA')[i%4],-3-i,-2_000_000,status='closed')
    agreements=[{'entity':'P','csa_id':c,'currency':'GBP','product_scope':'rates_swap','threshold_minor':t*M,
                 'effective_s':-3600,'document':'agreements/CSA-pack.md'} for c,t in [('RHO',2),('TAU',1),('SIGMA',0),('OMEGA',0)]]
    agreements += [{**r,'entity':'Q','threshold_minor':0} for r in agreements[:3]]
    posted=[]
    for c,vm,im in [('RHO',3,4),('TAU',1,4),('SIGMA',3,2),('OMEGA',0,0)]:
        for kind,value in [('VM',vm),('IM',im)]:
            posted.append({'posting_id':c+'-'+kind,'entity':'P','csa_id':c,'currency':'GBP','kind':kind,
                           'amount_minor':value*M,'status':'posted','effective_s':-3600})
    restrictions=[{'delivery_id':'R-D1','restriction_id':'R1','account':'P-GBP','amount_minor':4*M,'start_s':-7200,'end_s':99999},
                  {'delivery_id':'R-D2','restriction_id':'R2','account':'P-GBP','amount_minor':(3 if retained else 2)*M,'start_s':-3600,'end_s':99999},
                  {'delivery_id':'R-D3','restriction_id':'R-OLD','account':'P-GBP','amount_minor':5*M,'start_s':-7200,'end_s':-1},
                  {'delivery_id':'R-D4','restriction_id':'R1','account':'P-GBP','amount_minor':4*M,'start_s':-7200,'end_s':99999},
                  {'delivery_id':'R-D5','restriction_id':'Q1','account':'Q-GBP','amount_minor':9*M,'start_s':-7200,'end_s':99999}]
    accounts=[{'account':'P-GBP','entity':'P','currency':'GBP','book_minor':(21 if retained else 20)*M,'as_of_s':0},
              {'account':'P-USD','entity':'P','currency':'USD','book_minor':(13 if retained else 12)*M,'as_of_s':0},
              {'account':'Q-GBP','entity':'Q','currency':'GBP','book_minor':15*M,'as_of_s':0}]
    return {
        'bank/opening.csv':csv_text(accounts),
        'bank/restrictions.csv':csv_text(restrictions),
        'positions/trade_register.csv':csv_text(trades),
        'agreements/csa_terms.csv':csv_text(agreements),
        'agreements/margin_collateral.csv':csv_text(posted),
        'inbox/MB-025.csv':csv_text([{'risk_factor':'GBP-RATES','delta_bp':0 if scenario=='quiet' else 25,'as_of_s':1200,'source':'MARKET-DATA-A'}]),
        'agreements/CSA-pack.md':'''Synthetic agreement schedule, applicable to this exercise only.
Each entity is a separate legal person. Netting is permitted only within the same entity, agreement ID and currency, for products listed in that agreement's product_scope. Registry records outside that scope are not cash VM offsets under this schedule.
Trade MTM is from the entity's perspective: positive is an asset, negative a liability. Published market-factor moves modify current MTM by signed dv01_minor_per_bp times delta_bp. This linear exercise uses no convexity adjustment.
For an eligible active netting set, additional cash VM is max(0, -net MTM - threshold - posted VM). IM remains segregated and does not discharge VM under this schedule. Positive net MTM is not same-day cash in this exercise.
The registry contains lifecycle history and delivery copies. Current business records use the highest effective revision for each entity and trade_id as of the evaluation time. A repeated delivery of that revision is not another trade. Closed trades and trades with close_s at or before the evaluation time have no ongoing MTM under this schedule.
All monetary numeric columns are currency minor units. The terms table and posting register specify the agreement-specific threshold and designated collateral. These documents define business rights and do not select funding transactions.
''',
        'bank/record-semantics.md':'''Bank opening.csv is a point-in-time posted book balance, not a liquidity forecast. Active restrictions overlay that balance. A restriction is active for start_s <= current time < end_s. Delivery copies with the same restriction_id describe one restriction. Restrictions are account-specific and do not transfer legal title between entities.
Expected-inflows.csv contains the contractual intraday receipt schedule; future receipts are not current cash. The accounts query returns current posted bank balances. It does not calculate the internal restriction overlay or reserved liquidity for the operator.
''',
        'risk/forecast-open.json':json.dumps({'as_of_s':0,'entity':'P','currency':'GBP','estimate_minor':(9 if retained else 8)*M,'status':'estimate','source':'overnight-risk-cache'}),
    }


def reconstruct(files,as_of_s=1200):
    def rows(name):
        return list(csv.DictReader(io.StringIO(files[name])))
    accounts={r['account']:int(r['book_minor']) for r in rows('bank/opening.csv')}
    active={r['restriction_id']:r for r in rows('bank/restrictions.csv') if int(r['start_s'])<=as_of_s<int(r['end_s'])}
    cash=accounts['P-GBP']-sum(int(r['amount_minor']) for r in active.values() if r['account']=='P-GBP')
    agreements={(r['entity'],r['csa_id']):r for r in rows('agreements/csa_terms.csv') if int(r['effective_s'])<=as_of_s}
    current={}
    for r in rows('positions/trade_register.csv'):
        key=(r['entity'],r['trade_id'])
        if int(r['effective_s'])<=as_of_s and (key not in current or int(r['revision'])>int(current[key]['revision'])):
            current[key]=r
    moves={r['risk_factor']:int(r['delta_bp']) for r in rows('inbox/MB-025.csv') if int(r['as_of_s'])<=as_of_s}
    mtm={c:0 for e,c in agreements if e=='P'}
    for r in current.values():
        term=agreements.get((r['entity'],r['csa_id']))
        if r['entity']!='P' or not term or r['product']!=term['product_scope'] or r['status']!='open' or int(r['close_s'])<=as_of_s:
            continue
        mtm[r['csa_id']]+=int(r['mtm_minor'])+int(r['dv01_minor_per_bp'])*moves.get(r['risk_factor'],0)
    posted={r['posting_id']:r for r in rows('agreements/margin_collateral.csv') if r['entity']=='P' and r['kind']=='VM' and r['status']=='posted' and int(r['effective_s'])<=as_of_s}
    calls={c:max(0,-value-int(agreements['P',c]['threshold_minor'])-sum(int(r['amount_minor']) for r in posted.values() if r['csa_id']==c)) for c,value in mtm.items()}
    return {'available_cash_minor':cash,'forecast_by_csa':calls,'total_minor':sum(calls.values())}
