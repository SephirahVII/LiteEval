"""Provider boundary: OpenAI-compatible Chat Completions, explicit model and env key."""
from .security import checked_url,secret_from_env
from .transport import post_json

class OpenAICompatibleProvider:
    def __init__(self,config):self.config=config
    async def generate(self,messages):
        conf=self.config
        url=checked_url(conf['base_url'].rstrip('/')+'/chat/completions')
        payload={'model':conf['model'],'messages':messages,**conf.get('generation_parameters',{})}
        if conf.get('json_mode',False):payload['response_format']={'type':'json_object'}
        raw=await post_json(url,payload,{'Authorization':'Bearer '+secret_from_env(conf.get('api_key_env','LITEEVAL_JUDGE_API_KEY'))},conf.get('timeout_seconds',30))
        return raw['choices'][0]['message']['content'],raw
