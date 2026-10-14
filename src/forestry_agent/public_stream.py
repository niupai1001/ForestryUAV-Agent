"""Project only public structured answer text, keeping provider reasoning separate."""
from pydantic_core import from_json


class AnswerStream:
    def __init__(self):
        self.parts = {}

    def update(self, index, part=None, delta=None):
        state = self.parts.setdefault(index, {'name':'','args':'','shown':''})
        if part is not None:
            state['name'] = part.tool_name
            state['args'] = part.args if isinstance(part.args,str) else ''
            if isinstance(part.args,dict):
                data = part.args
            else:
                data = None
        else:
            state['name'] += delta.tool_name_delta or ''
            if isinstance(delta.args_delta,str):
                state['args'] += delta.args_delta
            data = delta.args_delta if isinstance(delta.args_delta,dict) else None
        if state['name'] != 'final_result':
            return None
        if data is None:
            try:
                data = from_json(state['args'],allow_partial='trailing-strings')
            except ValueError:
                return None
        if not isinstance(data,dict):
            return None
        text = data.get('response',data.get('summary'))
        if isinstance(text,str) and text!=state['shown']:
            state['shown'] = text
            return text
        return None
