"""Stdio MCP tool that writes only files declared by the Bench."""

import json
import os
from pathlib import Path, PurePosixPath
import sys
import tempfile


def write_submission(root, allowed, arguments):
    if not isinstance(arguments, dict):
        raise ValueError('Submission arguments must be an object')
    name = arguments.get('path')
    content = arguments.get('content')
    if not isinstance(name, str) or name not in allowed or not isinstance(content, str):
        raise ValueError('Use a declared submission path and text content')
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
        raise ValueError('Unsafe submission path')
    data = content.encode('utf-8')
    if len(data) > allowed[name]:
        raise ValueError('Submission exceeds the declared size limit')
    target = root.joinpath(*path.parts)
    if any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction())
           for p in (target, *target.parents) if p != root and root in p.parents):
        raise ValueError('Submission path must not contain links')
    target.resolve().relative_to(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix='.submission-', dir=target.parent)
    try:
        with os.fdopen(handle, 'wb') as file:
            file.write(data)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return f'Wrote {len(data)} bytes to {name}'


def serve(root, allowed):
    for line in sys.stdin:
        request = json.loads(line)
        if 'id' not in request:
            continue
        method = request['method']
        params = request.get('params', {})
        response = {'jsonrpc': '2.0', 'id': request['id']}
        if method == 'initialize':
            response['result'] = {'protocolVersion': params.get('protocolVersion', '2024-11-05'),
                                  'capabilities': {'tools': {}},
                                  'serverInfo': {'name': 'liteeval-submission', 'version': '1.0.0'}}
        elif method == 'tools/list':
            response['result'] = {'tools': [{'name': 'write_submission',
                                           'description': 'Write a declared final submission file in the current task workspace.',
                                           'inputSchema': {'type': 'object', 'properties': {
                                               'path': {'type': 'string', 'enum': sorted(allowed)},
                                               'content': {'type': 'string'}}, 'required': ['path', 'content'],
                                               'additionalProperties': False}}]}
        elif method == 'tools/call' and params.get('name') == 'write_submission':
            try:
                message = write_submission(root, allowed, params.get('arguments', {}))
                response['result'] = {'content': [{'type': 'text', 'text': message}], 'isError': False}
            except (ValueError, OSError) as exc:
                response['result'] = {'content': [{'type': 'text', 'text': str(exc)}], 'isError': True}
        elif method == 'ping':
            response['result'] = {}
        else:
            response['error'] = {'code': -32601, 'message': 'Unknown method'}
        print(json.dumps(response), flush=True)


if __name__ == '__main__':
    serve(Path.cwd().resolve(), json.loads(sys.argv[1]))
