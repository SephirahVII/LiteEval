import re
from typing import Any
from pathlib import PurePosixPath

PATH=re.compile(r'(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])')

def select(value: Any,path: str) -> Any:
    if path in {'$', ''}:return value
    if not isinstance(path,str):raise ValueError('Field path must be a string')
    rest=path[1:] if path.startswith('$') else '.'+path
    if ''.join(PATH.findall(rest))!=rest:raise ValueError('Unsupported field path')
    for token in PATH.findall(rest):
        if token.startswith('.'):
            if not isinstance(value,dict):raise KeyError(path)
            value=value[token[1:]]
        else:
            if not isinstance(value,list):raise KeyError(path)
            value=value[int(token[1:-1])]
    return value


def relative_path(value):
    if not isinstance(value,str): raise ValueError("Path must be text")
    path=PurePosixPath(value)
    if not value or value=="." or path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value:
        raise ValueError("Expected a safe relative path")
    return str(path)
