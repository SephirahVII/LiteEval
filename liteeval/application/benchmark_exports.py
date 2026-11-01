"""Exports immutable plans, evidence and the actual submitted file bytes."""
import csv
import io
import zipfile
from liteeval.domain.values import encoded


def export_execution(service, project_id, execution_id, destination):
    data = service.repository.execution(project_id, execution_id)
    result = data['result']
    if not result: raise ValueError('Execution has no persisted result')
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('execution.json', encoded(data))
        archive.writestr('events.jsonl', '\n'.join(encoded(e) for e in data['events']) + '\n')
        report = io.StringIO(newline=''); writer = csv.writer(report)
        writer.writerow(['task_id', 'execution_status', 'grading_outcome', 'score', 'wall_time_ms'])
        for task in result['tasks']:
            writer.writerow([task['task_id'], task['execution_status'], task['grading']['outcome'], task['grading']['score'], task['wall_time_ms']])
            for name, meta in task['submission'].items():
                archive.writestr('submissions/' + task['task_id'] + '/' + name, service.blobs.get(meta['sha256']))
        archive.writestr('summary.csv', report.getvalue().encode('utf-8-sig'))
