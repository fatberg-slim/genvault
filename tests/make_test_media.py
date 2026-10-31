"""Generate the 11 placeholder test files (6 clips with a fake ComfyUI graph,
4 images, 1 sound) used by the checks. Usage: make_test_media.py <out_dir>"""
import json
import subprocess
import sys
from pathlib import Path

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
graph = json.dumps({
    "3": {"class_type": "KSampler", "inputs": {"seed": 12345, "steps": 20, "cfg": 6.5,
                                               "sampler_name": "euler", "scheduler": "normal"}},
    "6": {"class_type": "CLIPTextEncode", "_meta": {"title": "Positive Prompt"},
          "inputs": {"text": "a test pattern drifting over a calm sea"}},
    "7": {"class_type": "CLIPTextEncode", "_meta": {"title": "Negative Prompt"}, "inputs": {"text": "blurry"}},
    "10": {"class_type": "LoraLoader", "inputs": {"lora_name": "test_lora_v1.safetensors", "strength_model": 0.8}},
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "wan2.1_test.safetensors"}},
})
ff = ["ffmpeg", "-hide_banner", "-v", "error", "-y"]
for i in range(1, 7):
    src = "testsrc2=s=1280x720:r=24:d=12" if i % 2 else "mandelbrot=s=1280x720:r=24"
    subprocess.run(ff + ["-f", "lavfi", "-i", src, "-t", "12", "-metadata", f"comment={graph}",
                         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
                         str(out / f"test clip {i}.mp4")], check=True)
for i in range(1, 5):
    subprocess.run(ff + ["-f", "lavfi", "-i", "testsrc=s=768x768", "-frames:v", "1",
                         str(out / f"test image {i}.png")], check=True)
subprocess.run(ff + ["-f", "lavfi", "-i", "sine=frequency=440:duration=8", str(out / "test sound 1.mp3")], check=True)
print(sorted(p.name for p in out.iterdir()))
