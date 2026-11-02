"""Provider boundary: OpenAI-compatible Chat Completions, explicit model and env key."""
from liteeval.infrastructure.security.secrets import checked_url, secret_from_env
from liteeval.infrastructure.network.http import post_json

class OpenAICompatibleProvider:
    def __init__(self,config,store=None):self.config=config;self.store=store
    async def generate(self,messages):
        conf=self.config
        url=checked_url(conf['base_url'].rstrip('/')+'/chat/completions')
        payload={'model':conf['model'],'messages':messages,**conf.get('generation_parameters',{})}
        if conf.get('json_mode',False):payload['response_format']={'type':'json_object'}
        if conf.get('connection_id'):
            from liteeval.application.connections import api_key
            key=api_key(self.store,conf['connection_id'])
        else:key=secret_from_env(conf.get('api_key_env') or 'LITEEVAL_JUDGE_API_KEY')
        raw=await post_json(url,payload,{'Authorization':'Bearer '+key} if key else {},conf.get('timeout_seconds',30))
        return raw['choices'][0]['message']['content'],raw

    async def respond(self, body, on_event=None):
        conf = self.config
        if conf.get('connection_id'):
            from liteeval.application.connections import api_key
            key = api_key(self.store, conf['connection_id'])
        else:
            key = secret_from_env(conf.get('api_key_env') or 'LITEEVAL_JUDGE_API_KEY')
        payload = {**conf.get('generation_parameters', {}), **body, 'model': conf['model']}
        if on_event:
            from liteeval.infrastructure.network.http import responses_stream
            return await responses_stream(checked_url(conf['base_url'].rstrip('/') + '/responses'), payload,
                {'Authorization': 'Bearer ' + key} if key else {}, on_event, timeout=120)
        return await post_json(checked_url(conf['base_url'].rstrip('/') + '/responses'), payload,
                               {'Authorization': 'Bearer ' + key} if key else {}, conf.get('timeout_seconds', 300))
