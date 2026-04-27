"""Persistent local daemon for the tuned Feishu Office model."""

from __future__ import annotations

import argparse
import json
import socketserver
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


def _load_runtime():
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer, TextIteratorStreamer

    return torch, AutoModelForCausalLM, AutoTokenizer, PeftModel, TextIteratorStreamer


@dataclass
class RuntimeConfig:
    base_model: str
    adapter_path: str
    max_input_chars: int
    default_max_tokens: int
    host: str
    port: int


class ModelRuntime:
    def __init__(self, config: RuntimeConfig):
        self.config = config
        self._lock = threading.Lock()
        self._tokenizer = None
        self._model = None

    def _ensure_loaded(self) -> None:
        if self._model is not None and self._tokenizer is not None:
            return
        torch, AutoModelForCausalLM, AutoTokenizer, PeftModel, TextIteratorStreamer = _load_runtime()
        tokenizer = AutoTokenizer.from_pretrained(self.config.base_model, trust_remote_code=True)
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token
        model = AutoModelForCausalLM.from_pretrained(
            self.config.base_model,
            torch_dtype=torch.float16,
            device_map="auto",
            trust_remote_code=True,
        )
        adapter_path = Path(self.config.adapter_path)
        if adapter_path.exists():
            model = PeftModel.from_pretrained(model, str(adapter_path))
        model.eval()
        self._tokenizer = tokenizer
        self._model = model
        self._torch = torch
        self._text_iterator_streamer = TextIteratorStreamer

    def stream_generate(self, prompt: str, max_tokens: int):
        if len(prompt) > self.config.max_input_chars:
            raise RuntimeError(
                f"Prompt exceeds max_input_chars={self.config.max_input_chars}: {len(prompt)}"
            )
        with self._lock:
            self._ensure_loaded()
            tokenizer = self._tokenizer
            model = self._model
            torch = self._torch
            inputs = tokenizer(prompt, return_tensors="pt")
            inputs = {key: value.to(model.device) for key, value in inputs.items()}
            streamer = self._text_iterator_streamer(
                tokenizer,
                skip_prompt=True,
                skip_special_tokens=True,
            )

            generation_thread = threading.Thread(
                target=model.generate,
                kwargs={
                    **inputs,
                    "do_sample": False,
                    "max_new_tokens": max_tokens or self.config.default_max_tokens,
                    "pad_token_id": tokenizer.pad_token_id,
                    "eos_token_id": tokenizer.eos_token_id,
                    "streamer": streamer,
                },
            )
            generation_thread.start()
            for chunk in streamer:
                yield chunk
            generation_thread.join()


class FeishuOfficeHandler(socketserver.StreamRequestHandler):
    runtime: ModelRuntime

    def _write_json(self, payload: Dict[str, Any]) -> None:
        self.wfile.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        self.wfile.flush()

    def handle(self) -> None:
        line = self.rfile.readline()
        if not line:
            return
        request = json.loads(line.decode("utf-8"))
        request_type = request.get("type")
        if request_type == "ping":
            self._write_json({"type": "pong"})
            return
        if request_type != "generate":
            self._write_json({"type": "error", "message": f"Unsupported request type: {request_type}"})
            return
        prompt = request.get("prompt", "")
        max_tokens = int(request.get("max_tokens", self.runtime.config.default_max_tokens))
        try:
            for chunk in self.runtime.stream_generate(prompt, max_tokens):
                if chunk:
                    self._write_json({"type": "chunk", "text": chunk})
        except Exception as exc:  # pragma: no cover - exercised via remote integration
            self._write_json({"type": "error", "message": str(exc)})
            return
        self._write_json({"type": "done"})


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    parser = argparse.ArgumentParser(description="Persistent daemon for the Feishu Office adapter.")
    parser.add_argument("--base-model", required=True)
    parser.add_argument("--adapter-path", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19600)
    parser.add_argument("--max-input-chars", type=int, default=32768)
    parser.add_argument("--default-max-tokens", type=int, default=128)
    args = parser.parse_args()

    config = RuntimeConfig(
        base_model=args.base_model,
        adapter_path=args.adapter_path,
        max_input_chars=args.max_input_chars,
        default_max_tokens=args.default_max_tokens,
        host=args.host,
        port=args.port,
    )
    runtime = ModelRuntime(config)
    FeishuOfficeHandler.runtime = runtime

    with ThreadedTCPServer((args.host, args.port), FeishuOfficeHandler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
