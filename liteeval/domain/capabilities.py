"""Declared task/Harness compatibility; declarations do not confer permissions."""
import re

BASE_CAPABILITIES = frozenset({'workspace.read', 'workspace.write', 'process.execute'})


def validate_capabilities(value):
    if not isinstance(value, list) or not all(isinstance(c, str) and re.fullmatch(r'[a-z][a-z0-9_.]{0,79}', c) for c in value):
        raise ValueError('Capabilities must be an array of valid names')
    if len(value) != len(set(value)):
        raise ValueError('Duplicate capability')
    return value
