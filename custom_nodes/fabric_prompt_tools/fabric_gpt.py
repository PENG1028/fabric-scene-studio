# -*- coding: utf-8 -*-
"""BYOK image generation with ordered references and an explicit local result cache."""
import base64
import csv
import datetime
import hashlib
import io
import json
import os
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import folder_paths
import numpy as np
import requests
import torch
from filelock import FileLock, Timeout
from PIL import Image as PILImage

API_BASE = "https://api.openai.com/v1"
KEY_FILE = Path(__file__).parent / "api_key.txt"
_OUT_DIR = Path(folder_paths.get_output_directory())
COST_LOG = _OUT_DIR / "fabric_cost_log.csv"
CACHE_DIR = _OUT_DIR / "fabric_cache"
MODELS = ["gpt-image-2", "gpt-image-2.5-sunburst-2026-09-08",
          "gpt-image-2.5-sunburst", "gpt-image-2.5-flare", "gpt-image-1.5", "gpt-image-1"]
SIZES = ["auto", "1024x1024", "1024x1536", "1536x1024", "2048x2048",
         "2048x1152", "1152x2048", "3840x2160", "2160x3840", "Custom"]
CACHE_MODES = ["reuse", "refresh", "off"]
PRICING_URL = "https://developers.openai.com/api/docs/pricing"
PRICING_DATE = "2026-09-09"


def estimate_usage_cost(model, usage):
    """Standard USD rates; missing usage is unknown, never zero consumption."""
    result = dict(currency="USD", usd_min=None, usd_max=None,
                  pricing_url=PRICING_URL, pricing_date=PRICING_DATE, assumptions=[])
    if model == "gpt-image-2" or model.startswith(("gpt-image-2.5-sunburst", "gpt-image-2.5-flare")):
        rates = dict(text_input=5, image_input=8, cached_text=1.25, cached_image=2, image_output=30, text_output=0)
    elif model == "gpt-image-1.5":
        rates = dict(text_input=5, image_input=8, cached_text=1.25, cached_image=2, image_output=32, text_output=10)
    elif model == "gpt-image-1":
        rates = dict(text_input=5, image_input=10, cached_text=1.25, cached_image=2.5, image_output=40, text_output=0)
    else:
        result["status"] = "unknown_model"
        return result
    result["rates_per_million"] = rates
    if not usage:
        result["status"] = "usage_unavailable"
        return result
    detail = usage.get("input_tokens_details", {}) or {}
    out = usage.get("output_tokens_details", {}) or {}
    required = [usage.get("input_tokens"), usage.get("output_tokens"), detail.get("text_tokens"), detail.get("image_tokens")]
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in required):
        result["status"] = "incomplete_usage"
        return result
    total_in, total_out, text_in, image_in = required
    if text_in + image_in != total_in:
        result["status"] = "inconsistent_usage"
        return result
    if not out:
        if model == "gpt-image-1.5":
            result["status"] = "incomplete_usage"
            return result
        out = dict(image_tokens=total_out, text_tokens=0)
        result["assumptions"].append("Images API 输出 token 全部按图像计价")
    image_out, text_out = out.get("image_tokens", 0), out.get("text_tokens", 0)
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in (image_out, text_out)) or image_out + text_out != total_out:
        result["status"] = "inconsistent_usage"
        return result
    if text_out and rates["text_output"] == 0:
        result["status"] = "unsupported_text_output"
        return result
    cached = detail.get("cached_tokens", usage.get("cached_tokens"))
    if cached is None:
        cached = 0
        result["assumptions"].append("接口未报告缓存 token，按未缓存输入估算；若账单有缓存优惠，实际费用更低")
    if not isinstance(cached, int) or isinstance(cached, bool) or not 0 <= cached <= total_in:
        result["status"] = "inconsistent_usage"
        return result
    cached_detail = detail.get("cached_tokens_details", {}) or {}
    if cached_detail and all(k in cached_detail for k in ("text_tokens", "image_tokens")):
        ct, ci = cached_detail["text_tokens"], cached_detail["image_tokens"]
        if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in (ct, ci)) or ct + ci != cached or ct > text_in or ci > image_in:
            result["status"] = "inconsistent_usage"
            return result
        allocations = [(ct, ci)]
    else:
        allocations = [(ct, cached - ct) for ct in (max(0, cached-image_in), min(cached,text_in))]
        if cached:
            result["assumptions"].append("缓存 token 未按模态拆分，按可行分配估算区间")
    costs = [((text_in-ct)*rates["text_input"] + ct*rates["cached_text"] +
              (image_in-ci)*rates["image_input"] + ci*rates["cached_image"] +
              image_out*rates["image_output"] + text_out*rates["text_output"]) / 1_000_000
             for ct, ci in allocations]
    result.update(status="estimated", usd_min=round(min(costs), 8), usd_max=round(max(costs), 8))
    return result


def _cost_text(cost):
    if cost.get("status") == "relay_pricing_unknown":
        return "中转站价格未配置，费用未知；以该站账单为准"
    if cost["usd_min"] is None:
        return "用量或拆分不足，费用未知"
    lo, hi = cost["usd_min"], cost["usd_max"]
    return f"估算 ${lo:.5f}" if lo == hi else f"估算 ${lo:.5f}–${hi:.5f}"


def normalize_api_base(value):
    value = (value or API_BASE).strip().rstrip("/")
    parts = urlsplit(value)
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("接口地址中不要包含密钥、账号、查询参数或 # 片段；密钥单独填写。")
    if not parts.hostname or any(ch.isspace() for ch in value):
        raise ValueError("请填写完整 API 地址，例如 https://api.openai.com/v1。")
    if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1")):
        raise ValueError("远程 API 地址需要 HTTPS；本机服务可使用 HTTP。")
    path = parts.path.rstrip("/")
    for endpoint in ("/images/generations", "/images/edits"):
        if path.endswith(endpoint):
            path = path[:-len(endpoint)]
            break
    path = path or "/v1"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def endpoint_cost(model, usage, api_base_url):
    if api_base_url == API_BASE:
        return estimate_usage_cost(model, usage)
    return dict(currency="USD", status="relay_pricing_unknown", usd_min=None, usd_max=None,
                pricing_url=None, pricing_date=None, assumptions=["中转站收费规则未知，不使用官方单价估算"])


def request_key(inline_key, api_key_file, api_base_url):
    if inline_key and inline_key.strip():
        return inline_key.strip()
    if api_key_file and api_key_file.strip():
        path = Path(api_key_file.strip()).expanduser()
        if not path.is_absolute():
            path = KEY_FILE.parent / path
        if not path.is_file():
            raise ValueError("找不到指定的密钥文件。请检查路径。")
        return path.read_text(encoding="utf-8-sig").strip()
    if api_base_url != API_BASE:
        raise ValueError("中转站需要填写对应 API Key 或密钥文件；不会自动把官方 Key 发给中转站。")
    return resolve_api_key()


def download_image(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("接口返回的图片下载地址需要是无账号密码的 HTTPS URL。")
    # Never reuse the image API's Authorization header on the download host.
    with requests.get(url, headers={}, timeout=(20, 120), stream=True, allow_redirects=False) as response:
        if response.status_code != 200:
            raise RuntimeError(f"图片下载 HTTP {response.status_code}；未重新生图。")
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > 50 * 1024 * 1024:
                raise ValueError("返回图片超过 50 MB，下载已停止；未重新生图。")
            chunks.append(chunk)
        return b"".join(chunks)


def _repair_csv_header(path, fieldnames):
    if not path.exists() or not path.stat().st_size:
        return
    with path.open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.reader(source))
    if rows[0] == fieldnames:
        return
    backup = path.with_name(path.stem + ".before-usage-fix.csv")
    if not backup.exists():
        shutil.copy2(path, backup)
    repaired = []
    for values in rows[1:]:
        header = rows[0]
        # Historical logger appended usage before error without updating its 13-column header.
        if "usage" not in header and len(values) == len(header) + 1:
            header = header[:-1] + ["usage", header[-1]]
        if len(values) != len(header):
            raise ValueError("旧 CSV 列数不一致，已备份原始日志，请检查。")
        old = dict(zip(header, values))
        row = {k: old.get(k, "") for k in fieldnames}
        usage = json.loads(old["usage"]) if old.get("usage") else None
        api_base_url = old.get("api_base_url") or API_BASE
        cost = endpoint_cost(old.get("model", ""), usage, api_base_url)
        cache_hit = old.get("status") == "cache_hit" or old.get("cache_hit") in (True, "True", "true", "1")
        if cache_hit:
            cost.update(usd_min=0.0, usd_max=0.0)
        _fill_usage_columns(row, usage)
        row.update(usd_estimate_min=cost["usd_min"], usd_estimate_max=cost["usd_max"],
                   pricing_date=cost.get("pricing_date"), cache_hit=cache_hit)
        if "api_base_url" in row:
            row.update(api_base_url=api_base_url, pricing_source="OpenAI published rates" if api_base_url == API_BASE else "relay price unknown")
        repaired.append(row)
    temp = path.with_suffix(".csv.tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(repaired)
    os.replace(temp, path)


def _fill_usage_columns(row, usage):
    usage = usage or {}
    detail = usage.get("input_tokens_details", {}) or {}
    row.update(input_tokens=usage.get("input_tokens", ""),
               input_text_tokens=detail.get("text_tokens", ""), input_image_tokens=detail.get("image_tokens", ""),
               output_tokens=usage.get("output_tokens", ""), total_tokens=usage.get("total_tokens", ""),
               cached_tokens=detail.get("cached_tokens", usage.get("cached_tokens", "")))


def resolve_api_key():
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text(encoding="utf-8").strip()
    return key


def validate_size(model, size, custom_width, custom_height):
    if model not in MODELS or size not in SIZES:
        raise ValueError("模型或尺寸无效，请从下拉框选择。")
    if size == "auto":
        return size
    if model in ("gpt-image-1", "gpt-image-1.5"):
        if size not in ("1024x1024", "1024x1536", "1536x1024"):
            raise ValueError("此旧模型仅支持 1024x1024 / 1024x1536 / 1536x1024。")
        return size
    w, h = (custom_width, custom_height) if size == "Custom" else map(int, size.split("x"))
    if min(w, h) <= 0 or w % 16 or h % 16:
        raise ValueError("宽高必须为正数且是 16 的倍数。")
    if max(w, h) > 3840 or max(w, h) / min(w, h) > 3:
        raise ValueError("最长边不得超过 3840，长宽比不得超过 3:1。")
    if not 655360 <= w * h <= 8294400:
        raise ValueError("总像素需在 655,360 至 8,294,400 之间。")
    return f"{w}x{h}"


def tensor_to_png_bytes(image, max_pixels=2048 * 2048):
    if image.dim() != 4 or image.shape[0] != 1 or image.shape[-1] != 3:
        raise ValueError("参考图需为单张 RGB 图片。")
    arr = (image[0].cpu().float().numpy().clip(0, 1) * 255).round().astype(np.uint8)
    pil = PILImage.fromarray(arr)
    if max_pixels and pil.width * pil.height > max_pixels:
        ratio = (max_pixels / (pil.width * pil.height)) ** 0.5
        pil = pil.resize((max(1, int(pil.width * ratio)), max(1, int(pil.height * ratio))), PILImage.Resampling.LANCZOS)
    buf = io.BytesIO()
    pil.save(buf, format="PNG")
    return buf.getvalue()


def mask_to_rgba_png_bytes(mask, target_size):
    m = mask.cpu().float().numpy()
    if m.ndim == 3 and m.shape[0] == 1:
        m = m[0]
    if m.ndim != 2:
        raise ValueError("遮罩需要单张 MASK。")
    rgba = np.zeros((*m.shape, 4), dtype=np.uint8)
    rgba[:, :, 3] = ((1.0 - m).clip(0, 1) * 255).round().astype(np.uint8)
    pil = PILImage.fromarray(rgba).resize(target_size, PILImage.Resampling.NEAREST)
    buf = io.BytesIO()
    pil.save(buf, format="PNG")
    return buf.getvalue()


def _decode_image(raw):
    with PILImage.open(io.BytesIO(raw)) as source:
        return torch.from_numpy(np.array(source.convert("RGB")).astype(np.float32) / 255.0).unsqueeze(0)


def _log_request(status, request, ref_count, usage=None, http_status=0, error="",
                 request_id="", request_hash="", elapsed_seconds=None, original_usage=None, api_base_url=API_BASE):
    cache_hit = status == "cache_hit"
    cost = endpoint_cost(request["model"], usage, api_base_url)
    if cache_hit:
        cost.update(status="local_cache", usd_min=0.0, usd_max=0.0, assumptions=[])
    row = dict(time=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), status=status,
               model=request["model"], quality=request["quality"], size=request["size"],
               n=request["n"], ref_images=ref_count, est_usd_min="", est_usd_max="",
               http_status=http_status, key_origin="BYOK", prompt_len=len(request["prompt"]),
               usage=json.dumps(usage, ensure_ascii=False) if usage else "", error=error)
    _fill_usage_columns(row, usage)
    row.update(cached_tokens=row["cached_tokens"], usd_estimate_min=cost["usd_min"],
               usd_estimate_max=cost["usd_max"], pricing_date=cost.get("pricing_date"),
               request_id=request_id, request_hash=request_hash,
               elapsed_seconds=elapsed_seconds, cache_hit=cache_hit, api_base_url=api_base_url,
               pricing_source="OpenAI published rates" if api_base_url == API_BASE else "relay price unknown")
    event = dict(schema=1, time=row["time"], status=status, model=request["model"], quality=request["quality"],
                 size=request["size"], n=request["n"], ref_images=ref_count, request_id=request_id,
                 request_hash=request_hash, elapsed_seconds=elapsed_seconds, cache_hit=cache_hit,
                 http_status=http_status, usage=usage, original_usage=original_usage, cost=cost, error=error,
                 api_base_url=api_base_url)
    try:
        COST_LOG.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(COST_LOG) + ".lock", timeout=10):
            with COST_LOG.with_name("fabric_usage_log.jsonl").open("a", encoding="utf-8") as out:
                out.write(json.dumps(event, ensure_ascii=False) + "\n")
            _repair_csv_header(COST_LOG, list(row))
            new = not COST_LOG.exists() or not COST_LOG.stat().st_size
            with COST_LOG.open("a", newline="", encoding="utf-8-sig") as out:
                writer = csv.DictWriter(out, fieldnames=list(row))
                if new:
                    writer.writeheader()
                writer.writerow(row)
    except (OSError, Timeout, ValueError) as exc:
        print(f"[fabric-cost] 日志写入失败: {type(exc).__name__}")


def _save_cache(path, images, metadata):
    metadata = {**metadata, "output_sha256": [hashlib.sha256(x).hexdigest() for x in images]}
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("manifest.json", json.dumps(metadata, ensure_ascii=False, indent=2))
            for i, raw in enumerate(images):
                archive.writestr(f"{i}.png", raw)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _load_cache(path, expected_hash, n):
    try:
        with zipfile.ZipFile(path) as archive:
            metadata = json.loads(archive.read("manifest.json"))
            if metadata["request_hash"] != expected_hash or len(metadata["output_sha256"]) != n:
                raise ValueError("缓存记录不匹配")
            images = [archive.read(f"{i}.png") for i in range(n)]
            if [hashlib.sha256(x).hexdigest() for x in images] != metadata["output_sha256"]:
                raise ValueError("缓存图片校验失败")
            for raw in images:
                _decode_image(raw)
            return images, metadata
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        raise RuntimeError(f"本地缓存损坏，未调用 API。请将 cache_mode 改为 refresh 后手动重试。文件: {path.name}") from exc


class FabricGPTImage2:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "prompt": ("STRING", {"multiline": True, "default": ""}),
            "model": (MODELS, {"default": "gpt-image-2"}),
            "quality": (["low", "medium", "high", "xhigh", "max", "auto"], {"default": "low"}),
            "size": (SIZES, {"default": "auto"}),
            "custom_width": ("INT", {"default": 1024, "min": 16, "max": 3840, "step": 16}),
            "custom_height": ("INT", {"default": 1024, "min": 16, "max": 3840, "step": 16}),
            "background": (["auto", "opaque", "transparent"], {"default": "auto"}),
            "n": ("INT", {"default": 1, "min": 1, "max": 8}),
            "cost_preview": ("STRING", {"default": "待实际 usage；不使用未经验证的总价估算"}),
            "token_estimate": ("STRING", {"default": "待运行：显示实际上传尺寸", "multiline": True}),
            "last_tokens": ("STRING", {"default": "(尚未运行)", "multiline": True}),
            "last_cost": ("STRING", {"default": "(尚未运行)", "multiline": True}),
            "openai_api_key": ("STRING", {"default": ""}),
        }, "optional": {
            "ref_images": ("IMAGE",), "mask": ("MASK",), "original_refs": ("FABRIC_REFS",),
            "cache_mode": (CACHE_MODES, {"default": "off", "tooltip": "reuse=复用成功结果；refresh=重新生成并覆盖缓存；off=每次重新生成"}),
            "variation": ("INT", {"default": 0, "min": 0, "max": 2147483647, "tooltip": "变体编号仅管理缓存版本，不是模型随机种子"}),
            "reference_max_pixels": ([4194304, 1048576, 0], {"default": 4194304, "tooltip": "每张参考图像素上限；0=原图；不放大局部裁切。"}),
            "api_base_url": ("STRING", {"default": API_BASE, "tooltip": "OpenAI 兼容接口根地址，例如 https://api.openai.com/v1 或中转站提供的 /v1 地址。"}),
            "custom_model": ("STRING", {"default": "", "tooltip": "中转站的模型标识；非空时覆盖上面的 model。留空使用下拉模型。"}),
            "api_key_file": ("STRING", {"default": "", "tooltip": "推荐：密钥文件路径，密钥不进入工作流。留空时官方接口沿用 api_key.txt；中转站需单独指定。"}),
        }}

    RETURN_TYPES = ("IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("image", "cost_report", "debug_json")
    FUNCTION = "run"
    CATEGORY = "fabric"

    def run(self, prompt, model, quality, size, custom_width, custom_height, background, n,
            cost_preview=None, token_estimate=None, last_tokens=None, last_cost=None,
            openai_api_key=None, ref_images=None, mask=None, original_refs=None,
            cache_mode="off", variation=0, reference_max_pixels=4194304,
            api_base_url=API_BASE, custom_model="", api_key_file=""):
        api_base_url = normalize_api_base(api_base_url)
        actual_model = custom_model.strip() or model
        if len(actual_model) > 200 or any(ch.isspace() for ch in actual_model):
            raise ValueError("模型标识不能为空白或含空白字符。")
        final_size = validate_size(model, size, custom_width, custom_height)
        if not prompt.strip():
            raise ValueError("提示词为空，未调用 API。")
        if quality not in ("low", "medium", "high", "xhigh", "max", "auto"):
            raise ValueError("质量设置无效。")
        if quality in ("xhigh", "max") and not model.startswith("gpt-image-2.5-"):
            raise ValueError("xhigh / max 仅适用于 Image 2.5。")
        if background not in ("auto", "opaque", "transparent"):
            raise ValueError("背景设置无效。")
        if model == "gpt-image-2" and background == "transparent":
            raise ValueError("GPT Image 2 不支持透明背景。")
        if not 1 <= n <= 8 or cache_mode not in CACHE_MODES or variation < 0:
            raise ValueError("张数、缓存模式或变体编号无效。")
        if reference_max_pixels not in (4194304, 1048576, 0):
            raise ValueError("参考图像素上限无效。")
        captions = []
        if original_refs is not None:
            images, captions = original_refs["images"], original_refs["captions"]
            if not images or len(images) != len(captions):
                raise ValueError("原图与说明数量不匹配。")
        elif ref_images is not None:
            if ref_images.dim() != 4 or not ref_images.shape[0]:
                raise ValueError("参考图 batch 无效。")
            images = [ref_images[i:i+1] for i in range(ref_images.shape[0])]
        else:
            images = []
        if len(images) > 16:
            raise ValueError("最多支持 16 张参考图。")
        payloads = [tensor_to_png_bytes(im, reference_max_pixels) for im in images]
        dimensions = []
        for raw in payloads:
            if len(raw) >= 50 * 1024 * 1024:
                raise ValueError("单张参考 PNG 超过 50MB，请降低像素上限或裁切。")
            with PILImage.open(io.BytesIO(raw)) as pil:
                dimensions.append(list(pil.size))
        mask_bytes = None
        if mask is not None:
            if len(images) != 1:
                raise ValueError("当前节点的遮罩模式需要且仅支持一张参考图。")
            if tuple(mask.shape[-2:]) != tuple(images[0].shape[1:3]):
                raise ValueError("遮罩尺寸必须与原始参考图一致。")
            mask_bytes = mask_to_rgba_png_bytes(mask, tuple(dimensions[0]))
        effective_prompt = prompt.strip()
        if captions and not all(c in effective_prompt for c in captions):
            effective_prompt = "\n\n".join(captions) + "\n\n" + effective_prompt
        request = dict(model=actual_model, prompt=effective_prompt, quality=quality,
                       background=background, n=n, size=final_size, output_format="png")
        manifest = dict(schema=1, request=request, variation=variation,
                        reference_max_pixels=reference_max_pixels,
                        input_sha256=[hashlib.sha256(p).hexdigest() for p in payloads],
                        uploaded_sizes=dimensions,
                        mask_sha256=hashlib.sha256(mask_bytes).hexdigest() if mask_bytes else None)
        # Keep existing official cache keys; isolate every relay endpoint.
        if api_base_url != API_BASE:
            manifest["api_base_url"] = api_base_url
        request_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        cache_path = CACHE_DIR / (request_hash + ".zip")
        if cache_mode != "off":
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with FileLock(str(cache_path) + ".lock", timeout=600):
                if cache_mode == "reuse" and cache_path.exists():
                    results, metadata = _load_cache(cache_path, request_hash, n)
                    _log_request("cache_hit", request, len(payloads), usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                                 request_hash=request_hash, original_usage=metadata.get("usage"), api_base_url=api_base_url)
                    return self._result(results, metadata, True, cache_path)
                results, metadata = self._request(request, payloads, mask_bytes, openai_api_key, manifest, request_hash, api_key_file)
                try:
                    _save_cache(cache_path, results, metadata)
                except OSError as exc:
                    metadata["cache_error"] = f"缓存未保存: {type(exc).__name__}；再次运行可能收费。"
                return self._result(results, metadata, False, cache_path)
        results, metadata = self._request(request, payloads, mask_bytes, openai_api_key, manifest, request_hash, api_key_file)
        return self._result(results, metadata, False, None)

    def _request(self, request, payloads, mask_bytes, openai_api_key, manifest, request_hash, api_key_file=""):
        api_base_url = manifest.get("api_base_url", API_BASE)
        key = request_key(openai_api_key, api_key_file, api_base_url)
        if not key:
            raise ValueError("未找到 OpenAI Key，请在 api_key.txt 或环境变量中配置。")
        headers = {"Authorization": f"Bearer {key}"}
        endpoint = "images/edits" if payloads else "images/generations"
        started = time.perf_counter()
        try:
            if payloads:
                field = "image" if len(payloads) == 1 else "image[]"
                files = [(field, (f"reference_{i+1:02d}.png", raw, "image/png")) for i, raw in enumerate(payloads)]
                if mask_bytes:
                    files.append(("mask", ("mask.png", mask_bytes, "image/png")))
                resp = requests.post(f"{api_base_url}/{endpoint}", headers=headers, data=request, files=files, timeout=(20, 300), allow_redirects=False)
            else:
                resp = requests.post(f"{api_base_url}/{endpoint}", headers=headers, json=request, timeout=(20, 300), allow_redirects=False)
        except requests.RequestException as exc:
            _log_request("error", request, len(payloads), error=type(exc).__name__,
                         request_hash=request_hash, elapsed_seconds=round(time.perf_counter()-started, 3), api_base_url=api_base_url)
            raise RuntimeError("请求中断，未自动重试。服务端可能已生成，请核对用量后再手动重试。") from None
        request_id = resp.headers.get("x-request-id", "")
        elapsed_seconds = round(time.perf_counter()-started, 3)
        if resp.status_code != 200:
            try:
                error = resp.json().get("error", {})
                message = str(error.get("message", "请求失败")).replace(key, "[redacted]")[:400]
            except (ValueError, AttributeError):
                message = "响应不是标准错误格式"
            _log_request("error", request, len(payloads), http_status=resp.status_code, error=message,
                         request_id=request_id, request_hash=request_hash, elapsed_seconds=elapsed_seconds, api_base_url=api_base_url)
            raise RuntimeError(f"图像 API {resp.status_code}: {message}；request_id={request_id}。未自动切换接口、模型或重试。")
        usage = None
        try:
            data = resp.json()
            usage = data.get("usage") or {}
            results = []
            for item in data.get("data", []):
                if item.get("b64_json"):
                    encoded = item["b64_json"]
                    if encoded.startswith("data:image/"):
                        encoded = encoded.split(",", 1)[1]
                    raw = base64.b64decode(encoded, validate=True)
                elif item.get("url"):
                    raw = download_image(item["url"])
                else:
                    raise ValueError("响应没有 b64_json 或图片 URL")
                _decode_image(raw)
                results.append(raw)
            if len(results) != request["n"]:
                raise ValueError("返回图片数量与请求不一致")
        except (ValueError, TypeError, KeyError, AttributeError, OSError, requests.RequestException, RuntimeError) as exc:
            message = f"返回图片读取失败 ({type(exc).__name__})；服务端可能已经计费，未重新生图。"
            _log_request("error", request, len(payloads), usage=usage, http_status=200, error=message,
                         request_id=request_id, request_hash=request_hash, elapsed_seconds=elapsed_seconds, api_base_url=api_base_url)
            raise RuntimeError(message) from None
        _log_request("ok", request, len(payloads), usage=usage, http_status=200,
                     request_id=request_id, request_hash=request_hash, elapsed_seconds=elapsed_seconds, api_base_url=api_base_url)
        return results, dict(manifest=manifest, request_hash=request_hash, usage=usage,
                             request_id=request_id, endpoint=endpoint, elapsed_seconds=elapsed_seconds,
                             created_at=datetime.datetime.now().isoformat(timespec="seconds"))

    def _result(self, results, metadata, cache_hit, cache_path):
        tensors = [_decode_image(raw) for raw in results]
        if len({tuple(x.shape) for x in tensors}) != 1:
            raise RuntimeError("返回图片尺寸不同，请使用固定 size 且 n=1；已生成结果可在缓存中找回。")
        usage = metadata.get("usage", {})
        tokens = json.dumps(usage, ensure_ascii=False) if usage else "API 未返回 usage"
        request = metadata["manifest"]["request"]
        api_base_url = metadata["manifest"].get("api_base_url", API_BASE)
        estimated = endpoint_cost(request["model"], usage, api_base_url)
        cost = ("本次本地缓存，新增 API 费用 $0；首次生成 " if cache_hit else "本次 ") + _cost_text(estimated)
        if api_base_url == API_BASE:
            cost += "；以 OpenAI 账单为准。"
        sizes = metadata["manifest"]["uploaded_sizes"]
        upload_info = "上传参考尺寸: " + (", ".join(f"{w}x{h}" for w, h in sizes) or "无参考图")
        if metadata.get("cache_error"):
            cost += " " + metadata["cache_error"]
        report = f"{request['model']} | {request['quality']} | {len(results)} 张 | {cost} | " + ("首次生成 usage: " if cache_hit else "本次 usage: ") + tokens
        debug = {**metadata, "cost_estimate": estimated, "cache_hit": cache_hit, "cache_path": str(cache_path) if cache_path else None}
        status = {"cost_preview": cost, "token_estimate": upload_info,
                  "last_tokens": ("首次生成: " if cache_hit else "本次: ") + tokens,
                  "last_cost": request["model"] + "\n" + cost}
        return {"ui": {"fabric_status": [status]},
                "result": (torch.cat(tensors), report, json.dumps(debug, ensure_ascii=False, indent=2))}

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # 每次检查磁盘缓存；是否请求 API 由显式 cache_mode 决定。
        return float("nan")


NODE_CLASS_MAPPINGS = {"FabricGPTImage2": FabricGPTImage2}
NODE_DISPLAY_NAME_MAPPINGS = {"FabricGPTImage2": "面料生图 · API Key / 自定义接口"}
