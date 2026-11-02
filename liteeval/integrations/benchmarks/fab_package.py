"""Deterministic conversion of pinned FAB v2 public questions to native packages."""
import hashlib
import json
from pathlib import Path
import tempfile
from liteeval.domain.values import decoded
from liteeval.infrastructure.sources.package import DirectoryLoader
from liteeval.integrations.benchmarks.fab_source import load_source, select_rows, rubric_for, research_prompt

OFFICIAL_TOOLS = ['edgar_search', 'web_search', 'parse_html_page', 'retrieve_information', 'calculator', 'price_history']
RESEARCH_CAPABILITIES = ['workspace.read', 'workspace.write', 'process.execute', 'web.search', 'web.fetch']


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def quote(value):
    return json.dumps(value, ensure_ascii=False)


def package_files(source, rows=None, docker_image=None):
    source = Path(source)
    provenance, data = load_source(source)
    selected = select_rows(data, rows)
    suffix = '' if selected == list(range(1, len(data) + 1)) else '-rows-' + '-'.join(map(str, selected))
    kind = 'docker' if docker_image else 'workspace'
    identifier = 'fab-v2-public' + suffix
    version = 'public-' + provenance['commit'][:12] + '-native1-' + kind
    environment = ('type = "workspace"\n' if not docker_image else
                   f'type = "docker"\nimage = {quote(docker_image)}\nnetwork = "bridge"\ncpus = 2\nmemory = "2g"\n')
    dataset = f'''schema_version = 1
[benchmark]
id = {quote(identifier)}
name = "FAB v2 Public — native rubric diagnostic"
version = {quote(version)}
description = "Pinned public questions; adapted Harness and single-model grading, not official leaderboard methodology"
license = {quote(provenance['license'])}
[tasks]
include = ["tasks/*/task.toml"]
[defaults]
environment_profile = "research"
target_timeout_seconds = 7200
judge_timeout_seconds = 180
[environment_profiles.research]
{environment}[harness_requirements]
evaluation_protocol = "liteeval.task/v1"
default_evaluation_harness = "liteeval.native/v1"
target_capabilities = {json.dumps(RESEARCH_CAPABILITIES)}
'''
    derived = {**provenance, 'conversion': {'schema_version': 1, 'selected_rows': selected,
               'exported_task_count': len(selected), 'required_capabilities': RESEARCH_CAPABILITIES,
               'grading': 'single_model_binary_rubric_diagnostic', 'official_tools': OFFICIAL_TOOLS,
               'official_leaderboard_comparable': False, 'environment': kind}}
    files = {'dataset.toml': dataset.encode('utf-8'), 'source.json': json_bytes(derived),
             'LICENSE': (source / 'LICENSE').read_bytes().replace(b'\r\n', b'\n')}
    count = 0
    for number in selected:
        row = data[number - 1]; question = row['Question']
        rubric = rubric_for(row, number, provenance)
        count += len(rubric['criteria'])
        task_id = f'fabv2-public-{number:03d}'
        prefix = 'tasks/' + task_id + '/'
        prompt = research_prompt(question)
        task = f'''schema_version = 1
[task]
id = {quote(task_id)}
title = {quote('FAB v2 public row ' + str(number))}
category = {quote(row['Question Type'])}
tags = ["finance", "fab-v2", "public", "research"]
[task.metadata]
source_row = {number}
expert_time_minutes = {int(row['Expert time (mins)'])}
question_sha256 = {quote(hashlib.sha256(question.encode('utf-8')).hexdigest())}
repository = {quote(provenance['repository'])}
commit = {quote(provenance['commit'])}
source_csv_sha256 = {quote(provenance['sha256'])}
methodology = "adapted_public_single_judge"
[input]
prompt_file = "prompt.md"
[environment]
initial_workspace = "environment/initial_workspace"
[[artifacts]]
path = "response.md"
required = true
max_bytes = 1000000
[[artifacts]]
path = "sources.json"
required = false
[[artifacts]]
path = "calculations.json"
required = false
[judge]
[[judge.checks]]
id = "fab-v2-rubric"
type = "llm_rubric"
connection_slot = "judge"
rubric_file = "judge/rubric.json"
submission_paths = ["response.md"]
timeout_seconds = 180
weight = 1
[judge.aggregate]
method = "weighted_mean"
pass_threshold = 1
'''
        files[prefix + 'task.toml'] = task.encode('utf-8')
        files[prefix + 'prompt.md'] = prompt.encode('utf-8')
        files[prefix + 'environment/initial_workspace/question.txt'] = question.encode('utf-8')
        files[prefix + 'judge/rubric.json'] = json_bytes(rubric)
    files['README.md'] = (f'''# FAB v2 Public native package

{len(selected)} public questions, {count} weighted binary criteria. Source commit: {provenance['commit']}.
Original CSV SHA-256: {provenance['sha256']}. MIT license retained.

This package preserves questions, original rubric entries, severity weights and must-pass flags.
Rubrics remain Judge-private resources. There is no fabricated ground_truth.json or reference answer.
The Agent must genuinely research and write response.md. A missing file is a failed task.

Execution requires a research-capable Harness declaring web.search and web.fetch, plus a saved Judge
connection. The plain JSON file/command Harness does not advertise those capabilities and is blocked.
Capability declarations are compatibility claims, not proof that tools/credentials work.

Target deadline: 7200 seconds (maximum, not a minimum). Expert time is human metadata only.
One Judge request returns a verdict for every criterion; platform code computes Partial Credit,
must-pass gating and All-Pass. Invalid/incomplete verdicts are scoring errors, not zero-quality answers.
This is single-model diagnostic grading. Official research tools, a three-model jury, repeated runs
and private test data are not reproduced. Results must not be labelled official FAB scores.

Validate: `liteeval benchmark validate benchmarks/fab_v2_public`.
See docs/fab-v2-native.md for conversion, import, Harness requirements and run commands.
''').encode('utf-8')
    return files


def convert_fab(source, destination, rows=None, docker_image=None):
    files = package_files(source, rows, docker_image)
    destination = Path(destination).absolute()
    if destination.is_symlink(): raise ValueError('Destination cannot be a symlink')
    destination = destination.resolve()
    reused = False
    if destination.exists():
        current = {p.relative_to(destination).as_posix(): p.read_bytes() for p in destination.rglob('*') if p.is_file() and not p.is_symlink()}
        if current != files: raise ValueError('Destination differs; choose a new directory instead of overwriting a package')
        reused = True
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.fab-build-', dir=destination.parent) as temporary:
            stage = Path(temporary).resolve()
            assert stage.is_relative_to(destination.parent)
            for name, data in files.items():
                path = stage / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
            # Validate before publication; invalid profiles never leave a partial package.
            DirectoryLoader().load(stage)
            stage.rename(destination)
    package = DirectoryLoader().load(destination)
    return {'path': str(destination), 'id': package.id, 'version': package.version, 'digest': package.digest,
            'task_count': len(package.tasks), 'criterion_count': sum(len(decoded(package.blobs[t.judge['resources']['judge/rubric.json']])['criteria']) for t in package.tasks),
            'reused': reused, 'official_leaderboard_comparable': False}
