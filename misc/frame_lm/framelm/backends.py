"""Optional model adapters, imported without eagerly importing PyTorch."""
import json
from urllib.request import Request, urlopen
from urllib.error import URLError
from .core import Candidate, messages, serialize_messages


class OllamaBackend:
    def generate(self, frame, contexts, config):
        results = []
        for i in range(config["candidate_count"]):
            payload = {"model": config["model"], "messages": messages(frame, contexts), "stream": False,
                       "options": {"temperature": config["temperature"], "seed": config["seed"] + i,
                                   "num_predict": config["max_new_tokens"]}}
            request = Request(config["endpoint"].rstrip("/") + "/api/chat",
                              data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
            try:
                with urlopen(request, timeout=config["timeout_seconds"]) as response:
                    data = json.load(response)
                text = data["message"]["content"]
                if not isinstance(text, str):
                    raise ValueError("Model content must be a string.")
            except (URLError, KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(f"Ollama request failed: {exc}") from exc
            results.append(Candidate(text, "ollama:" + config["model"]))
        return results


class TinyBackend:
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
