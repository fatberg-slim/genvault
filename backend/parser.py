"""Metadata extraction.

Node-aware ComfyUI parser (handles Kijai WanVideoWrapper nodes + stock nodes via
a generic field-shape fallback), plus A1111 'parameters' support. Video metadata
is read from the container tags via ffprobe (ComfyUI/VHS stores the graph in the
'comment' atom); image metadata is read from PNG text chunks via Pillow.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import config

# ---- small coercers ----------------------------------------------------------


def _str_or_none(v: Any):
    return None if v is None else str(v)


def _int_or_none(v: Any):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _float_or_none(v: Any):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _is_scalar(v: Any) -> bool:
    return isinstance(v, (str, int, float, bool))


def _empty() -> dict:
    return {
        "media_type": None, "filesize": None, "width": None, "height": None,
        "duration": None, "positive_prompt": None, "negative_prompt": None,
        "loras": [], "model": None, "seed": None, "sampler": None,
        "scheduler": None, "steps": None, "cfg": None, "shift": None,
        "raw_graph": None, "source": None,
    }


# ---- ffprobe -----------------------------------------------------------------


CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def ffprobe_json(path: Path, timeout: int = 60) -> dict:
    """ffprobe -show_format -show_streams as a dict. Raises RuntimeError with
    ffprobe's own message on failure.

    The output is read as BYTES and decoded as UTF-8: with text=True Python
    decodes with the Windows ANSI codepage (cp1252), and a single emoji or
    accented letter in an embedded ComfyUI prompt made that decode fail in a
    background thread — silently, leaving an empty result (no prompt, no
    width/height/duration)."""
    out = subprocess.run(
        [config.FFPROBE, "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW,
    )
    if out.returncode != 0 or not out.stdout:
        msg = out.stderr.decode("utf-8", "replace").strip()[-300:]
        raise RuntimeError(f"ffprobe could not read {Path(path).name}: {msg or 'no output'}")
    return json.loads(out.stdout.decode("utf-8", "replace"))


def _ffprobe_json(path: Path) -> dict:
    """Best-effort variant for metadata parsing: {} when ffprobe fails."""
    try:
        return ffprobe_json(path)
    except Exception:
        return {}


# ---- ComfyUI graph -----------------------------------------------------------

POS_FIELDS = ("positive_prompt", "positive", "text_positive")
NEG_FIELDS = ("negative_prompt", "negative", "text_negative")
MODEL_FIELDS = ("ckpt_name", "model", "model_name", "unet_name", "checkpoint")
SAMPLER_HINTS = ("sampler", "ksampler")
LORA_HINTS = ("lora",)
MODEL_SKIP = ("vae", "wav2vec", "clip", "lora", "upscal", "melband", "roformer")
MODEL_EXTS = (".safetensors", ".ckpt", ".gguf", ".pt", ".pth", ".sft", ".bin")


def _coerce_graph(value: Any) -> dict | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


def _looks_like_graph(d: Any) -> bool:
    if not isinstance(d, dict) or not d:
        return False
    return any(
        isinstance(v, dict) and ("class_type" in v or "inputs" in v)
        for v in list(d.values())[:8]
    )


def find_graph(container: dict) -> dict | None:
    """Locate a ComfyUI API graph inside a tag/info dict (direct or nested)."""
    keys = ("prompt", "comment", "Comment", "workflow", "description")
    candidates = [container.get(k) for k in keys if k in container]
    candidates += list(container.values())
    for raw in candidates:
        g = _coerce_graph(raw)
        if g is None:
            continue
        if _looks_like_graph(g):
            return g
        if "prompt" in g:
            inner = _coerce_graph(g["prompt"])
            if _looks_like_graph(inner):
                return inner
    return None


def _resolve_text(graph: dict, val, depth: int = 0):
    """Resolve a prompt input to literal text, following links through string /
    concatenate nodes so dynamically-assembled prompts can still be read."""
    if depth > 5:
        return None
    if isinstance(val, str):
        return val if val.strip() else None
    if isinstance(val, list) and val and isinstance(val[0], (str, int)):
        target = graph.get(str(val[0]))
        if isinstance(target, dict):
            inp = target.get("inputs", {})
            ct = str(target.get("class_type", "")).lower()
            for k in ("text", "text_g", "string", "value", "prompt", "positive_prompt"):
                if k in inp:
                    r = _resolve_text(graph, inp[k], depth + 1)
                    if r:
                        return r
            if "concat" in ct or "string" in ct or "join" in ct:
                parts = []
                for k in ("string_a", "string_b", "string_c", "prefix", "suffix", "text_b"):
                    if k in inp:
                        r = _resolve_text(graph, inp[k], depth + 1)
                        if r:
                            parts.append(r)
                if parts:
                    return " ".join(parts)
    return None


def parse_comfy_graph(graph: dict) -> dict:
    result = _empty()
    result["source"] = "comfyui"
    nodes = [
        (nid, n) for nid, n in graph.items()
        if isinstance(n, dict) and "inputs" in n
    ]

    def scalars(node):
        return {k: v for k, v in node.get("inputs", {}).items() if _is_scalar(v)}

    def title_of(node):
        meta = node.get("_meta")
        return str(meta.get("title", "")).lower() if isinstance(meta, dict) else ""

    # Pass 1: explicit positive_prompt / negative_prompt fields (WanVideo, etc.)
    for _nid, node in nodes:
        inp = scalars(node)
        for f in POS_FIELDS:
            if result["positive_prompt"] is None and isinstance(inp.get(f), str) and inp[f].strip():
                result["positive_prompt"] = inp[f]
        for f in NEG_FIELDS:
            if result["negative_prompt"] is None and isinstance(inp.get(f), str) and inp[f].strip():
                result["negative_prompt"] = inp[f]

    # Pass 2: text-encode nodes disambiguated by node title (positive vs negative)
    for _nid, node in nodes:
        ct = str(node.get("class_type", "")).lower()
        if "encode" not in ct and "cliptext" not in ct:
            continue
        txt = scalars(node).get("text") or scalars(node).get("prompt")
        if not (isinstance(txt, str) and txt.strip()):
            continue
        title = title_of(node)
        if "negativ" in title and result["negative_prompt"] is None:
            result["negative_prompt"] = txt
        elif "positiv" in title and result["positive_prompt"] is None:
            result["positive_prompt"] = txt

    # Pass 3: follow the sampler's positive/negative links, resolving through
    # string/concat nodes (handles prompts assembled dynamically)
    if result["positive_prompt"] is None or result["negative_prompt"] is None:
        for _nid, node in nodes:
            if "sampler" not in str(node.get("class_type", "")).lower():
                continue
            si = node.get("inputs", {})
            if result["positive_prompt"] is None:
                result["positive_prompt"] = _resolve_text(graph, si.get("positive"))
            if result["negative_prompt"] is None:
                result["negative_prompt"] = _resolve_text(graph, si.get("negative"))
            if result["positive_prompt"]:
                break

    # Pass 4: last resort — first encode node's literal text that is NOT a negative
    if result["positive_prompt"] is None:
        for _nid, node in nodes:
            ct = str(node.get("class_type", "")).lower()
            if "encode" not in ct and "cliptext" not in ct and "text" not in ct:
                continue
            if "negativ" in title_of(node):
                continue
            txt = scalars(node).get("text") or scalars(node).get("prompt")
            if isinstance(txt, str) and txt.strip():
                result["positive_prompt"] = txt
                break

    # LoRAs — scalar loaders (LoraLoader, WanVideoLoraSelect) plus rgthree
    # Power Lora Loader, which nests entries as lora_1: {on, lora, strength}
    for _nid, node in nodes:
        ct = str(node.get("class_type", "")).lower()
        if not any(h in ct for h in LORA_HINTS):
            continue
        inp = node.get("inputs", {})
        name = next((inp[k] for k in ("lora", "lora_name", "name")
                     if isinstance(inp.get(k), str)), None)
        if name:
            weight = next((float(inp[k]) for k in ("strength", "strength_model", "weight", "lora_strength")
                           if isinstance(inp.get(k), (int, float))), None)
            result["loras"].append({"name": name, "weight": weight})
        for v in inp.values():
            if isinstance(v, dict) and isinstance(v.get("lora"), str):
                if v.get("on") is False:
                    continue
                w = v.get("strength")
                result["loras"].append({
                    "name": v["lora"],
                    "weight": float(w) if isinstance(w, (int, float)) else None,
                })
    _seen, _uniq = set(), []
    for lora in result["loras"]:
        key = (lora["name"], lora["weight"])
        if key not in _seen:
            _seen.add(key)
            _uniq.append(lora)
    result["loras"] = _uniq

    # Sampler settings + seed
    for _nid, node in nodes:
        ct = str(node.get("class_type", "")).lower()
        if not any(h in ct for h in SAMPLER_HINTS):
            continue
        inp = scalars(node)
        result["seed"] = result["seed"] or _str_or_none(inp.get("seed") or inp.get("noise_seed"))
        result["steps"] = result["steps"] or _int_or_none(inp.get("steps"))
        result["cfg"] = result["cfg"] or _float_or_none(inp.get("cfg"))
        result["shift"] = result["shift"] or _float_or_none(inp.get("shift"))
        if isinstance(inp.get("sampler_name"), str):
            result["sampler"] = result["sampler"] or inp["sampler_name"]
        if isinstance(inp.get("scheduler"), str):
            result["scheduler"] = result["scheduler"] or inp["scheduler"]

    # Model / checkpoint
    for _nid, node in nodes:
        ct = str(node.get("class_type", "")).lower()
        if any(s in ct for s in MODEL_SKIP):
            continue
        inp = node.get("inputs", {})
        for f in MODEL_FIELDS:
            v = inp.get(f)
            if isinstance(v, str) and v.lower().endswith(MODEL_EXTS):
                result["model"] = v
                break
        if result["model"]:
            break

    return result


# ---- A1111 -------------------------------------------------------------------


def parse_a1111(text: str) -> dict:
    result = _empty()
    result["source"] = "a1111"
    neg = "Negative prompt:"
    steps_marker = "Steps:"
    neg_idx = text.find(neg)
    if neg_idx != -1:
        result["positive_prompt"] = text[:neg_idx].strip() or None
        rest = text[neg_idx + len(neg):]
    else:
        rest = ""
        result["positive_prompt"] = text.strip() or None
    steps_idx = rest.find(steps_marker)
    if steps_idx != -1:
        result["negative_prompt"] = rest[:steps_idx].strip() or None
        settings = rest[steps_idx:]
    else:
        result["negative_prompt"] = rest.strip() or None
        settings = ""
    for m in re.finditer(r"([A-Za-z][A-Za-z ]*?):\s*([^,]+)", settings):
        key, val = m.group(1).strip().lower(), m.group(2).strip()
        if key == "steps":
            result["steps"] = _int_or_none(val)
        elif key == "cfg scale":
            result["cfg"] = _float_or_none(val)
        elif key == "seed":
            result["seed"] = val
        elif key == "sampler":
            result["sampler"] = val
        elif key in ("model", "model hash"):
            result["model"] = result["model"] or val
    src = result["positive_prompt"] or ""
    for m in re.finditer(r"<lora:([^:>]+):([0-9.]+)>", src):
        result["loras"].append({"name": m.group(1), "weight": _float_or_none(m.group(2))})
    return result


# ---- orchestration -----------------------------------------------------------


def _apply_video(data: dict, path: Path) -> None:
    probe = _ffprobe_json(path)
    for s in probe.get("streams", []):
        if s.get("codec_type") == "video":
            data["width"] = s.get("width")
            data["height"] = s.get("height")
            break
    data["duration"] = _float_or_none((probe.get("format") or {}).get("duration"))
    tags = (probe.get("format") or {}).get("tags") or {}
    graph = find_graph(tags)
    if graph:
        parsed = parse_comfy_graph(graph)
        for k, v in parsed.items():
            if v not in (None, [], ""):
                data[k] = v
        data["raw_graph"] = json.dumps(graph)[:1_000_000]


def _apply_image(data: dict, path: Path) -> None:
    info: dict = {}
    try:
        from PIL import Image
        with Image.open(path) as img:
            data["width"], data["height"] = img.size
            info = dict(img.info or {})
    except Exception:
        pass
    if isinstance(info.get("parameters"), str):
        parsed = parse_a1111(info["parameters"])
        for k, v in parsed.items():
            if v not in (None, [], ""):
                data[k] = v
        data["raw_graph"] = info["parameters"][:1_000_000]
        return
    graph = find_graph(info)
    if graph:
        parsed = parse_comfy_graph(graph)
        for k, v in parsed.items():
            if v not in (None, [], ""):
                data[k] = v
        data["raw_graph"] = json.dumps(graph)[:1_000_000]


def extract(path: Path) -> dict:
    """Return a full metadata dict for a media file (best-effort, never raises)."""
    data = _empty()
    data["media_type"] = config.media_type_for(path)
    try:
        data["filesize"] = path.stat().st_size
    except OSError:
        pass
    try:
        if data["media_type"] in ("video", "audio"):
            _apply_video(data, path)
        elif data["media_type"] == "image":
            _apply_image(data, path)
    except Exception:
        pass
    return data


if __name__ == "__main__":
    import sys
    target = Path(sys.argv[1])
    meta = extract(target)
    meta["raw_graph"] = f"<{len(meta['raw_graph'])} chars>" if meta.get("raw_graph") else None
    print(json.dumps(meta, indent=2, ensure_ascii=False))
