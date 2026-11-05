"""Author-side feasible path. Never included in the subject workspace."""
from .workflow.engine import Treasury, M


def execute(level,variant='calibration'):
    retained=variant=='retained'
    desk=Treasury({'level':level,'variant':variant})
    def at(t,**action):
        desk.advance(t)
        result=desk.act(action)
        desk.record('action',action=action,result={'ok':True,'value':result})
        return result
    line=(6 if retained else 5) if level==1 else (10 if retained else 9) if level==2 else 12
    line_amount=(56 if retained else 46)*M//10 if level>=6 else line*M
    approval=at(60,action='approval',route='line',amount=line_amount)
    fx=at(1500,action='quote',route='fx')
    at(1530,action='submit',route='fx',amount=7*M,key='fx',quote_id=fx['id'])
    repo=at(2160,action='quote',route='repo')
    allocations={'collateral':[{'lot_id':'G1','market_value_minor':(16 if retained else 17)*M},
                               {'lot_id':'G2','market_value_minor':(8 if retained else 7)*M}]} if level>=7 else {}
    repo_order=at(2190,action='submit',route='repo',amount=204*M//10,key='repo',quote_id=repo['id'],**allocations)
    line_order=at(2220,action='submit',route='line',amount=line_amount,key='line',approval_id=approval['id'])
    if level>=3:
        sale=at(2790,action='quote',route='sale')
        at(2820,action='submit',route='sale',amount=4*M,key='sale',quote_id=sale['id'])
    if level>=5:
        second=at(2850,action='quote',route='fx_b')
        at(2880,action='submit',route='fx_b',amount=(7 if level>=6 else 4 if retained else 3)*M,key='fx-b',quote_id=second['id'])
        at(2910,action='submit',route='payment',amount=3*M,key='c1',obligation_id='C1',version=1,account='P-GBP',ssi_version='SSI2')
    if level>=6:
        at(2940,action='submit',route='early_redeem',amount=4*M,key='mmf-early')
    if level>=4:
        at(3330,action='reconcile',order_id=repo_order['id'],account='P-GBP',reference='CR-'+repo_order['id'])
    at(3630,action='submit',route='payment',amount=((40 if level==1 else 44)+int(retained))*M,key='m1',obligation_id='M1',version=1 if level==1 or level>=6 else 2,account='P-GBP',ssi_version='SSI2' if level>=5 else 'SSI1')
    at(3660,action='submit',route='payment',amount=(9 if retained else 8)*M,key='m2',obligation_id='M2',version=1,account='P-GBP',ssi_version='SSI2' if level>=5 else 'SSI1')
    at(3690,action='submit',route='payment',amount=(6 if retained else 5)*M,key='u1',obligation_id='U1',version=1,account='P-USD',ssi_version='SSI2' if level>=5 else 'SSI1')
    at(3720,action='submit',route='redeem',amount=(6 if level>=6 else 9 if retained else 8)*M,key='redeem')
    at(7300 if level>=8 else 7900,action='submit',route='repay',amount=repo_order['credit'],key='repay-repo',debt_id=repo_order['id'])
    at(7330 if level>=8 else 9400,action='submit',route='repay',amount=line_order['credit'],key='repay-line',debt_id=line_order['id'])
    at(10000,action='handover',text='All three obligations have been discharged. Repo and standby principals were repaid on their maturities and collateral released. Opening restricted cash remains restricted. No unresolved operations; treasury is the incoming owner.')
    desk.advance(10800)
    return desk.export()
