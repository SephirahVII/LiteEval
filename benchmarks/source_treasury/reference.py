"""Author feasibility witness; not supplied to the tested Agent."""
from .authoring import M,reconstruct
from .workflow.source_engine import SourceDesk


def execute(files,fixture):
    retained=fixture['variant']=='retained'
    desk=SourceDesk(fixture,files)
    def at(t,**action):
        desk.advance(t)
        result=desk.act(action)
        desk.record('action',action=action,result={'ok':True,'value':result})
        return result
    amount=(56 if retained else 46)*M//10
    if fixture.get('scenario','stress')!='stress':
        desk.advance(1200)
        expected=reconstruct(files)
        at(1740,action='risk_report',forecast_by_csa=expected['forecast_by_csa'],available_cash_minor=expected['available_cash_minor'],as_of_s=1200,
           sources=['positions/trade_register.csv','agreements/CSA-pack.md','inbox/MB-025.csv','bank/restrictions.csv'])
        at(3630,action='submit',route='payment',amount=expected['total_minor'],key='m1',obligation_id='M1',version=1,account='P-GBP',ssi_version='SSI2')
        at(3660,action='submit',route='payment',amount=(9 if retained else 8)*M,key='m2',obligation_id='M2',version=1,account='P-GBP',ssi_version='SSI2')
        at(3690,action='submit',route='payment',amount=(6 if retained else 5)*M,key='u1',obligation_id='U1',version=1,account='P-USD',ssi_version='SSI2')
        at(3720,action='submit',route='redeem',amount=6*M,key='redeem')
        at(10000,action='handover',text='All obligations have been discharged without repo or standby borrowing. Restrictions remain in place. Treasury riskbook contains per-agreement VM and source references. Treasury is the incoming duty owner.')
        desk.advance(10800)
        return desk.export()
    # Approval is nonbinding and free. Do not size it using the future market move.
    approval=at(60,action='approval',route='line',amount=12*M)
    desk.advance(1200)
    expected=reconstruct(files)
    at(1740,action='risk_report',forecast_by_csa=expected['forecast_by_csa'],available_cash_minor=expected['available_cash_minor'],
       as_of_s=1200,sources=['positions/trade_register.csv','agreements/CSA-pack.md','agreements/margin_collateral.csv','inbox/MB-025.csv','bank/restrictions.csv'])
    repo=at(2160,action='quote',route='repo')
    debt=at(2190,action='submit',route='repo',amount=204*M//10,key='repo',quote_id=repo['id'],
            collateral=[{'lot_id':'G1','market_value_minor':(16 if retained else 17)*M},
                        {'lot_id':'G2','market_value_minor':(8 if retained else 7)*M}])
    line=at(2220,action='submit',route='line',amount=amount,key='line',approval_id=approval['id'])
    sale=at(2790,action='quote',route='sale')
    at(2820,action='submit',route='sale',amount=4*M,key='sale',quote_id=sale['id'])
    fx=at(2850,action='quote',route='fx_b')
    at(2880,action='submit',route='fx_b',amount=7*M,key='fx',quote_id=fx['id'])
    at(2910,action='submit',route='payment',amount=3*M,key='c1',obligation_id='C1',version=1,account='P-GBP',ssi_version='SSI2')
    at(2940,action='submit',route='early_redeem',amount=4*M,key='early')
    at(3330,action='reconcile',order_id=debt['id'],account='P-GBP',reference='CR-'+debt['id'])
    at(3630,action='submit',route='payment',amount=expected['total_minor'],key='m1',obligation_id='M1',version=1,account='P-GBP',ssi_version='SSI2')
    at(3660,action='submit',route='payment',amount=(9 if retained else 8)*M,key='m2',obligation_id='M2',version=1,account='P-GBP',ssi_version='SSI2')
    at(3690,action='submit',route='payment',amount=(6 if retained else 5)*M,key='u1',obligation_id='U1',version=1,account='P-USD',ssi_version='SSI2')
    at(3720,action='submit',route='redeem',amount=6*M,key='redeem')
    at(7300,action='submit',route='repay',amount=debt['credit'],key='repay-repo',debt_id=debt['id'])
    at(7330,action='submit',route='repay',amount=line['credit'],key='repay-line',debt_id=line['id'])
    at(10000,action='handover',text='All published calls are paid. Repo and standby principals were repaid. Cash restrictions remain in place; positions and collateral sources are linked in the riskbook. Treasury assumes the incoming duty.')
    desk.advance(10800)
    return desk.export()
