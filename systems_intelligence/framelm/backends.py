"""Optional model adapters, imported without eagerly importing PyTorch."""
import json
from urllib.request import Request, urlopen
from urllib.error import URLError
from .core import Candidate, messages, serialize_messages
from .controller.hooks import emit, observed


class OllamaBackend:
    @observed('backend.ollama')
    def generate(self, frame, contexts, config):
        results = []
        for i in range(config["candidate_count"]):
            streaming=config.get('stream_output',False)
            payload = {"model": config["model"], "messages": messages(frame, contexts), "stream": streaming,
                       "options": {"temperature": config["temperature"], "seed": config["seed"] + i,
                                   "num_predict": config["max_new_tokens"]}}
            request = Request(config["endpoint"].rstrip("/") + "/api/chat",
                              data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
            try:
                with urlopen(request, timeout=config["timeout_seconds"]) as response:
                    if streaming:
                        parts=[]; done=False
                        for line in response:
                            if not line.strip(): continue
                            data=json.loads(line)
                            if data.get('error'): raise ValueError(data['error'])
                            chunk=data.get('message',{}).get('content','')
                            if not isinstance(chunk,str): raise ValueError('Model chunk must be a string')
                            if chunk:
                                emit('backend.output_chunk',{'candidate':i,'index':len(parts),'text':chunk,'granularity':'chunk','token_detail_available':False,'backend':'ollama'},phase='provisional')
                                parts.append(chunk)
                            if data.get('done'): done=True; break
                        if not done: raise ValueError('Model stream ended without a completion marker')
                        text=''.join(parts)
                    else:
                        data = json.load(response)
                        text = data["message"]["content"]
                if not isinstance(text, str):
                    raise ValueError("Model content must be a string.")
            except (URLError, KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(f"Ollama request failed: {exc}") from exc
            results.append(Candidate(text, "ollama:" + config["model"]))
            if not streaming:
                emit('backend.output_chunk', {'candidate':i,'text':text,'granularity':'complete_response',
                                             'token_detail_available':False,'backend':'ollama','detail_unavailable':'Streaming was disabled in configuration.'})
        return results


class TinyBackend:
    @observed('backend.tiny')
    def generate(self, frame, contexts, config):
        try:
            from .neural import load_model, generate
        except ImportError as exc:
            raise RuntimeError("Tiny backend requires PyTorch. Install requirements-neural.txt.") from exc
        model = load_model(config["checkpoint"], config["device"])
        prefix = serialize_messages(messages(frame, contexts))
        if 1 + len(prefix.encode("utf-8")) + config["max_new_tokens"] > model.config["block_size"]:
            raise ValueError("Framed request plus output exceeds the tiny model byte window. Reduce prompt/context/output budgets or train a larger-window model.")
        return [Candidate(generate(model, prefix, config["max_new_tokens"], config["temperature"], config["seed"] + i), "tiny")
                for i in range(config["candidate_count"])]
