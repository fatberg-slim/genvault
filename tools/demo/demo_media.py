"""Generate good-looking, fully synthetic demo media for genvault screenshots.

Procedural scenes (Pillow) -> mp4 via ffmpeg pipe / png, with fictional
ComfyUI or A1111 metadata embedded, plus a few synthesized audio tracks.
Writes manifest.json describing how each file should be organized.
"""
import json
import math
import random
import struct
import subprocess
import sys
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, PngImagePlugin

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
VW, VH, FPS, SECS = 854, 480, 24, 6


# ---------------------------------------------------------------- helpers
def mix(c1, c2, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def vgrad(w, h, stops):
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        for i in range(len(stops) - 1):
            (p0, c0), (p1, c1) = stops[i], stops[i + 1]
            if p0 <= t <= p1:
                d.line([(0, y), (w, y)], fill=mix(c0, c1, (t - p0) / max(1e-6, p1 - p0)))
                break
    return img


def ridge(w, h, base, amp, seed, offset=0.0, rough=1.0):
    r = random.Random(seed)
    waves = [(r.uniform(0.002, 0.006) * rough, r.uniform(0, 6.28), r.uniform(0.4, 1.0)) for _ in range(3)]
    waves += [(r.uniform(0.012, 0.03) * rough, r.uniform(0, 6.28), r.uniform(0.08, 0.2)) for _ in range(3)]
    norm = sum(a for _, _, a in waves)
    pts = []
    for x in range(0, w + 8, 8):
        v = sum(a * math.sin((x + offset) * f * 854 / w + p) for f, p, a in waves) / norm
        pts.append((x, base - v * amp))
    return pts + [(w, h), (0, h)]


def glow(radius, color, strength=2.2):
    size = int(radius * 2 * strength)
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = size // 2
    for i in range(12, 0, -1):
        rr = radius * strength * i / 12
        d.ellipse([c - rr, c - rr, c + rr, c + rr], fill=color + (int(10 + 6 * (12 - i)),))
    return im.filter(ImageFilter.GaussianBlur(radius * 0.35))


# ---------------------------------------------------------------- scenes
class Sunset:
    def __init__(self, w, h, seed, pal):
        self.w, self.h, self.seed, self.pal = w, h, seed, pal
        self.bg = vgrad(w, h, pal["sky"])
        self.glow = glow(h * 0.09, pal["sun"])

    def frame(self, t):
        w, h, p = self.w, self.h, self.pal
        im = self.bg.copy().convert("RGBA")
        sy = h * (0.42 + 0.12 * t)
        sx = w * p.get("sun_x", 0.62)
        g = self.glow
        im.alpha_composite(g, (int(sx - g.width / 2), int(sy - g.height / 2)))
        d = ImageDraw.Draw(im)
        r = h * 0.085
        d.ellipse([sx - r, sy - r, sx + r, sy + r], fill=p["sun"])
        for i, col in enumerate(p["layers"]):
            base = h * (0.58 + 0.11 * i)
            d.polygon(ridge(w, h, base, h * (0.16 - 0.03 * i), self.seed + i, offset=t * 160 * (i + 1)), fill=col)
        return im.convert("RGB")


class Synthwave:
    def __init__(self, w, h, seed, pal):
        self.w, self.h, self.pal = w, h, pal
        self.hz = int(h * 0.6)
        self.bg = vgrad(w, h, pal["sky"])
        self.glow = glow(h * 0.2, pal["sun_top"], 1.6)
        self.mount = ridge(w, h, self.hz, h * 0.1, seed, rough=1.6)

    def frame(self, t):
        w, h, p, hz = self.w, self.h, self.pal, self.hz
        im = self.bg.copy().convert("RGBA")
        cx, cy, r = w * 0.5, hz - h * 0.06, h * 0.2
        im.alpha_composite(self.glow, (int(cx - self.glow.width / 2), int(cy - self.glow.height / 2)))
        sun = vgrad(int(2 * r), int(2 * r), [(0, p["sun_top"]), (1, p["sun_bottom"])]).convert("RGBA")
        mask = Image.new("L", sun.size, 0)
        md = ImageDraw.Draw(mask)
        md.ellipse([0, 0, sun.width - 1, sun.height - 1], fill=255)
        step = sun.height * 0.075
        for i in range(7):                       # retro stripes, scrolling down
            y = sun.height * 0.3 + i * step + (t * step) % step
            md.rectangle([0, y, sun.width, y + 1 + i * 1.3], fill=0)
        sun.putalpha(mask)
        im.alpha_composite(sun, (int(cx - r), int(cy - r)))
        d = ImageDraw.Draw(im)
        d.polygon(self.mount, fill=p["mountain"])
        d.rectangle([0, hz, w, h], fill=p["floor"])
        for i in range(14):                      # horizontal grid lines moving towards camera
            z = (i + (1 - t) % 1) / 14
            y = hz + (h - hz) * (z ** 2.2)
            d.line([(0, y), (w, y)], fill=p["grid"], width=1 + int(z * 2))
        for i in range(-12, 13):                 # converging verticals
            x0 = w / 2 + i * w * 0.012
            x1 = w / 2 + i * w * 0.16
            d.line([(x0, hz), (x1, h)], fill=p["grid"], width=2)
        return im.convert("RGB")


class Ocean:
    def __init__(self, w, h, seed, pal):
        self.w, self.h, self.pal = w, h, pal
        self.hz = int(h * 0.55)
        self.bg = vgrad(w, h, pal["sky"])
        sea = vgrad(w, h - self.hz, pal["sea"])
        self.bg.paste(sea, (0, self.hz))
        self.glow = glow(h * 0.06, pal["moon"], 2.8)
        r = random.Random(seed)
        self.stars = [(r.uniform(0, w), r.uniform(0, self.hz * 0.8), r.random()) for _ in range(90)]

    def frame(self, t):
        w, h, p, hz = self.w, self.h, self.pal, self.hz
        im = self.bg.copy().convert("RGBA")
        d = ImageDraw.Draw(im)
        for x, y, b in self.stars:
            a = 0.5 + 0.5 * math.sin(t * 6.28 * 2 + b * 30)
            c = mix(p["sky"][0][1], (255, 255, 255), 0.3 + 0.6 * a * b)
            d.point((x, y), fill=c)
        mx, my, r = w * 0.7, h * 0.22, h * 0.055
        im.alpha_composite(self.glow, (int(mx - self.glow.width / 2), int(my - self.glow.height / 2)))
        d = ImageDraw.Draw(im)
        d.ellipse([mx - r, my - r, mx + r, my + r], fill=p["moon"])
        n = 22
        for i in range(n):                       # waves: short glints, never crossing
            z = (i + 1) / n
            y0 = hz + (h - hz) * z ** 1.7
            gap = (h - hz) * (((i + 2) / n) ** 1.7 - z ** 1.7)
            amp = gap * 0.25
            r = random.Random(i * 13)
            for _ in range(int(10 + 22 * z)):
                cx = (r.uniform(0, w) + t * w * 0.05 * (1 if i % 2 else -1)) % w
                seg = r.uniform(12, 50) * (0.3 + z)
                ph = r.uniform(0, 6.28)
                yy = y0 + amp * math.sin(t * 6.28 * 2 + ph)
                d.line([(cx - seg / 2, yy), (cx + seg / 2, yy)],
                       fill=mix(p["sea"][0][1], p["wave"], 0.2 + 0.45 * z), width=1 + int(z * 2))
        for i in range(18):                      # moon reflection
            z = i / 18
            y = hz + 6 + (h - hz) * z * 0.9
            half = 6 + 40 * z * (0.6 + 0.4 * math.sin(t * 6.28 * 3 + i * 1.7))
            d.line([(mx - half, y), (mx + half, y)], fill=mix(p["moon"], p["sea"][0][1], 0.35 + 0.4 * z), width=2)
        return im.convert("RGB")


class Starfield:
    def __init__(self, w, h, seed, pal):
        self.w, self.h, self.pal = w, h, pal
        r = random.Random(seed)
        neb = Image.new("RGB", (w * 2, h * 2), pal["bg"])
        nd = ImageDraw.Draw(neb)
        for _ in range(26):
            cx, cy = r.uniform(0, w * 2), r.uniform(0, h * 2)
            rr = r.uniform(h * 0.15, h * 0.5)
            nd.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=r.choice(pal["nebula"]))
        self.neb = neb.filter(ImageFilter.GaussianBlur(h * 0.12))
        self.stars = [(r.uniform(-1, 1), r.uniform(-1, 1), r.uniform(0.05, 1)) for _ in range(380)]

    def frame(self, t):
        w, h = self.w, self.h
        ox, oy = int(w * 0.5 * t), int(h * 0.25 * t)
        im = self.neb.crop((ox, oy, ox + w, oy + h))
        d = ImageDraw.Draw(im)
        for sx, sy, z in self.stars:
            zz = (z - t * 0.5) % 1 + 0.02
            x, y = w / 2 + sx / zz * w * 0.12, h / 2 + sy / zz * h * 0.12
            if 0 <= x < w and 0 <= y < h:
                s = max(0.6, 2.2 * (1 - zz))
                c = int(140 + 115 * (1 - zz))
                d.ellipse([x - s, y - s, x + s, y + s], fill=(c, c, min(255, c + 20)))
        return im


class Forest:
    def __init__(self, w, h, seed, pal):
        self.w, self.h, self.pal, self.seed = w, h, pal, seed
        self.bg = vgrad(w, h, pal["sky"])
        r = random.Random(seed)
        self.layers = [[(r.uniform(0, w * 2), r.uniform(0.7, 1.3)) for _ in range(14 + 6 * i)] for i in range(4)]

    def frame(self, t):
        w, h, p = self.w, self.h, self.pal
        im = self.bg.copy().convert("RGBA")
        for li, trees in enumerate(self.layers):
            layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            d = ImageDraw.Draw(layer)
            base = h * (0.62 + 0.11 * li)
            th = h * (0.22 + 0.08 * li)
            col = p["trees"][li]
            for x0, s in trees:
                x = (x0 - t * 40 * (li + 1)) % (w * 2) - w * 0.25
                for k in range(3):
                    tw = th * 0.32 * s * (1 - k * 0.22)
                    top = base - th * s + k * th * s * 0.25
                    d.polygon([(x, top), (x - tw, top + th * s * 0.55), (x + tw, top + th * s * 0.55)], fill=col)
                d.rectangle([x - 2 - li, base - th * s * 0.2, x + 2 + li, h], fill=col)
            d.rectangle([0, base, w, h], fill=col)
            im.alpha_composite(layer)
            if li >= len(self.layers) - 2:          # fog only between the back layers
                continue
            fog = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            fd = ImageDraw.Draw(fog)
            fx = (t * 120 * (li + 1)) % w
            for k in range(3):
                cx = (fx + k * w / 2.5) % (w * 1.4) - w * 0.2
                fd.ellipse([cx - w * 0.35, base - h * 0.05, cx + w * 0.35, base + h * 0.08], fill=p["fog"] + (40,))
            im.alpha_composite(fog.filter(ImageFilter.GaussianBlur(18)))
        return im.convert("RGB")


class Aurora:
    def __init__(self, w, h, seed, pal):
        self.w, self.h, self.pal, self.seed = w, h, pal, seed
        self.bg = vgrad(w, h, pal["sky"])
        r = random.Random(seed)
        d = ImageDraw.Draw(self.bg)
        for _ in range(160):
            x, y, b = r.uniform(0, w), r.uniform(0, h * 0.7), r.uniform(120, 255)
            d.point((x, y), fill=(int(b), int(b), int(b)))
        self.front = ridge(w, h, h * 0.82, h * 0.1, seed + 5, rough=1.3)
        self.back = ridge(w, h, h * 0.74, h * 0.12, seed + 9)

    def frame(self, t):
        w, h, p = self.w, self.h, self.pal
        im = self.bg.copy().convert("RGBA")
        band = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(band)
        for i, col in enumerate(p["bands"]):
            y0 = h * (0.25 + 0.09 * i)
            for k in range(10):
                pts = [(x, y0 + k * 3 + h * 0.07 * math.sin(x * 0.006 + t * 6.28 + i * 1.3)
                        + h * 0.03 * math.sin(x * 0.017 - t * 6.28 * 2 + i)) for x in range(0, w + 10, 10)]
                d.line(pts, fill=col + (int(120 - k * 10),), width=6)
        im.alpha_composite(band.filter(ImageFilter.GaussianBlur(9)))
        d = ImageDraw.Draw(im)
        d.polygon(self.back, fill=p["back"])
        d.polygon(self.front, fill=p["front"])
        return im.convert("RGB")


PAL = {
    "sunset_gold": dict(sky=[(0, (36, 24, 72)), (0.45, (220, 92, 76)), (0.62, (255, 178, 92)), (1, (255, 214, 140))],
                        sun=(255, 236, 170), layers=[(150, 70, 90), (96, 40, 78), (52, 22, 56), (24, 10, 32)]),
    "sunset_rose": dict(sky=[(0, (20, 30, 70)), (0.5, (180, 90, 150)), (0.7, (250, 150, 140)), (1, (255, 200, 170))],
                        sun=(255, 225, 200), sun_x=0.35, layers=[(120, 80, 130), (80, 50, 100), (46, 28, 70), (20, 12, 40)]),
    "highland": dict(sky=[(0, (40, 80, 140)), (0.5, (140, 170, 210)), (0.7, (240, 200, 160)), (1, (250, 225, 190))],
                     sun=(255, 245, 220), sun_x=0.25, layers=[(90, 110, 140), (60, 80, 100), (36, 54, 64), (18, 30, 34)]),
    "synth_pink": dict(sky=[(0, (10, 4, 30)), (0.45, (60, 10, 90)), (0.6, (200, 40, 140))], sun_top=(255, 220, 90),
                       sun_bottom=(255, 40, 150), mountain=(30, 6, 50), floor=(14, 2, 28), grid=(255, 60, 200)),
    "synth_cyan": dict(sky=[(0, (2, 6, 28)), (0.45, (10, 30, 90)), (0.6, (20, 120, 170))], sun_top=(120, 255, 240),
                       sun_bottom=(160, 60, 255), mountain=(6, 14, 46), floor=(2, 6, 22), grid=(60, 220, 255)),
    "night_sea": dict(sky=[(0, (6, 10, 30)), (1, (30, 50, 100))], sea=[(0, (14, 30, 66)), (1, (4, 10, 26))],
                      moon=(240, 240, 220), wave=(120, 160, 220)),
    "dusk_sea": dict(sky=[(0, (40, 30, 90)), (0.7, (230, 120, 110)), (1, (255, 190, 140))], sea=[(0, (90, 70, 120)), (1, (20, 20, 50))],
                     moon=(255, 230, 190), wave=(255, 190, 170)),
    "nebula_violet": dict(bg=(4, 2, 14), nebula=[(70, 20, 110), (20, 40, 120), (130, 30, 90), (10, 80, 110)]),
    "nebula_teal": dict(bg=(2, 8, 12), nebula=[(10, 90, 100), (20, 50, 120), (60, 120, 80), (10, 30, 60)]),
    "forest_mist": dict(sky=[(0, (150, 180, 175)), (1, (220, 230, 215))], fog=(235, 240, 230),
                        trees=[(120, 150, 140), (80, 110, 100), (44, 70, 62), (18, 36, 32)]),
    "forest_dusk": dict(sky=[(0, (60, 50, 90)), (0.6, (200, 130, 110)), (1, (240, 190, 150))], fog=(240, 200, 190),
                        trees=[(110, 80, 100), (70, 50, 70), (40, 28, 46), (16, 12, 22)]),
    "aurora_green": dict(sky=[(0, (2, 6, 20)), (1, (10, 30, 50))], bands=[(60, 255, 160), (40, 200, 220), (150, 90, 255)],
                         back=(14, 26, 40), front=(4, 10, 16)),
    "aurora_violet": dict(sky=[(0, (8, 2, 24)), (1, (24, 14, 50))], bands=[(200, 80, 255), (90, 140, 255), (60, 255, 200)],
                          back=(24, 16, 44), front=(8, 4, 16)),
}
SCENES = {"sunset": Sunset, "synth": Synthwave, "ocean": Ocean, "stars": Starfield, "forest": Forest, "aurora": Aurora}


# ---------------------------------------------------------------- metadata
def comfy_graph(pos, neg, seed, model, loras, steps=30, cfg=6.0, sampler="euler", scheduler="normal",
                video=False, shift=None):
    g = {}
    if video:
        g["11"] = {"class_type": "WanVideoModelLoader", "inputs": {"model": model, "precision": "bf16"}}
        g["16"] = {"class_type": "WanVideoTextEncode", "inputs": {"positive_prompt": pos, "negative_prompt": neg}}
        g["27"] = {"class_type": "WanVideoSampler", "inputs": {"seed": seed, "steps": steps, "cfg": cfg,
                                                               "shift": shift or 8.0, "scheduler": scheduler}}
        for i, (name, wgt) in enumerate(loras):
            g[str(40 + i)] = {"class_type": "WanVideoLoraSelect", "inputs": {"lora": name, "strength": wgt}}
    else:
        g["4"] = {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": model}}
        g["6"] = {"class_type": "CLIPTextEncode", "_meta": {"title": "Positive"}, "inputs": {"text": pos}}
        g["7"] = {"class_type": "CLIPTextEncode", "_meta": {"title": "Negative"}, "inputs": {"text": neg}}
        g["3"] = {"class_type": "KSampler", "inputs": {"seed": seed, "steps": steps, "cfg": cfg,
                                                       "sampler_name": sampler, "scheduler": scheduler}}
        for i, (name, wgt) in enumerate(loras):
            g[str(10 + i)] = {"class_type": "LoraLoader", "inputs": {"lora_name": name, "strength_model": wgt}}
    return g


def a1111_params(pos, neg, seed, model, loras, steps=28, cfg=7.0, sampler="DPM++ 2M Karras"):
    lora_tags = " ".join(f"<lora:{n.rsplit('.', 1)[0]}:{w}>" for n, w in loras)
    return (f"{pos} {lora_tags}\nNegative prompt: {neg}\n"
            f"Steps: {steps}, Sampler: {sampler}, CFG scale: {cfg}, Seed: {seed}, Size: 1024x576, Model: {model.rsplit('.', 1)[0]}")


NEG = "blurry, low quality, jpeg artifacts, watermark, text, oversaturated"
NEG_V = "static, frozen frame, flicker, low quality, distorted, watermark"

# (file, scene, palette, seed, project, sub, kind, prompt, model, loras, extras)
VIDEOS = [
    ("NeonDrift_00012.mp4", "synth", "synth_pink", 1, "Neon Drift", "Episode 1", "clip",
     "retro synthwave landscape, glowing neon grid floor rushing towards camera, striped sunset sun, purple haze, 80s aesthetic, smooth camera dolly",
     "wan2.1_t2v_14B_fp8.safetensors", [("retrowave_style_v3.safetensors", 0.8)]),
    ("NeonDrift_00015.mp4", "synth", "synth_cyan", 2, "Neon Drift", "Episode 1", "clip",
     "cyan synthwave horizon at night, neon grid, low mountains, vaporwave sun, cinematic, slow forward motion",
     "wan2.1_t2v_14B_fp8.safetensors", [("retrowave_style_v3.safetensors", 0.75), ("film_grain_lite.safetensors", 0.4)]),
    ("NeonDrift_ep2_00003.mp4", "synth", "synth_pink", 7, "Neon Drift", "Episode 2", "clip",
     "endless neon highway grid, magenta sky, retro sun with horizontal stripes, outrun style, dreamy",
     "wan2.1_t2v_14B_fp8.safetensors", [("retrowave_style_v3.safetensors", 0.9)]),
    ("coast_golden_00004.mp4", "ocean", "dusk_sea", 3, "Coastal Dreams", "Golden Hour", "clip",
     "calm ocean at dusk, gentle waves, warm pink and orange sky, moon rising, reflections shimmering on the water",
     "wan2.1_i2v_480p_fp8.safetensors", [("calm_waters_v1.safetensors", 0.7)]),
    ("coast_moonlight_00009.mp4", "ocean", "night_sea", 4, "Coastal Dreams", "Moonlight", "clip",
     "moonlit sea at night, twinkling stars, silver moon reflection dancing on the waves, peaceful, cinematic wide shot",
     "wan2.1_i2v_480p_fp8.safetensors", [("calm_waters_v1.safetensors", 0.65), ("night_sky_detail.safetensors", 0.5)]),
    ("coast_sunset_00002.mp4", "sunset", "sunset_rose", 5, "Coastal Dreams", "Golden Hour", "clip",
     "rose colored sunset over layered coastal hills, sun slowly setting, soft atmospheric haze, parallax",
     "wan2.1_t2v_14B_fp8.safetensors", []),
    ("aurora_v2_00007.mp4", "aurora", "aurora_green", 6, "Northern Lights", "Night Sky", "clip",
     "green aurora borealis dancing over dark mountains, starry arctic sky, long exposure feel, majestic",
     "wan2.1_t2v_14B_fp8.safetensors", [("aurora_glow_v2.safetensors", 0.85)]),
    ("aurora_v2_00011.mp4", "aurora", "aurora_violet", 8, "Northern Lights", "Night Sky", "clip",
     "violet and blue aurora ribbons waving across the night sky above mountain silhouettes, magical atmosphere",
     "wan2.1_t2v_14B_fp8.safetensors", [("aurora_glow_v2.safetensors", 0.9)]),
    ("deep_space_00021.mp4", "stars", "nebula_violet", 9, "Northern Lights", "Deep Space", "clip",
     "flying through a violet nebula, stars streaking past the camera, deep space, cosmic dust, hyperspace",
     "wan2.1_t2v_14B_fp8.safetensors", [("space_nebula_xl.safetensors", 0.7)]),
    ("deep_space_00024.mp4", "stars", "nebula_teal", 10, "Northern Lights", "Deep Space", "clip",
     "teal nebula clouds, travelling through a field of stars, calm cosmic journey",
     "wan2.1_t2v_14B_fp8.safetensors", [("space_nebula_xl.safetensors", 0.6)]),
    ("woods_mist_00005.mp4", "forest", "forest_mist", 11, "Whispering Woods", "Mist", "clip",
     "misty pine forest, layered tree silhouettes, drifting fog, soft morning light, tranquil, slow pan",
     "wan2.1_t2v_14B_fp8.safetensors", [("misty_forest_v1.safetensors", 0.8)]),
    ("woods_dusk_00008.mp4", "forest", "forest_dusk", 12, "Whispering Woods", "Dusk", "clip",
     "pine forest at dusk, pink evening sky, fog rolling between the trees, fairytale mood",
     "wan2.1_t2v_14B_fp8.safetensors", [("misty_forest_v1.safetensors", 0.7)]),
    ("highlands_00001.mp4", "sunset", "highland", 13, "Highlands", None, "clip",
     "golden morning over misty highland ridges, sun rising, layered blue mountains, aerial parallax",
     "wan2.1_t2v_14B_fp8.safetensors", [("landscape_depth.safetensors", 0.6)]),
    ("highlands_00006.mp4", "sunset", "sunset_gold", 14, "Highlands", None, "clip",
     "dramatic golden sunset behind mountain ranges, warm light, purple shadows, epic landscape",
     "wan2.1_t2v_14B_fp8.safetensors", [("landscape_depth.safetensors", 0.7)]),
]

IMAGES = [
    ("ComfyUI_00421_.png", "synth", "synth_pink", 21, (1024, 576), "Neon Drift", "Episode 1", "image", "comfy",
     "synthwave poster, neon grid and striped sun, purple night, retro 80s artwork, high detail",
     "dreamscape_xl_v2.safetensors", [("retrowave_style_v3.safetensors", 0.8)]),
    ("ComfyUI_00433_.png", "synth", "synth_cyan", 22, (832, 1088), "Neon Drift", "Episode 2", "image", "comfy",
     "vertical synthwave wallpaper, cyan neon horizon, retro sun, starless sky, clean composition",
     "dreamscape_xl_v2.safetensors", [("retrowave_style_v3.safetensors", 0.7)]),
    ("coast_still_0007.png", "ocean", "night_sea", 23, (1024, 576), "Coastal Dreams", "Moonlight", "image", "a1111",
     "moonlight over the ocean, silver reflections, starry sky, calm night, photorealistic",
     "serene_realism_v5.safetensors", [("calm_waters_v1.safetensors", 0.6)]),
    ("coast_still_0011.png", "sunset", "sunset_rose", 24, (1024, 576), "Coastal Dreams", "Golden Hour", "image", "a1111",
     "pastel sunset over coastal hills, layered silhouettes, soft haze, dreamy",
     "serene_realism_v5.safetensors", []),
    ("aurora_still_02.png", "aurora", "aurora_green", 25, (1024, 576), "Northern Lights", "Night Sky", "image", "comfy",
     "aurora borealis over snowy peaks, green light ribbons, star field, wide angle",
     "dreamscape_xl_v2.safetensors", [("aurora_glow_v2.safetensors", 0.8)]),
    ("nebula_wallpaper_01.png", "stars", "nebula_violet", 26, (1024, 576), "Northern Lights", "Deep Space", "image", "comfy",
     "deep space wallpaper, violet nebula, scattered stars, cosmic, 8k detail",
     "dreamscape_xl_v2.safetensors", [("space_nebula_xl.safetensors", 0.7)]),
    ("woods_cover_v1.png", "forest", "forest_mist", 27, (832, 1088), "Whispering Woods", "Mist", "image", "comfy",
     "book cover art, foggy pine forest, layered silhouettes, muted green tones, minimal",
     "dreamscape_xl_v2.safetensors", [("misty_forest_v1.safetensors", 0.75)]),
    ("woods_cover_v2.png", "forest", "forest_dusk", 28, (832, 1088), "Whispering Woods", "Dusk", "image", "comfy",
     "pine forest at dusk, pink sky, fog, storybook illustration, soft gradients",
     "dreamscape_xl_v2.safetensors", [("misty_forest_v1.safetensors", 0.7)]),
    ("highlands_print_A.png", "sunset", "highland", 29, (1024, 576), "Highlands", None, "image", "a1111",
     "blue highland ridges at sunrise, layered mountains, minimalist landscape print",
     "serene_realism_v5.safetensors", [("landscape_depth.safetensors", 0.6)]),
    ("highlands_print_B.png", "sunset", "sunset_gold", 30, (1024, 576), "Highlands", None, "image", "comfy",
     "golden hour mountain panorama, warm sun, purple foothills, minimalist art print",
     "dreamscape_xl_v2.safetensors", [("landscape_depth.safetensors", 0.65)]),
]

# unsorted inbox items (no project) — a mix
INBOX = [
    ("wan_test_00031.mp4", "ocean", "dusk_sea", 41, "video",
     "test render, dusk ocean with gentle waves, pink sky", "wan2.1_t2v_14B_fp8.safetensors", []),
    ("ComfyUI_00502_.png", "aurora", "aurora_violet", 42, (1024, 576),
     "violet aurora study, mountains, night", "dreamscape_xl_v2.safetensors", [("aurora_glow_v2.safetensors", 0.8)]),
    ("ComfyUI_00505_.png", "synth", "synth_cyan", 43, (1024, 576),
     "neon grid variation, cyan, retro", "dreamscape_xl_v2.safetensors", []),
    ("wan_test_00034.mp4", "stars", "nebula_teal", 44, "video",
     "starfield flythrough test, teal nebula", "wan2.1_t2v_14B_fp8.safetensors", []),
]
TRASHED = [
    ("wan_test_00029.mp4", "forest", "forest_mist", 45, "video", "Whispering Woods", "Mist", "clip",
     "early fog test, too fast", "wan2.1_t2v_14B_fp8.safetensors", []),
    ("ComfyUI_00418_.png", "sunset", "sunset_gold", 46, (1024, 576), "Highlands", None, "image",
     "sunset draft, composition off", "dreamscape_xl_v2.safetensors", []),
]


# ---------------------------------------------------------------- writers
def write_video(name, scene, pal, seed, meta_json):
    sc = SCENES[scene](VW, VH, seed, PAL[pal])
    out = OUT / name
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{VW}x{VH}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-crf", "19", "-preset", "medium", "-movflags", "+faststart", "-metadata", f"comment={meta_json}", str(out)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    n = FPS * SECS
    for i in range(n):
        p.stdin.write(sc.frame(i / n).tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise RuntimeError(f"ffmpeg failed for {name}")


def write_image(name, scene, pal, seed, size, info):
    sc = SCENES[scene](size[0], size[1], seed, PAL[pal])
    img = sc.frame(random.Random(seed).uniform(0.1, 0.9))
    pi = PngImagePlugin.PngInfo()
    for k, v in info.items():
        pi.add_text(k, v)
    img.save(OUT / name, pnginfo=pi)


def write_audio(name, seed, bpm, scale_root, secs=24):
    r = random.Random(seed)
    sr = 32000
    semis = [0, 3, 5, 7, 10, 12, 15]
    beat = 60 / bpm / 2
    notes = [scale_root * 2 ** (r.choice(semis) / 12) for _ in range(int(secs / beat) + 1)]
    chord = [scale_root / 2 * 2 ** (s / 12) for s in (0, 7, 12)]
    frames = bytearray()
    for i in range(int(secs * sr)):
        t = i / sr
        k = int(t / beat)
        nt = t - k * beat
        f = notes[k]
        env = math.exp(-nt * 6)
        v = 0.33 * env * (math.sin(2 * math.pi * f * t) + 0.3 * math.sin(4 * math.pi * f * t))
        v += 0.12 * sum(math.sin(2 * math.pi * c * t) for c in chord) / 3 * (0.6 + 0.4 * math.sin(t * 0.8))
        v *= min(1, t / 1.5, (secs - t) / 2)
        frames += struct.pack("<h", int(max(-1, min(1, v)) * 30000))
    wav = OUT / (Path(name).stem + ".wav")
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(wav), "-b:a", "160k",
                    str(OUT / name)], check=True)
    wav.unlink()


def main():
    manifest = []
    rnd = random.Random(2026)
    for (name, scene, pal, seed, proj, sub, kind, prompt, model, loras) in VIDEOS:
        g = comfy_graph(prompt, NEG_V, 100000 + seed * 7919, model, loras, steps=rnd.choice([20, 25, 30]),
                        cfg=rnd.choice([5.0, 6.0]), scheduler="unipc", video=True, shift=rnd.choice([5.0, 8.0]))
        write_video(name, scene, pal, seed, json.dumps(g))
        manifest.append(dict(file=name, project=proj, sub_project=sub, kind=kind))
        print("video", name, flush=True)
    for (name, scene, pal, seed, size, proj, sub, kind, fmt, prompt, model, loras) in IMAGES:
        s = 500000 + seed * 104729
        info = ({"prompt": json.dumps(comfy_graph(prompt, NEG, s, model, loras, steps=rnd.choice([28, 30, 35]),
                                                  cfg=rnd.choice([5.5, 6.5, 7.0]), sampler="dpmpp_2m", scheduler="karras"))}
                if fmt == "comfy" else {"parameters": a1111_params(prompt, NEG, s, model, loras)})
        write_image(name, scene, pal, seed, size, info)
        manifest.append(dict(file=name, project=proj, sub_project=sub, kind=kind))
        print("image", name, flush=True)
    for (name, scene, pal, seed, size_or_v, prompt, model, loras) in INBOX:
        if size_or_v == "video":
            write_video(name, scene, pal, seed, json.dumps(comfy_graph(prompt, NEG_V, seed * 31, model, loras, video=True)))
        else:
            write_image(name, scene, pal, seed, size_or_v, {"prompt": json.dumps(comfy_graph(prompt, NEG, seed * 31, model, loras))})
        manifest.append(dict(file=name, project=None))
        print("inbox", name, flush=True)
    for (name, scene, pal, seed, size_or_v, proj, sub, kind, prompt, model, loras) in TRASHED:
        if size_or_v == "video":
            write_video(name, scene, pal, seed, json.dumps(comfy_graph(prompt, NEG_V, seed * 31, model, loras, video=True)))
        else:
            write_image(name, scene, pal, seed, size_or_v, {"prompt": json.dumps(comfy_graph(prompt, NEG, seed * 31, model, loras))})
        manifest.append(dict(file=name, project=proj, sub_project=sub, kind=kind, trash=True))
        print("trash", name, flush=True)
    for name, seed, bpm, root, proj, sub, notes in [
        ("neon_drift_theme.mp3", 1, 110, 220.0, "Neon Drift", None, "Suno · synthwave, driving bassline, 110 bpm, retro arpeggios"),
        ("night_drive_loop.mp3", 2, 96, 196.0, "Neon Drift", "Episode 2", "Suno · darksynth loop, moody pads"),
        ("ocean_lullaby.mp3", 3, 72, 261.6, "Coastal Dreams", None, "Suno · ambient piano, calm waves, lullaby"),
        ("aurora_ambience.mp3", 4, 60, 174.6, "Northern Lights", None, "Suno · ethereal ambient, shimmering synth pads"),
    ]:
        write_audio(name, seed, bpm, root)
        manifest.append(dict(file=name, project=proj, sub_project=sub, kind="sound", notes=notes))
        print("audio", name, flush=True)
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print("done", len(manifest))


if __name__ == "__main__":
    main()
