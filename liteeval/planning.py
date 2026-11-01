"""Side-effect-free experiment preflight and shared immutable input resolution."""
import json
import re
from . import catalog, runtime
from .adapters import select
from .config import number, validate_agent, validate_evaluator
from .storage import Conflict, digest


class PreflightError(ValueError):
    def __init__(self, report):
        self.report = report
        super().__init__('Preflight blocked this experiment; fix evidence or explicitly request a diagnostic run')


class StalePreflight(Conflict):
    def __init__(self, report):
        self.report = report
        super().__init__('Preflight inputs changed; review the current preflight and resubmit')


def normalize(body):
    allowed = {'project_id', 'dataset_id', 'dataset_version_id', 'agent_version_id', 'evaluator_ids',
               'config', 'name', 'description', 'diagnostic', 'preflight_hash'}
    if not isinstance(body, dict) or set(body)-allowed:
        raise ValueError('Unsupported experiment field')
    eids = body['evaluator_ids']
    if not isinstance(eids, list) or not eids or not all(isinstance(e, str) and e for e in eids) or len(set(eids)) != len(eids):
        raise ValueError('Provide distinct evaluator IDs')
    for field in ('project_id', 'agent_version_id'):
        if not isinstance(body[field], str) or not body[field]:raise ValueError(f'{field} must be a nonempty string')
    for field in ('dataset_id', 'dataset_version_id'):
        if body.get(field) is not None and (not isinstance(body[field], str) or not body[field]):
            raise ValueError(f'{field} must be a nonempty string')
    if bool(body.get('dataset_id')) == bool(body.get('dataset_version_id')):
        raise ValueError('Provide exactly one of dataset_id or dataset_version_id')
    conf = body.get('config')
    if conf is None:conf = {}
    if not isinstance(conf, dict) or set(conf)-{'concurrency', 'max_retries', 'judge_retries', 'retry_delay_seconds'}:
        raise ValueError('Unsupported Experiment configuration')
    number(conf.get('retry_delay_seconds', .5), 'retry_delay_seconds', 0, 30)
    for key, default, low, high in [('concurrency', 4, 1, 32), ('max_retries', 0, 0, 3), ('judge_retries', 1, 0, 3)]:
        value = conf.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'{key} must be an integer in {low}..{high}')
    for key, limit in [('name', 200), ('description', 2000)]:
        if not isinstance(body.get(key, ''), str) or len(body.get(key, '')) > limit:
            raise ValueError(f'{key} must be a string of at most {limit} characters')
    if type(body.get('diagnostic', False)) is not bool:
        raise ValueError('diagnostic must be boolean')
    check = body.get('preflight_hash')
    if check is not None and (not isinstance(check, str) or not re.fullmatch('[0-9a-f]{64}', check)):
        raise ValueError('preflight_hash must contain 64 lowercase hex characters')
    return {'project_id': body['project_id'], 'dataset_id': body.get('dataset_id'),
            'dataset_version_id': body.get('dataset_version_id'), 'agent_version_id': body['agent_version_id'],
            'evaluator_ids': list(eids), 'config': dict(conf), 'name': body.get('name', ''),
            'description': body.get('description', ''), 'diagnostic': body.get('diagnostic', False),
            'preflight_hash': check}


def resolve(db, store, request):
    dataset, version, rows = catalog.source(db, store, request['dataset_id'], request['dataset_version_id'])
    agent = store.required(db, 'agent_versions', request['agent_version_id'])
    if dataset['project_id'] != request['project_id'] or agent['project_id'] != request['project_id']:
        raise ValueError('Dataset and Agent must belong to the Project')
    agent_config = json.loads(agent['config_json'])
    validate_agent(agent_config)
    evaluators = []
    for eid in request['evaluator_ids']:
        evaluator = dict(store.required(db, 'evaluator_revisions', eid))
        store.active(evaluator)
        if evaluator['project_id'] != request['project_id']:
            raise ValueError('Evaluator belongs to a different Project')
        evaluator['config'] = json.loads(evaluator['config_json'])
        validate_evaluator(evaluator['kind'], evaluator['config'])
        evaluators.append(evaluator)
    return dataset, version, rows, agent, agent_config, evaluators


def report(db, store, request, resolved=None):
    dataset, version, rows, agent, agent_conf, evaluators = resolved or resolve(db, store, request)
    cases = [json.loads(r['content_json']) for r in rows]
    errors, warnings, coverage = [], [], []
    if not cases:
        errors.append({'code': 'empty_dataset', 'message': 'Dataset has no active Cases', 'count': 0})
    for evaluator in evaluators:
        kind, config = evaluator['kind'], evaluator['config']
        missing = []
        needs_expected = kind in {'exact_match', 'contains'} or kind == 'file_content' and 'expected' not in config
        needs_parameters = kind == 'tool_parameter'
        for case in cases:
            expected = case.get('expected')
            if 'expected_field' in config:
                try:expected = select(expected, config['expected_field'])
                except (ValueError, KeyError, IndexError, TypeError):expected = None
            if needs_expected and expected is None or needs_parameters and not isinstance(config.get('parameters', case.get('expected')), dict):
                missing.append(case['id'])
        if missing:
            errors.append({'code': 'expected_invalid' if needs_parameters else 'expected_missing',
                           'evaluator_id': evaluator['id'], 'count': len(missing), 'case_ids': missing[:20],
                           'message': 'Required expected evidence is missing or has an invalid type'})
        coverage.append({'evaluator_id': evaluator['id'], 'kind': kind, 'total': len(cases),
                         'static_evidence_ready': len(cases)-len(missing), 'requires_runtime_evidence':
                         kind not in {'exact_match', 'contains'}, 'missing_expected_count': len(missing)})
        if kind in {'file_exists', 'file_content', 'test_passed'} and agent_conf.get('environment', {}).get('type', 'none') == 'none':
            errors.append({'code': 'final_state_unavailable', 'evaluator_id': evaluator['id'],
                           'message': 'Selected Agent has no environment providing final-state evidence'})
        if kind in {'tool_called', 'tool_parameter'}:
            warnings.append({'code': 'trajectory_required', 'evaluator_id': evaluator['id'],
                             'message': 'Tool evidence and trajectory completeness are verified only after execution'})
    credentials = runtime.readiness(db, agent_conf, [(e['kind'], e['config']) for e in evaluators])
    if credentials['state'] != 'ready':
        warnings.append({'code': 'worker_readiness_'+credentials['state'],
                         'message': 'Worker capability/credential status is advisory; verify the target Worker before execution'})
    config = request['config']
    fingerprint = digest({'preflight_schema': 1, 'project_id': request['project_id'],
                          'dataset_id': dataset['id'], 'dataset_version_id': version['id'] if version else None,
                          'snapshot_hash': catalog.snapshot_hash(rows), 'agent_id': agent['id'], 'agent_hash': agent['config_hash'],
                          'evaluators': sorted((e['id'], e['config_hash']) for e in evaluators),
                          'config': config, 'diagnostic': request['diagnostic']})
    diagnostic_allowed = bool(cases) and all(e['code'] in {'expected_missing', 'expected_invalid', 'final_state_unavailable'} for e in errors)
    return {'ready': not errors, 'submission_allowed': not errors or request['diagnostic'] and diagnostic_allowed,
            'diagnostic': request['diagnostic'], 'blocking_errors': errors, 'warnings': warnings,
            'source': {'dataset_id': dataset['id'], 'dataset_version_id': version['id'] if version else None,
                       'snapshot_hash': catalog.snapshot_hash(rows), 'case_count': len(cases),
                       'critical_count': sum(c.get('metadata', {}).get('critical') is True for c in cases)},
            'evidence_coverage': coverage, 'credential_readiness': credentials, 'preflight_hash': fingerprint,
            'estimated_call_count': {'agent_max': len(cases)*(1+config.get('max_retries', 0)),
                                     'judge_max': len(cases)*sum(e['kind']=='llm_judge' for e in evaluators)*(1+config.get('judge_retries', 1))},
            'estimated_cost': None, 'cost_note': 'No pricing configured; external calls and costs are not performed or inferred'}


def preflight(store, body):
    request = normalize(body)
    with store.connect() as db:
        db.execute('BEGIN')
        return report(db, store, request)
