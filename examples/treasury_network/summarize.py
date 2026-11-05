"""Produce a shareable calibration ledger without model credentials or raw CoT."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import statistics
import tomllib

from liteeval.bootstrap import benchmark_service
from liteeval.infrastructure.persistence.store import Store


def summarize(source,output):
    if not (source/'liteeval.db').exists():
        raise ValueError('Calibration database does not exist')
    output.mkdir(parents=True,exist_ok=True)
    service=benchmark_service(Store(source/'liteeval.db'))
    rows=[]
    for path in sorted(source.glob('*/report.json')):
        report=json.loads(path.read_text(encoding='utf-8'))
        execution=service.repository.execution(report['project_id'],report['execution_id'])
        plan=execution['plan']
        task=next(iter(report['result']['tasks']),{'grading':{'score':None,'outcome':'unscored'},'checks':[],
                                                 'error':report.get('invalidation','Cancelled before a business result')})
        package=Path(report['package'])
        version=tomllib.loads((package/'dataset.toml').read_text(encoding='utf-8'))['benchmark']['version']
        frozen=json.loads((package/'freeze.json').read_text(encoding='utf-8'))
        unchanged=all(hashlib.sha256((package/name).read_bytes()).hexdigest()==sha for name,sha in frozen['files'].items())
        checks={c['check_id']:c.get('score') for c in task['checks']}
        evidence_path=path.parent/'submission/evidence/treasury.json'
        actions=sum(e['kind']=='workflow_event' and e['payload'].get('kind')=='treasury_action' for e in execution['events'])
        trace_name=None
        if evidence_path.exists():
            evidence=json.loads(evidence_path.read_text(encoding='utf-8'))
            actions=sum(r['kind']=='action' for r in evidence['journal'])
            trace_name='traces/'+path.parent.name+'.json'
            dest=output/trace_name
            dest.parent.mkdir(exist_ok=True)
            fields=[]
            for key,value in evidence.items():
                encoded=('['+'\n'+',\n'.join(json.dumps(row,ensure_ascii=False,separators=(',',':')) for row in value)+'\n]') if key=='journal' else json.dumps(value,ensure_ascii=False,separators=(',',':'))
                fields.append(json.dumps(key)+':'+encoded)
            body='{\n'+',\n'.join(fields)+'\n}\n'
            if dest.exists() and json.loads(dest.read_text(encoding='utf-8'))!=evidence:
                raise ValueError('Refusing to change a previously exported trajectory')
            dest.write_text(body,encoding='utf-8')
        integrity_path=path.parent/'integrity.json'
        integrity=json.loads(integrity_path.read_text(encoding='utf-8')) if integrity_path.exists() else {}
        row={'label':report['label'],'level':report['round'],'case_version':version,'phase':report['phase'],'model':report['model'],
             'score':task['grading']['score'],'outcome':task['grading']['outcome'],'checks':checks,
             'weights':{c['check_id']:c.get('weight') for c in task['checks']},
             'metrics':next((c['metrics'] for c in task['checks'] if 'metrics' in c),{}),
             'error':task['error'],'execution_id':report['execution_id'],'release_digest':report['release_digest'],
             'agent_digest':plan['agent_harness']['digest'],'agent_version':plan['agent_harness']['spec']['definition']['harness']['version'],
             'evaluation_digest':plan['evaluation_harness']['digest'],'workflow_digest':(plan.get('workflow') or {}).get('digest'),
             'parameters':report['parameters'],'actions':actions,'usage':report['result']['usage'],
             'wall_seconds':report['wall_seconds'],'action_budget':224 if report['round']>=6 else 160,
             'raw_responses':sum(e['kind']=='model_response_raw' for e in execution['events']),
             'package_unchanged':unchanged,'integrity':integrity,
             'trace':trace_name}
        rows.append(row)
    rows.sort(key=lambda r:(r['level'],r['label']))
    groups=defaultdict(list)
    for r in rows:
        if r['score'] is not None:
            groups[(r['phase'],r['model'],r['release_digest'],r['agent_digest'],r['evaluation_digest'],r['workflow_digest'],json.dumps(r['parameters'],sort_keys=True))].append(r['score']*100)
    stats=[{'phase':k[0],'model':k[1],'release_digest':k[2],'agent_digest':k[3],
            'evaluation_digest':k[4],'workflow_digest':k[5],'parameters':json.loads(k[6]),'n':len(v),'mean':statistics.mean(v),'sample_stddev':statistics.stdev(v) if len(v)>1 else None,
            'sample_variance':statistics.variance(v) if len(v)>1 else None,
            'min':min(v),'max':max(v),'scores':v} for k,v in groups.items()]
    data={'schema_version':1,'runs':rows,'fixed_protocol_groups':stats}
    (output/'results.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# Treasury network 真实校准记录','',
           '模型通过用户已有的 MiniMax API 连接调用。业务时间回放与真实 API 耗时分开记录；前五级最多160次动作，第六级起224次。',
           '表中分数均为原始冻结版本的分数，环境失败保持未评分；版本之间不合并计算难度均值。',
           '第一轮存在 repo 报价歧义；第三轮初次运行遇到 TLS 中断，第一次重跑遇到重试事件协议错误；第四轮初次运行遇到429。原记录全部保留。',
           '第4–5级修正净缓冲和交接存在性评分；第6级发现消极不融资仍可获40分的漏洞；第7级改为联合资金可行性，0.7.1再修正未触发义务的分母与契约口径。旧分数保留，不能据此挑选低分宣称达标。', '',
           '当前0.8.0使用联合资金40、付款25、缓冲15、偿债20，费用单独报告。以下先列同版、同Agent/Workflow/Evaluator摘要及参数的重复结果。', '',
           '| 模型 | 重复次数 | 原始分数 /100 | 均分 | 样本标准差 |',
           '| --- | ---: | --- | ---: | ---: |']
    for g in stats:
        if g['phase']=='calibration' and g['n']>1:
            values=', '.join(f'{score:.2f}' for score in g['scores'])
            lines.append(f"| {g['model']} | {g['n']} | {values} | {g['mean']:.2f} | {g['sample_stddev']:.2f} |")
    lines+=['','0.8.0校准未达到30–40分目标；保留变体没有用于补挑低分。下一轮原始证据与协议推导设计见 [作者侧草案](../../product/financial-source-risk-case-design.md)。','',
           '| 运行 | 题版 | 模型 | 分数 /100 | 动作 | API用量记录 | 实际秒数 | 轨迹 |',
           '| --- | --- | --- | ---: | ---: | ---: | ---: | --- |']
    for r in rows:
        score='未评分' if r['score'] is None else f"{r['score']*100:.2f}"
        trace=f"[账本]({r['trace']})" if r['trace'] else '无完整产物'
        lines.append(f"| {r['label']} | {r['case_version']} | {r['model']} | {score} | {r['actions']} | {r['usage']['record_count']} | {r['wall_seconds']:.2f} | {trace} |")
    lines+=['','动作数包含被拒的工具请求；API用量记录还可能包含未完成请求和传输重试。原始第五轮触及8MB进程输出上限，修复为工作区会话历史加摘要句柄后使用同一题包补跑。',
            '完整请求/响应、模型返回的 reasoning、工具回执、发布快照及其摘要在本地 `data/treasury-calibration/<运行>/execution.zip`。本目录仅发布合成业务账本和脱敏统计。',
            'input_tokens 包含缓存命中部分；output_tokens 包含供应商报告的 reasoning 部分。两者不能再次相加计算总量。供应商未报告 API 费用时保持未知；账本里的 GBP fee 是仿真融资成本。',
            '所有级别均由作者侧参考路径验证100分。当前均为一个场景家族；只有最终同题包、同Harness摘要、同参数的重复及未参与改题的情景验证，才能支持30–40分难度声明。','']
    (output/'README.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({'runs':len(rows),'graded':sum(r['score'] is not None for r in rows),'output':str(output.resolve()),
                      'repeated_groups':[{'model':g['model'],'phase':g['phase'],'n':g['n'],'mean':g['mean'],'sample_stddev':g['sample_stddev']} for g in stats if g['n']>1]},ensure_ascii=True))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,default=Path('data/treasury-calibration'))
    p.add_argument('--output',type=Path,default=Path('docs/examples/treasury-network'))
    a=p.parse_args()
    summarize(a.input,a.output)
