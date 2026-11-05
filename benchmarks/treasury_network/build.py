"""Build immutable native packages for sequential calibration and retained runs."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
from .workflow.resources import CollateralBook

ROOT=Path(__file__).resolve().parent


def write(root,name,text):
    path=root/name
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(text if isinstance(text,str) else json.dumps(text,indent=2),encoding='utf-8',newline='\n')


def build(output,level,variant='calibration'):
    output=Path(output)
    if output.exists():
        raise ValueError('Refusing to overwrite a frozen package: '+str(output))
    if level not in range(1,9) or variant not in ('calibration','retained'):
        raise ValueError('Invalid level/variant')
    output.mkdir(parents=True)
    retained=variant=='retained'
    weights={'payments':30,'buffer':15,'debt_close':30,'economics':25} if level>=4 else {'payments':30,'buffer':15,'debt_close':25,'economics':25,'handover':5}
    if level>=6:
        weights={'payments':25,'buffer':15,'debt_close':20,'covenant':40}
    if level>=7:
        weights={'payments':25,'buffer':15,'debt_close':20,'joint_funding':40}
    task=output/'tasks'/'treasury-duty'
    public=task/'environment'/'initial_workspace'
    prefix=f'0.{level}.'+('1' if level==7 else '0')+('-retained' if variant=='retained' else '')
    write(output,'dataset.toml',f'''schema_version = 1
[benchmark]
id = "treasury-network"
version = "{prefix}"
name = "Treasury network stress challenge"
description = "Versioned multicurrency liquidity and settlement workflow"
[tasks]
include = ["tasks/*/task.toml"]
[defaults]
environment_profile = "local"
target_timeout_seconds = 2400
judge_timeout_seconds = 30
[environment_profiles.local]
type = "workspace"
[harness_requirements]
target_capabilities = ["agent.action_json"]
''')
    write(task,'prompt.md','''You are the treasury operator for Fund P on today's 08:30–11:30 duty.
Maintain the desk mandate, discharge Fund P's obligations, and leave an auditable handover.
You may inspect public records and use the authorized treasury operations provided by the tools.
The workspace entry point is desk/index.md. This is a synthetic financial exercise.
''')
    write(public,'desk/mandate.md','''Fund P and Fund Q are distinct legal persons. You operate P only; Q cash is not P liquidity.
Fund P must discharge published GBP and USD obligations by their stated deadlines. Maintain at least GBP5,000,000 available after each GBP margin settlement.
Fund P's restricted cash is unavailable throughout this shift. Do not borrow against it.
Use authorized routes under their contractual capacity and collateral rules. Avoid unnecessary financing expense and retain USD needed for P's obligations.
Close today's repo and standby financing principals at their contractual maturities, and release the collateral. Fees are invoiced after this exercise; do not subtract them from intraday principal cash.
The incoming operator needs an accurate record of outstanding obligations, balances, financing, collateral and unresolved operations.
''')
    write(public,'bank/opening.csv','account,entity,currency,book_minor,restricted_minor,as_of\nP-GBP,P,GBP,2000000000,600000000,08:30\nP-USD,P,USD,1200000000,0,08:30\nQ-GBP,Q,GBP,1500000000,0,08:30\n')
    if retained:
        write(public,'bank/opening.csv','account,entity,currency,book_minor,restricted_minor,as_of\nP-GBP,P,GBP,2100000000,700000000,08:30\nP-USD,P,USD,1300000000,0,08:30\nQ-GBP,Q,GBP,1500000000,0,08:30\n')
    write(public,'bank/expected-inflows.csv','account,currency,amount_minor,settlement_time,reference\nP-GBP,GBP,2300000000,10:40,BOND-REDEMPTION\nP-GBP,GBP,1200000000,11:05,COUPON-SETTLEMENT\nQ-GBP,GBP,900000000,09:20,Q-SUBSCRIPTION\n')
    if level>=6:
        with (public/'bank/expected-inflows.csv').open('a',encoding='utf-8') as stream:
            stream.write(f'P-GBP,GBP,{300000000 if retained else 200000000},10:20,INTEREST-RECEIPT\n')
        write(public,'desk/limits.md',f'''Fund P internal committed financing ceiling: GBP{26 if retained else 25},000,000 from 09:28 through this shift. This aggregates accepted repo and standby B principal, including pending and unallocated receipts, until principal is actually repaid. Approval alone is not a drawing. External dealers apply their own route limits and do not enforce P's aggregate internal covenant. A breach exceeding GBP2,000,000 is material; smaller excess is reported proportionately.\n''')
    write(public,'operations/authority.md','''P's treasury operator is authorized to repo P's eligible unencumbered gilts, convert P's currency, sell P's short bills, redeem P's money fund, request and draw B, pay obligations and repay debt.
Q-to-P interfund loans require both boards' approvals. The boards do not meet before 09:50. No interfund route is authorized during the first margin window.
The system rejects orders outside authority, capacity and cutoffs. Queryable accounts are authoritative as to current cash. Business terms are in each route's contract.
''')
    write(public,'funding/repo-A.md','''Repo A: GBP proceeds against Fund P's eligible unencumbered gilts. Firm quotes available from 09:06, valid for 180s. Cash principal ceiling is eligible market value times (1-haircut); quotations may change with valuation. Multiple drawings share this ceiling.
Submit before 09:15; settlement 09:25. Principal due 11:00. Fee 5bps of cash principal. For repayment use route repay and the settled debt's order id; submit before maturity. Repayment releases pledged collateral. No automatic repayment.
''')
    write(public,'funding/standby-B.md','''Standby B: GBP12m total principal limit, P only. Approval takes eight business minutes. Approvals authorize a principal amount and are consumed by drawings; they do not deliver cash. Drawings must be submitted before 09:20 and settle 09:28. Principal due 11:10. Fee 25bps. Repay using the settled debt order id before maturity. No automatic repayment.
''')
    write(public,'funding/fx-desk.md','''P may sell USD for GBP. Quotes give GBP minor units per USD minor unit; current rate 0.80. Firm quotes valid 180s. Submit before 09:15. USD is reserved when accepted, debited only at 09:30 settlement. GBP is credited at 09:30 if matching succeeds. Failed matching releases the USD reservation and delivers no GBP. Match outcome may arrive before settlement. Fee 1bp of GBP credit. Pending matched amounts are not bank cash.
''')
    write(public,'funding/asset-liquidity.md','''Fund P short bills: separate unencumbered asset pool, GBP4m market value. Sell with firm quote, net proceeds 98% of market value submitted. Cutoff 09:27, settlement 09:35; fee 3bps of cash credit. This is an asset disposal, not financing debt.
P money fund: GBP10m redemption capacity. Submit before 09:55, settlement 10:15. Fee 3bps of cash credit; no debt repayment obligation.
''')
    if level>=6:
        with (public/'funding/asset-liquidity.md').open('a',encoding='utf-8') as stream:
            stream.write('Money fund early window: route early_redeem, open 09:10, cutoff 09:24, cash settlement 09:36, cap GBP4m, fee5bps. Both windows redeem the same opening GBP10m position; each accepted redemption consumes that shared position.\n')
    if level>=5:
        with (public/'funding/repo-A.md').open('a',encoding='utf-8') as stream:
            stream.write('Repo collateral maintenance: authenticated cash collateral calls are separate payment obligations. Posted cash is restricted until all repo principal has been repaid; it is then returned to P-GBP. A maintenance payment does not reduce repo principal.\n')
        write(public,'funding/fx-B.md','''Dealer B cash FX window: quotes from 09:17, orders before 09:25, settlement 09:38. Route fx_b sells P USD for GBP at quoted rate, currently 0.76 GBP per USD. Fee 8bps of GBP cash credit. Accepted orders reserve the USD and debit it on settlement. This venue has independent matching from the morning A window; there is no automatic transfer of orders between venues.\n''')
    write(public,'operations/payments.md','''query obligations lists currently published payment instructions, their version, account, amount and due time. Notices amend the obligation by id and version; delivery timestamp alone does not establish precedence. A queued payment is immutable; cancellation and replacement are supported. At due time, only a queued instruction matching the then-current obligation and settlement instruction version may settle. Available cash is checked at due time, not when queued.
SSI1 is the current settlement instruction set for P payments at opening. An authenticated SSI amendment, if delivered, supersedes that set for unsettled instructions. operations/ssi-amendment.json is published only when applicable.
''')
    write(public,'operations/bank-allocation.md','''query orders gives trade status. A trade sent by a counterparty is not necessarily available bank cash. Receipts with incomplete beneficiary-account matching sit in suspense. query suspense returns bank reference, currency, beneficiary entity, amount, remitter and related trade id. Reconcile may allocate a receipt to the matching entity/currency account using its exact bank reference. An allocated receipt becomes settled financing with the original contractual maturity. No write access to accounts is permitted.
''')
    write(public,'collateral/opening.csv','lot,owner,market_value_minor,eligible,encumbered,as_of\nG1,P,1700000000,true,false,08:30\nG2,P,1200000000,true,false,08:30\nG3,P,900000000,true,true,08:30\nG4,Q,2200000000,true,false,08:30\n')
    write(public,'collateral/haircuts.json',{'as_of_s':0,'version':'V1','haircut_bps':1000,'eligible_owner':'P','encumbered_assets_eligible':False})
    write(public,'collateral/revaluation-0905.json',{'as_of_s':2100,'version':'V2','eligible_unencumbered_value_minor':2400000000,'haircut_bps':1500})
    if level>=7:
        write(public,'funding/repo-A.md','''Repo A lends GBP against the eligible P-owned securities described in collateral/rules.md. The lender cash principal limit is GBP30m, independently of the allocated collateral lending value and P internal financing limit. Quote returns per-class haircuts and does not select lots. Multiple drawings share the lender limit and the same asset register.
Firm quotes open 09:06 and expire after180s. Submit before09:15; settlement09:25. Principal due11:00. Fee5bps of cash principal. Repayment orders must be submitted before maturity. Principal repayment releases its allocated securities; all repo principal repayment releases any posted cash collateral. There is no automatic repayment. Cash collateral maintenance calls are separate authenticated payment obligations and do not reduce principal.\n''')
        authority=(public/'operations/authority.md').read_text(encoding='utf-8').replace("repo P's eligible unencumbered gilts","repo P's eligible securities under the collateral agreement")
        write(public,'operations/authority.md',authority)
        book=CollateralBook(retained)
        stream=io.StringIO(newline='')
        writer=csv.DictWriter(stream,fieldnames=list(book.snapshot()[0]))
        writer.writeheader();writer.writerows(book.snapshot())
        write(public,'collateral/opening.csv',stream.getvalue())
        write(public,'collateral/rules.md','''Repo A accepts settled, unmatured GBP government_bond and short_bill lots owned by P. External encumbrance, prior repo allocation and disposal consume the same lot. Haircuts are by asset class and may be updated. The lender's maximum cash limit is separate from collateral lending value and P's internal aggregate financing limit. Quote and submit do not select or optimize lots on your behalf. In repo submit, collateral is a list of lot_id and market_value_minor. Sale disposes T1. All money fields in the register are currency minor units.\n''')
        write(public,'collateral/revaluation-0905.json',{'as_of_s':2100,'version':'V2','lot_market_values_minor':{'G1':(16 if retained else 17)*100000000,'G2':(8 if retained else 7)*100000000},'haircuts_bps':{'government_bond':1500,'short_bill':500}})
    if level>=8:
        for name in ('repo-A.md','standby-B.md'):
            with (public/'funding'/name).open('a',encoding='utf-8') as stream:
                stream.write('Servicing: the bank accepts full-principal repayment instructions into its10:35 batch. Submit before10:35 for today\'s stated maturity; settlement occurs at that maturity against then-available cash, not at instruction acceptance. Early cash payment and partial principal repayment are unavailable. Accepted funding trades are firm.\n')
    write(public,'risk/forecast-open.json',{'as_of_s':0,'entity':'P','currency':'GBP','estimate_minor':800000000,'due_s':4200,'status':'estimate'})
    write(public,'risk/forecast-0850.json',{'as_of_s':1200,'entity':'P','currency':'GBP','estimate_minor':3800000000,'due_s':4200,'status':'estimate'})
    write(public,'risk/delayed-estimate.json',{'as_of_s':1500,'delivered_s':2460,'entity':'P','currency':'GBP','estimate_minor':3900000000,'status':'estimate','source':'risk-cache'})
    write(public,'operations/ssi-amendment.json',{'authenticated':True,'published_s':2640,'effective_s':2640,'entity':'P','currencies':['GBP','USD'],'supersedes':'SSI1','current':'SSI2','reference':'CUSTODIAN-SIGNED-44'})
    write(public,'operations/bank-0925.csv','batch,reference,currency,beneficiary,remitter,posting_status\nCR-0925,see-query-suspense,GBP,P,REPO-A,booked-unallocated\n')
    for i in range(1,10):
        digest=f'''Market digest {i}, internal distribution. Source: synthetic news desk.
Gilt volatility remains elevated. Dealer indications are not cash receipts or payment instructions.
Fund Q reported GBP15m at opening; its independent board is reviewing investment allocations.
Commodity clients report larger USD settlements; this bulletin does not amend Fund P's obligations.
'''
        if level>=3:
            digest=f'''Markets and client bulletin {i}; distribution: treasury coverage desk.
08:{30+i:02} gilt index {101.8-i*.63:.2f}, bid/ask {10+i} ticks. Indicative agency haircut range 10–18%.
Client Q recap: gross GBP liquidity 15m; portfolio derivative estimate GBP{9+i}m; board session 09:50.
Client R update: custodian receipt R-AM-{i:02} reported GBP{4+i}m subject to allocation; short bill quotes 99.0/99.5 from another dealer.
USD desk: aggregate client demand USD{20+i}m. Coverage office recommends that desks validate their own current mandates and contracts.
Source timestamps vary by venue; desk distribution time is not an execution timestamp.
'''
        write(public,f'news/digest-{i:02}.txt',digest)
    for i in range(1,1+level*4):
        write(public,f'archive/fund-Q-report-{i:02}.csv',f'entity,report_date,currency,gross_asset_minor,estimated_margin_minor\nQ,previous-day-{i},GBP,{(50+i)*100000000},{(3+i)*100000000}\n')
    release_times={'collateral/revaluation-0905.json':2100,'risk/forecast-0850.json':1200,
                   'risk/delayed-estimate.json':2460 if level>=2 else 99999,
                   'operations/ssi-amendment.json':2640 if level>=5 else 99999,
                   'operations/bank-0925.csv':3300 if level>=4 else 99999}
    release_times.update({f'news/digest-{i+1:02}.txt':420+i*840 for i in range(9)})
    inventory=['# Duty workspace','', 'Current business mandate: desk/mandate.md.',
               'Bank balances/inflows: bank/. Route contracts: funding/. Authority, payment and bank operating contracts: operations/.',
               'Collateral register: collateral/. Forecast estimates: risk/. Historical independent Fund Q reports: archive/. Market digests: news/.',
               'Files bearing a future publication time are unavailable until released. Query tools provide current operational state.', '', '## Inventory']
    inventory.extend('- '+p.relative_to(public).as_posix() for p in sorted(public.rglob('*')) if p.is_file())
    write(public,'desk/index.md','\n'.join(inventory)+'\n')
    fixture={'level':level,'variant':variant,'release_times':release_times}
    write(task,'workflow-fixtures/scenario.json',fixture)
    reference_fee=(4650800 if retained else 4560000) if level>=5 else ((2846000 if level==1 else 3846000 if level==2 else 4407600) if retained else (2566000 if level==1 else 3566000 if level==2 else 4377600))
    if level>=6:
        reference_fee=3343200 if retained else 3093200
    write(task,'judge/ground_truth.json',{'level':level,'buffer_minor':500000000,'expected_obligations':['M1','U1','M2']+(['C1'] if 5<=level<7 else []),
          'conditional_obligations':['C1'] if level>=7 else [],
          'opening_balances':{'P-GBP':2100000000 if retained else 2000000000,'P-USD':1300000000 if retained else 1200000000,'Q-GBP':1500000000},
          'opening_restricted':{'P-GBP':700000000 if retained else 600000000,'P-USD':0,'Q-GBP':0},
          'reference_fee_minor':reference_fee,
          'financing_limit_minor':(2600000000 if retained else 2500000000) if level>=6 else None,
          'financing_materiality_minor':200000000,'weights':weights})
    for name in ('engine.py','controller.py','resources.py','harness.toml'):
        write(output,'workflow/'+name,(ROOT/'workflow'/name).read_text(encoding='utf-8'))
    write(task,'judge/verify.py',(ROOT/'verify.py').read_text(encoding='utf-8'))
    spec='''schema_version = 1
[task]
id = "treasury-duty"
title = "Fund P treasury duty"
category = "financial-stress-challenge"
[input]
prompt_file = "prompt.md"
[environment]
initial_workspace = "environment/initial_workspace"
[[artifacts]]
path = "evidence/treasury.json"
required = true
max_bytes = 4000000
[[artifacts]]
path = "handover.md"
required = false
[workflow]
id = "benchmark.treasury-network"
fixture_file = "workflow-fixtures/scenario.json"
outputs = ["evidence/treasury.json", "handover.md"]
[judge]
ground_truth_file = "judge/ground_truth.json"
[judge.aggregate]
method = "weighted_mean"
pass_threshold = 0.95
'''
    for name,weight in weights.items():
        spec+=f'''\n[[judge.checks]]
id = "{name}"
type = "command"
entrypoint = "judge/verify.py"
command = ["{{python}}", "/judge/verify.py", "--scenario", "/judge/ground_truth.json", "--submission", "/submission", "--result", "/judge-output/result.json", "--check", "{name}"]
weight = {weight}
must_pass = false
'''
    write(task,'task.toml',spec)
    files={p.relative_to(output).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.rglob('*')) if p.is_file()}
    write(output,'freeze.json',{'level':level,'variant':variant,'files':files})
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('output',type=Path)
    parser.add_argument('--level',type=int,required=True)
    parser.add_argument('--variant',default='calibration')
    args=parser.parse_args()
    print(build(args.output,args.level,args.variant))
