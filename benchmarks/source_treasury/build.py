"""Generate a native source-reconstruction case over validated settlement primitives."""
import argparse
import hashlib
import json
from pathlib import Path

from benchmarks.treasury_network.build import build as operational_build,write
from .authoring import sources,reconstruct

ROOT=Path(__file__).resolve().parent
WEIGHTS={'risk_reconstruction':40,'joint_funding':30,'payments':15,'buffer':5,'funding_control':10}
VERSION='0.3.0'


def build(output,variant='calibration',scenario='stress'):
    output=operational_build(output,8,variant)
    task=output/'tasks/treasury-duty'
    public=task/'environment/initial_workspace'
    retained=variant=='retained'
    source_files=sources(retained,scenario)
    expected=reconstruct(source_files)
    assert expected['total_minor']==((45 if retained else 44) if scenario=='stress' else (9 if retained else 8))*100_000_000
    assert expected['available_cash_minor']==1_400_000_000
    for name,body in source_files.items():
        write(public,name,body)
    for name in ('risk/forecast-0850.json','risk/delayed-estimate.json'):
        (public/name).unlink()
    prompt='''You are the treasury operator for Fund P on today's08:30–11:30 duty.
Maintain the desk mandate and live treasury riskbook, discharge P's obligations and leave an auditable handover.
You may inspect public records and use the authorized treasury operations and data services.
The workspace entry point is desk/index.md. This is a synthetic financial exercise.
'''
    write(task,'prompt.md',prompt)
    with (public/'desk/mandate.md').open('a',encoding='utf-8') as stream:
        stream.write('The treasury riskbook is a desk deliverable throughout the shift. It records expected additional cash VM by agreement, usable GBP cash, the business as-of time and evidence references. Estimates are distinct from instructions to pay.\n')
    paths=[p.relative_to(public).as_posix() for p in sorted(public.rglob('*')) if p.is_file() and p.relative_to(public).as_posix()!='desk/index.md']
    write(public,'desk/index.md','''# Fund P duty workspace
Mandate: desk/mandate.md. Current operational records are available through query; published CSV schemas through query(catalog).
Documents cover bank records, positions, agreement schedules, collateral, treasury contracts and operations. News and historical reports share the workspace.
Some records are published during duty. File delivery and business effective times are separate attributes.

## Inventory
'''+''.join('- '+name+'\n' for name in paths))
    fixture=json.loads((task/'workflow-fixtures/scenario.json').read_text(encoding='utf-8'))
    fixture.update(level=9,m1_amount_minor=expected['total_minor'],scenario=scenario)
    fixture['release_times']['inbox/MB-025.csv']=1200
    for name in ('risk/forecast-0850.json','risk/delayed-estimate.json'):
        fixture['release_times'].pop(name,None)
    write(task,'workflow-fixtures/scenario.json',fixture)
    truth=json.loads((task/'judge/ground_truth.json').read_text(encoding='utf-8'))
    truth.update(level=9,weights=WEIGHTS,risk_expected=expected,scenario=scenario,
                 prefunding_required=scenario=='stress',public_paths=paths+['desk/index.md'])
    if scenario!='stress':
        truth['reference_fee_minor']=180000
    write(task,'judge/ground_truth.json',truth)
    write(task,'judge/base_verify.py',(output/'tasks/treasury-duty/judge/verify.py').read_text(encoding='utf-8'))
    write(task,'judge/verify.py',(ROOT/'verify.py').read_text(encoding='utf-8'))
    manifest=(output/'dataset.toml').read_text(encoding='utf-8').replace('id = "treasury-network"','id = "source-treasury"')
    identity=VERSION+('-'+scenario if scenario!='stress' else '')+('-retained' if retained else '')
    manifest=manifest.replace('version = "0.8.0'+('-retained' if retained else '')+'"','version = "'+identity+'"')
    manifest=manifest.replace('Treasury network stress challenge','Source-based treasury challenge')
    write(output,'dataset.toml',manifest)
    harness=(output/'workflow/harness.toml').read_text(encoding='utf-8').replace('benchmark.treasury-network','benchmark.source-treasury').replace('controller.py','source_controller.py')
    write(output,'workflow/harness.toml',harness)
    for name in ('source_controller.py','source_engine.py','data_view.py','calculator.py'):
        write(output,'workflow/'+name,(ROOT/'workflow'/name).read_text(encoding='utf-8'))
    spec=(task/'task.toml').read_text(encoding='utf-8').split('[[judge.checks]]')[0]
    spec=spec.replace('id = "treasury-duty"','id = "source-treasury-duty"').replace('benchmark.treasury-network','benchmark.source-treasury')
    spec=spec.replace('[input]',f'[task.metadata]\nscenario = "{scenario}"\ncase_type = "'+('challenge_candidate' if scenario=='stress' else 'control')+'"\n[input]')
    for name,weight in WEIGHTS.items():
        spec+=f'''\n[[judge.checks]]
id = "{name}"
type = "command"
entrypoint = "judge/verify.py"
command = ["{{python}}", "/judge/verify.py", "--scenario", "/judge/ground_truth.json", "--submission", "/submission", "--result", "/judge-output/result.json", "--check", "{name}"]
weight = {weight}
must_pass = false
'''
    write(task,'task.toml',spec)
    files={p.relative_to(output).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.rglob('*')) if p.is_file() and p.name!='freeze.json'}
    write(output,'freeze.json',{'case_family':'source-treasury','version':VERSION,'scenario':scenario,'variant':variant,'files':files})
    return output


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('output',type=Path)
    p.add_argument('--variant',choices=('calibration','retained'),default='calibration')
    p.add_argument('--scenario',choices=('stress','quiet','hedged'),default='stress')
    a=p.parse_args()
    print(build(a.output,a.variant,a.scenario))
