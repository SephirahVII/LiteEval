"""Fail early on incomplete Agent/Evaluator configuration."""
import math
import re
from .security import checked_url,validate_no_secrets

def number(value,name,low,high):
    if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:
        raise ValueError(f'{name} must be {low}..{high}')

def validate_agent(config):
    validate_no_secrets(config)
    number(config.get('timeout_seconds',30),'timeout_seconds',0.05,3600)
    if config.get('type')=='http':
        checked_url(config.get('endpoint',''))
        number(config.get('timeout_seconds',30),'HTTP timeout_seconds',0.05,300)
        if config.get('auth') and (config['auth'].get('type')!='bearer' or not config['auth'].get('token_env')):raise ValueError('Bearer auth requires token_env')
        for name in config.get('headers',{}):
            if name.lower() in {'authorization','cookie','proxy-authorization'}:raise ValueError('Use headers_env/auth token_env for credentials')
    elif config.get('type')!='codex':raise ValueError('Unknown Agent type')
    env=config.get('environment',{})
    if env.get('type','none') not in {'none','workspace','docker'}:raise ValueError('Unknown environment type')
    if config.get('type')=='codex' and env.get('type') not in {'workspace','docker'}:raise ValueError('Codex needs workspace or docker environment')
    if env.get('type')=='docker':
        image=env.get('image')
        if not isinstance(image,str) or not image or image.startswith('-'):raise ValueError('Docker environment needs image')
        number(env.get('cpus',1),'cpus',0.1,16)
        if env.get('network','none') not in {'none','bridge'}:raise ValueError('Docker network must be none or bridge')
        if not re.fullmatch(r'[1-9][0-9]*(?:[kmgKMG])?',str(env.get('memory','1g'))):raise ValueError('Invalid memory limit')
    for test in env.get('validation_tests',[]):
        if not isinstance(test.get('name'),str) or not isinstance(test.get('command'),list) or not test['command'] or not all(isinstance(v,str) for v in test['command']):raise ValueError('Validation tests require name and command argv')
        number(test.get('timeout_seconds',30),'test timeout',0.05,120)

def validate_evaluator(kind,config):
    validate_no_secrets(config)
    needs={'regex':['pattern'],'json_schema':['schema'],'tool_called':['tool_name'],'tool_parameter':['tool_name'],
           'latency':['max_ms'],'cost':['max_cost','currency'],'file_exists':['path'],'file_content':['path'],
           'test_passed':['test_name'],'llm_judge':['base_url','model','rubric']}
    for name in needs.get(kind,[]):
        if name not in config:raise ValueError(f'{kind} requires {name}')
    if kind=='llm_judge':
        checked_url(config['base_url']);number(config.get('pass_threshold',1),'pass_threshold',0,1)
        number(config.get('timeout_seconds',30),'Judge timeout',0.05,300)
        if config.get('scale','binary') not in {'binary','continuous'}:raise ValueError('Invalid Judge scale')
        if config.get('direction','higher') not in {'higher','lower'}:raise ValueError('Invalid score direction')
        if not isinstance(config['generation_parameters'] if 'generation_parameters' in config else {},dict):raise ValueError('generation_parameters must be an object')
        if set(config.get('generation_parameters',{}))-{'temperature','max_tokens','max_completion_tokens','top_p','seed'}:raise ValueError('Unsupported generation parameter')
    if kind=='regex':
        if len(config['pattern'])>500:raise ValueError('Regex too long')
        re.compile(config['pattern'])
    if kind=='json_schema':
        from .evaluators import check_schema
        check_schema(config['schema'])
    if kind in {'file_exists','file_content'}:
        from .environments import relative_path
        relative_path(config['path'])
    if kind=='latency':number(config['max_ms'],'max_ms',0,3600000)
    if kind=='cost':number(config['max_cost'],'max_cost',0,1000000)
