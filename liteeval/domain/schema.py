import math

# Deliberately documented JSON Schema subset; unsupported keywords fail explicitly.
SCHEMA_KEYS={'type','properties','required','additionalProperties','items','enum','minimum','maximum','description','title'}

def check_schema(schema,depth=0):
    if depth>32 or not isinstance(schema,dict) or set(schema)-SCHEMA_KEYS:raise ValueError('Unsupported JSON Schema keyword or nesting depth')
    if 'type' in schema and schema['type'] not in {'object','array','string','integer','number','boolean','null'}:raise ValueError('Unsupported schema type')
    props=schema.get('properties',{})
    if not isinstance(props,dict):raise ValueError('Schema properties must be an object')
    for child in props.values():check_schema(child,depth+1)
    if 'items' in schema:check_schema(schema['items'],depth+1)
    if not isinstance(schema.get('required',[]),list) or not all(isinstance(v,str) for v in schema.get('required',[])):raise ValueError('Schema required must be a string array')
    if 'additionalProperties' in schema and type(schema['additionalProperties']) is not bool:raise ValueError('additionalProperties only supports boolean')
    if 'enum' in schema and not isinstance(schema['enum'],list):raise ValueError('enum must be an array')
    for bound in ('minimum','maximum'):
        if bound in schema and (type(schema[bound]) not in (int,float) or not math.isfinite(schema[bound])):raise ValueError('Schema bounds must be finite numbers')

def validate_schema(value,schema):
    check_schema(schema)
    kinds={'object':dict,'array':list,'string':str,'integer':int,'number':(int,float),'boolean':bool,'null':type(None)}
    if 'type' in schema:
        kind=schema['type']
        if kind not in kinds:raise ValueError('Unsupported schema type')
        if not isinstance(value,kinds[kind]) or kind in {'integer','number'} and isinstance(value,bool):return False
    if 'enum' in schema and value not in schema['enum']:return False
    if isinstance(value,dict):
        if any(k not in value for k in schema.get('required',[])):return False
        props=schema.get('properties',{})
        if schema.get('additionalProperties') is False and set(value)-set(props):return False
        if any(k in value and not validate_schema(value[k],s) for k,s in props.items()):return False
    if isinstance(value,list) and 'items' in schema:
        if not all(validate_schema(v,schema['items']) for v in value):return False
    if type(value) in (int,float):
        if 'minimum' in schema and value<schema['minimum']:return False
        if 'maximum' in schema and value>schema['maximum']:return False
    return True
