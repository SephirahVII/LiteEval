"""Resolve task flow before execution and run it against the chosen Agent contract."""
from liteeval.application.dialogue import run_dialogue, snapshot_dialogue


def resolve_flow(task, agent_protocol, workflow):
    if task.get('workflow'):
        if not workflow or workflow['spec']['definition']['harness']['id'] != task['workflow']['id']:
            raise ValueError('Task requires its registered Workflow controller')
        if agent_protocol == 'liteeval.dialogue/v1':
            raise ValueError('A command Workflow requires a request/response Agent Harness')
        return 'controller'
    if bool(task.get('dialogue')) != (agent_protocol == 'liteeval.dialogue/v1'):
        raise ValueError('Harness mode must match the task input (single prompt or dialogue)')
    return 'dialogue' if task.get('dialogue') else 'single'


async def run_target(flow, agent, task, public, context, blobs, evidence, controller=None):
    if flow == 'single':
        return await agent.run(public, context)
    if flow == 'controller':
        return await controller.run(task, public, agent, context, evidence)
    if flow != 'dialogue':
        raise ValueError('Unknown planned task flow')
    events = []

    def emit(kind, payload):
        event = context['emit'](kind, payload)
        if kind.startswith('dialogue_'):
            events.append(event)

    public = {k: v for k, v in public.items() if k != 'prompt'}
    try:
        return await run_dialogue(agent, task, public, context, blobs, emit, context['usage'])
    finally:
        if events:
            evidence['trajectory'] = snapshot_dialogue(events, blobs)
