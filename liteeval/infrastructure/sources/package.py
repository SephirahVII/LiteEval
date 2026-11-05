from liteeval.domain.values import decoded
"""Strict native directory loader. Parsing never executes code or calls a model."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import tomllib
from liteeval.domain.benchmark import Package, TaskSpec, fields, finite
from liteeval.domain.paths import relative_path
from liteeval.domain.values import digest
from liteeval.domain.rubrics import validate_rubric
from liteeval.domain.capabilities import validate_capabilities


class DirectoryLoader:
    def load(self, path):
        root = Path(path).resolve()
        if not root.is_dir():
            raise ValueError('Benchmark directory does not exist')
        blobs, files, total = {}, {}, 0
        for p in sorted(root.rglob('*')):
            if '__pycache__' in p.relative_to(root).parts:
                continue
            if p.is_symlink():
                raise ValueError('Symlinks are not supported in benchmark packages')
            if p.is_dir():
                continue
            if not p.is_file() or p.stat().st_nlink > 1:
                raise ValueError('Only regular, single-link files are supported')
            if len(files) >= 4000 or p.stat().st_size > 16_000_000:
                raise ValueError('Package file limit exceeded')
            data = p.read_bytes(); total += len(data)
            if total > 64_000_000:
                raise ValueError('Package exceeds 64 MB')
            sha = hashlib.sha256(data).hexdigest()
            files[relative_path(p.relative_to(root).as_posix())] = sha
            blobs[sha] = data

        def read(name):
            name = relative_path(name)
            if name not in files:
                raise ValueError('Missing package resource: ' + name)
            return blobs[files[name]]

        conf = tomllib.loads(read('dataset.toml').decode('utf-8'))
        fields(conf, ['schema_version', 'benchmark', 'tasks', 'defaults', 'environment_profiles', 'harness_requirements'], 'dataset')
        if conf.get('schema_version') != 1:
            raise ValueError('Unsupported benchmark schema_version')
        identity = conf['benchmark']
        fields(identity, ['id', 'version', 'name', 'description', 'license'], 'benchmark')
        for key in ('id', 'version'):
            if not isinstance(identity.get(key), str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', identity[key]):
                raise ValueError('Invalid benchmark ' + key)
        defaults = conf.get('defaults', {})
        fields(defaults, ['environment_profile', 'target_timeout_seconds', 'judge_timeout_seconds'], 'defaults')
        requirements = conf.get('harness_requirements', {})
        fields(requirements, ['evaluation_protocol', 'default_evaluation_harness', 'target_capabilities'], 'harness requirements')
        if requirements.get('evaluation_protocol', 'liteeval.task/v1') != 'liteeval.task/v1':
            raise ValueError('Unsupported evaluation protocol')
        if requirements.get('default_evaluation_harness', 'liteeval.native/v1') != 'liteeval.native/v1':
            raise ValueError('Select an explicit supported evaluation harness')
        fields(conf.get('tasks', {}), ['include'], 'tasks')
        patterns = conf.get('tasks', {}).get('include', ['tasks/*/task.toml'])
        if not isinstance(patterns, list) or not patterns:
            raise ValueError('tasks.include requires patterns')
        selected = set()
        for pattern in patterns:
            relative_path(pattern)
            selected.update(p.relative_to(root).as_posix() for p in root.glob(pattern) if p.is_file())
        if not selected:
            raise ValueError('No tasks found')
        tasks, ids = [], set()
        for name in sorted(selected):
            value = tomllib.loads(read(name).decode('utf-8'))
            fields(value, ['schema_version', 'task', 'input', 'environment', 'requirements', 'budget', 'artifacts', 'judge', 'workflow'], name)
            if value.get('schema_version') != 1:
                raise ValueError('Unsupported task schema_version')
            meta = value['task']; fields(meta, ['id', 'title', 'category', 'tags', 'metadata'], 'task')
            if not isinstance(meta.get('metadata', {}), dict):
                raise ValueError('Task metadata must be an object')
            task_id = meta['id']
            if not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', task_id) or task_id in ids:
                raise ValueError('Invalid or duplicate task id')
            ids.add(task_id)
            folder = str(Path(name).parent).replace('\\', '/')

            def resource(local):
                return relative_path(folder + '/' + relative_path(local))

            inputs = value['input']
            fields(inputs, ['prompt_file', 'dialogue_file'], 'input')
            if len(inputs) != 1:
                raise ValueError('Specify exactly one prompt_file or dialogue_file')
            prompt = read(resource(inputs['prompt_file'])).decode('utf-8') if 'prompt_file' in inputs else ''
            if 'prompt_file' in inputs and not prompt.strip():
                raise ValueError('Empty prompt')
            env = value.get('environment', {})
            fields(env, ['profile', 'initial_workspace'], 'environment')
            profile = env.get('profile', defaults.get('environment_profile'))
            environment = conf.get('environment_profiles', {}).get(profile)
            if not isinstance(environment, dict):
                raise ValueError('Unknown environment profile')
            fields(environment, ['type', 'image', 'network', 'cpus', 'memory'], 'environment profile')
            if environment.get('type') not in ('workspace', 'docker'):
                raise ValueError('Unsupported environment type')
            if environment['type'] == 'docker' and (not isinstance(environment.get('image'), str) or not re.fullmatch(r'[a-z0-9][a-z0-9._/:-]*@sha256:[a-f0-9]{64}', environment['image'])):
                raise ValueError('Docker image must be fixed by digest; build externally before publishing')
            if environment.get('network', 'none') not in ('none', 'bridge'):
                raise ValueError('Unsupported Docker network')
            finite(environment.get('cpus', 1), 'CPU limit', .1, 16)
            if not re.fullmatch(r'[1-9][0-9]*(?:[kmgKMG])?', str(environment.get('memory', '1g'))):
                raise ValueError('Invalid memory limit')
            requirements_task = value.get('requirements', {})
            fields(requirements_task, ['capabilities'], 'requirements')
            capabilities = sorted(set(validate_capabilities(requirements_task.get('capabilities', []))) |
                                  set(validate_capabilities(requirements.get('target_capabilities', []))))
            workspace = {}
            if env.get('initial_workspace'):
                prefix = resource(env['initial_workspace']) + '/'
                workspace = {n[len(prefix):]: sha for n, sha in files.items() if n.startswith(prefix)}
                if not workspace:
                    raise ValueError('Empty or missing initial_workspace')
                if any(n.split('/', 1)[0] in ('.harness', '.harness-output') for n in workspace):
                    raise ValueError('Initial workspace contains a reserved Harness path')
                if any(part == 'judge' for part in Path(env['initial_workspace']).parts):
                    raise ValueError('Judge resources cannot be public workspace')
            budget = {k: defaults.get(k, v) for k, v in [('target_timeout_seconds', 2400), ('judge_timeout_seconds', 180)]}
            fields(value.get('budget', {}), budget, 'budget'); budget.update(value.get('budget', {}))
            for k, v in budget.items(): finite(v, k, .05, 86400)
            dialogue = []
            if 'dialogue_file' in inputs:
                dialogue_path = resource(inputs['dialogue_file'])
                script = tomllib.loads(read(dialogue_path).decode('utf-8'))
                fields(script, ['schema_version', 'turns'], 'dialogue')
                turns = script.get('turns')
                if script.get('schema_version') != 1 or not isinstance(turns, list) or not 2 <= len(turns) <= 32:
                    raise ValueError('Dialogue v1 requires between 2 and 32 turns')
                private_files = {files[dialogue_path]}
                for turn in turns:
                    fields(turn, ['instruction_file', 'timeout_seconds'], 'dialogue turn')
                    path = resource(turn['instruction_file'])
                    data = read(path)
                    if not data.decode('utf-8').strip() or len(data) > 64_000:
                        raise ValueError('Dialogue instruction must be nonempty UTF-8 text within 64 KB')
                    sha = files[path]
                    private_files.add(sha)
                    timeout = turn.get('timeout_seconds', budget['target_timeout_seconds'])
                    finite(timeout, 'Dialogue turn timeout', .05, budget['target_timeout_seconds'])
                    dialogue.append({'instruction_sha256': sha, 'timeout_seconds': timeout})
                if private_files.intersection(workspace.values()):
                    raise ValueError('Dialogue resources cannot be included in the Agent workspace')
            artifacts = value.get('artifacts', [])
            if not isinstance(artifacts, list) or not artifacts:
                raise ValueError('Declare at least one submission artifact')
            artifact_paths = set()
            for item in artifacts:
                fields(item, ['path', 'required', 'max_bytes'], 'artifact')
                item['path'] = relative_path(item['path'])
                if item['path'].split('/', 1)[0] in ('.harness', '.harness-output'):
                    raise ValueError('Submission cannot target a reserved Harness path')
                if item['path'] in artifact_paths or type(item.get('required', True)) is not bool:
                    raise ValueError('Invalid artifact rule')
                artifact_paths.add(item['path']); finite(item.get('max_bytes', 1_000_000), 'artifact size', 1, 16_000_000)
            judge = value['judge']; fields(judge, ['ground_truth_file', 'checks', 'aggregate'], 'judge')
            private = {}
            gt = judge.get('ground_truth_file')
            if gt:
                if not gt.startswith('judge/'):
                    raise ValueError('Ground truth must be a Judge resource')
                decoded(read(resource(gt)))
                private[gt] = files[resource(gt)]
            checks = judge.get('checks', [])
            if not checks:
                raise ValueError('At least one Judge check is required')
            seen = set()
            for check in checks:
                fields(check, ['id', 'type', 'entrypoint', 'command', 'timeout_seconds', 'weight', 'must_pass', 'connection_slot', 'rubric_file', 'submission_paths'], 'Judge check')
                if not isinstance(check.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', check['id']) or check['id'] in seen:
                    raise ValueError('Invalid/duplicate Judge check id')
                seen.add(check['id']); finite(check.get('weight', 1), 'weight', .000001, 1_000_000)
                if type(check.get('must_pass', False)) is not bool:
                    raise ValueError('must_pass must be boolean')
                finite(check.get('timeout_seconds', budget['judge_timeout_seconds']), 'Judge timeout', .05, budget['judge_timeout_seconds'])
                if check['type'] == 'command':
                    entry = relative_path(check['entrypoint'])
                    if not entry.startswith('judge/'):
                        raise ValueError('Judge entrypoint must be private')
                    read(resource(entry))
                    argv = check.get('command', [])
                    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
                        raise ValueError('Judge requires command argv')
                elif check['type'] in ('llm', 'llm_rubric'):
                    if check.get('connection_slot', 'judge') != 'judge':
                        raise ValueError('Only the judge connection slot is supported')
                    rubric = check['rubric_file']
                    if not rubric.startswith('judge/'):
                        raise ValueError('Rubric must be private')
                    rubric_value = decoded(read(resource(rubric)))
                    if check['type'] == 'llm_rubric': validate_rubric(rubric_value)
                    for output in check.get('submission_paths', []):
                        if output not in artifact_paths:
                            raise ValueError('Judge references undeclared submission')
                else:
                    raise ValueError('Unsupported Judge type')
            for file, sha in files.items():
                prefix = folder + '/judge/'
                if file.startswith(prefix): private[file[len(folder) + 1:]] = sha
            policy = judge.get('aggregate', {})
            fields(policy, ['method', 'pass_threshold'], 'aggregation')
            if policy.get('method', 'weighted_mean') != 'weighted_mean':
                raise ValueError('Unsupported aggregation')
            finite(policy.get('pass_threshold', 1), 'pass threshold', 0, 1)
            compiled = {'checks': checks, 'aggregate': policy, 'resources': private, 'ground_truth_file': gt}
            workflow = value.get('workflow')
            if workflow is not None:
                fields(workflow, ['id', 'fixture_file', 'outputs'], 'Workflow')
                if dialogue or not isinstance(workflow.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', workflow['id']):
                    raise ValueError('Workflow requires an ID and a prompt')
                fixture = resource(workflow['fixture_file'])
                if not isinstance(decoded(read(fixture)), dict) or files[fixture] in workspace.values():
                    raise ValueError('Workflow fixture must be a private JSON object')
                outputs = workflow.get('outputs')
                if not isinstance(outputs, list) or not outputs:
                    raise ValueError('Workflow requires declared output paths')
                outputs = [relative_path(n) for n in outputs]
                if len(outputs) != len(set(outputs)) or not set(outputs) <= artifact_paths:
                    raise ValueError('Workflow outputs must be distinct submission artifacts')
                workflow = {'id': workflow['id'], 'fixture_sha256': files[fixture], 'outputs': outputs}
            tasks.append(TaskSpec(task_id, prompt, workspace, dict(environment), budget, artifacts, compiled, meta, capabilities, dialogue, workflow))
        compiled_tasks = []
        for task in tasks:
            compiled_task = asdict(task)
            if not task.dialogue:
                del compiled_task['dialogue']
            if task.workflow is None:
                del compiled_task['workflow']
            compiled_tasks.append(compiled_task)
        manifest = {'schema_version': 1, 'source_files': files, 'dataset': conf, 'tasks': compiled_tasks}
        return Package(identity['id'], identity['version'], digest(manifest), manifest, tasks, blobs)
