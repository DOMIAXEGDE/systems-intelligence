"""Trainable causal byte Transformer. Requires optional PyTorch dependency."""
from pathlib import Path
import torch
from torch import nn

PAD, BOS, EOS, VOCAB = 256, 257, 258, 259
PRESETS = {
    "micro": {"width": 128, "layers": 2, "heads": 4, "block_size": 2048},
    "small": {"width": 256, "layers": 6, "heads": 8, "block_size": 2048},
    "medium": {"width": 512, "layers": 8, "heads": 8, "block_size": 4096},
    "large": {"width": 768, "layers": 12, "heads": 12, "block_size": 4096},
}


class ByteTransformer(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.config = dict(config)
        width = config["width"]
        if width % config["heads"] or min(config.values()) <= 0:
            raise ValueError("Invalid Transformer dimensions.")
        self.tokens = nn.Embedding(VOCAB, width)
        self.positions = nn.Embedding(config["block_size"], width)
        # A causal mask turns self-attention into autoregressive attention.
        self.blocks = nn.ModuleList([
            nn.TransformerEncoderLayer(width, config["heads"], width * 4,
                                       dropout=0.0, activation="gelu", batch_first=True, norm_first=True)
            for _ in range(config["layers"])])
        self.norm = nn.LayerNorm(width)
        self.head = nn.Linear(width, VOCAB, bias=False)
        self.head.weight = self.tokens.weight
        self.apply(self._init)

    @staticmethod
    def _init(module):
        if isinstance(module, (nn.Linear, nn.Embedding)):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)
            if isinstance(module, nn.Linear) and module.bias is not None:
                nn.init.zeros_(module.bias)

    def forward(self, ids):
        length = ids.shape[1]
        if length > self.config["block_size"]:
            raise ValueError("Sequence exceeds block_size; silent truncation is disabled.")
        hidden = self.tokens(ids) + self.positions(torch.arange(length, device=ids.device))
        mask = torch.ones(length, length, device=ids.device, dtype=torch.bool).triu(1)
        for block in self.blocks:
            hidden = block(hidden, src_mask=mask)
        return self.head(self.norm(hidden))


def load_model(path, device="cpu"):
    state = torch.load(Path(path), map_location="cpu", weights_only=True)
    if state.get("format") != "framelm-byte-v1":
        raise ValueError("Unsupported checkpoint format.")
    model = ByteTransformer(state["config"])
    model.load_state_dict(state["model"])
    return model.to(device).eval()


@torch.no_grad()
def generate(model, prefix, max_new_tokens=128, temperature=.3, seed=42):
    device = next(model.parameters()).device
    ids = [BOS] + list(prefix.encode("utf-8"))
    if len(ids) + max_new_tokens > model.config["block_size"]:
        raise ValueError("Input plus output exceeds block_size.")
    # CPU sampling generator is portable across CPU, CUDA and MPS model devices.
    rng = torch.Generator(device="cpu").manual_seed(seed)
    output = []
    model.eval()
    for _ in range(max_new_tokens):
        logits = model(torch.tensor([ids], device=device))[:, -1, :].float().cpu()
        logits[:, PAD] = -float("inf")
        logits[:, BOS] = -float("inf")
        if temperature == 0:
            token = int(logits.argmax(-1).item())
        else:
            token = int(torch.multinomial(torch.softmax(logits / temperature, -1), 1, generator=rng).item())
        if token == EOS:
            break
        ids.append(token)
        output.append(token)
    return bytes(output).decode("utf-8", errors="replace")
