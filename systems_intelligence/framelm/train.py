"""Supervised response training with masked prompt loss and held-out validation."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import random
from .core import Context, frame_prompt, messages, serialize_messages
from .controller.hooks import observed


def read_records(path):
    records = []
    for line_no, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item.get("prompt"), str) or not isinstance(item.get("response"), str) or not item["response"].strip():
            raise ValueError(f"Invalid prompt/response at {path}:{line_no}")
        if not isinstance(item.get("contexts"), list) or not all(isinstance(c, str) for c in item["contexts"]):
            raise ValueError(f"contexts must be a list of strings at {path}:{line_no}")
        records.append(item)
    if not records:
        raise ValueError(f"Empty dataset: {path}")
    return records


def encode_record(item):
    from .neural import BOS, EOS
    ctx = [Context(i, "training", text, 0) for i, text in enumerate(item["contexts"], 1)]
    prefix = [BOS] + list(serialize_messages(messages(frame_prompt(item["prompt"]), ctx)).encode("utf-8"))
    answer = list(item["response"].encode("utf-8")) + [EOS]
    ids = prefix + answer
    labels = [-100] * len(prefix) + answer
    return ids[:-1], labels[1:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--preset", choices=["micro", "small", "medium", "large"], default="micro")
    parser.add_argument("--steps", type=int, default=200)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--output", type=Path, default=Path("state/tiny.pt"))
    parser.add_argument("--config", type=Path, default=Path("configs/demo.json"))
    args = parser.parse_args()
    if min(args.steps, args.batch_size, args.threads, args.learning_rate) <= 0:
        parser.error("steps, batch-size, threads and learning-rate must be positive")
    from .core import load_config
    from .controller import get_controller
    config=load_config(args.config)
    with get_controller(config):
        train(args,config)


@observed('training.run',lambda args,config:config)
def train(args,config):
    try:
        import torch
        from torch.nn import functional as F
        from .neural import ByteTransformer, PRESETS, PAD
    except ImportError:
        raise RuntimeError("Install the optional dependency: python -m pip install -r requirements-neural.txt")
    torch.set_num_threads(args.threads)
    torch.manual_seed(args.seed)
    rng = random.Random(args.seed)
    records, val_records = read_records(args.data), read_records(args.validation)
    train_prompts = {r["prompt"].strip().casefold() for r in records}
    if train_prompts & {r["prompt"].strip().casefold() for r in val_records}:
        raise ValueError("Train and validation prompts overlap. Supply a held-out validation set.")
    train = [encode_record(r) for r in records]
    validation = [encode_record(r) for r in val_records]
    config = PRESETS[args.preset]
    if max(len(x) for x, _ in train + validation) > config["block_size"]:
        raise ValueError("A training example exceeds block_size; shorten it or use a larger-window preset.")
    model = ByteTransformer(config).to(args.device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate)
    parameter_count = sum(p.numel() for p in model.parameters())
    print(f"Parameters: {parameter_count:,}; training examples: {len(train)}; validation: {len(validation)}")

    def batch(items):
        length = max(len(x) for x, _ in items)
        x = torch.full((len(items), length), PAD, dtype=torch.long, device=args.device)
        y = torch.full_like(x, -100)
        for i, (inputs, labels) in enumerate(items):
            x[i, :len(inputs)] = torch.tensor(inputs, device=args.device)
            y[i, :len(labels)] = torch.tensor(labels, device=args.device)
        return x, y

    @torch.no_grad()
    def evaluate():
        model.eval()
        total, tokens = 0., 0
        for start in range(0, len(validation), args.batch_size):
            x, y = batch(validation[start:start + args.batch_size])
            logits = model(x)
            total += float(F.cross_entropy(logits.reshape(-1, logits.shape[-1]), y.reshape(-1), ignore_index=-100, reduction="sum"))
            tokens += int((y != -100).sum())
        return total / tokens

    history = [{"step": 0, "validation_loss": evaluate()}]
    from .controller.hooks import emit
    emit('training.evaluation', history[0])
    for step in range(1, args.steps + 1):
        model.train()
        x, y = batch(rng.choices(train, k=args.batch_size))
        emit('training.batch', {'step':step,'examples':args.batch_size,'shape':list(x.shape)})
        logits = model(x)
        loss = F.cross_entropy(logits.reshape(-1, logits.shape[-1]), y.reshape(-1), ignore_index=-100)
        if not torch.isfinite(loss):
            raise RuntimeError("Non-finite loss; training stopped.")
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.)
        optimizer.step()
        emit('training.step', {'step':step,'loss':float(loss.detach())})
        if step == 1 or step % 25 == 0 or step == args.steps:
            row = {"step": step, "training_loss": float(loss.detach()), "validation_loss": evaluate()}
            history.append(row)
            emit('training.evaluation', row)
            print(json.dumps(row))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Save inference weights; this command starts a new run, not a resume.
    temp = args.output.with_suffix(args.output.suffix + ".tmp")
    torch.save({"format": "framelm-byte-v1", "config": config,
                "model": {k: v.detach().cpu() for k, v in model.state_dict().items()}}, temp)
    temp.replace(args.output)
    report = {"preset": args.preset, "parameters": parameter_count, "steps": args.steps,
              "seed": args.seed, "history": history,
              "validation_byte_perplexity": math.exp(min(history[-1]["validation_loss"], 700)),
              "train_sha256": hashlib.sha256(args.data.read_bytes()).hexdigest(),
              "validation_sha256": hashlib.sha256(args.validation.read_bytes()).hexdigest(),
              "note": "Byte-level loss is not answer correctness. Demo data cannot train a useful general assistant."}
    args.output.with_suffix(".metrics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Saved {args.output}")


if __name__ == "__main__":
    main()
