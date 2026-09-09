"""Portable single-image recipe runner. `prepare` and `run --offline` never use HTTP."""
import argparse
import datetime
import hashlib
import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

from filelock import FileLock
from PIL import Image, ImageOps
import api_core as core
from prompt_core import FabricMaterialFolder, FabricSceneCaption, FabricFaithfulness, FabricScenePreset

HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def png_payload(path, max_pixels):
    with Image.open(path) as source:
        pil = ImageOps.exif_transpose(source).convert('RGB')
    if max_pixels and pil.width * pil.height > max_pixels:
        ratio = (max_pixels / (pil.width * pil.height)) ** 0.5
        pil = pil.resize((max(1, int(pil.width * ratio)), max(1, int(pil.height * ratio))), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    pil.save(output, format='PNG')
    raw = output.getvalue()
    if len(raw) >= 50 * 1024 * 1024:
        raise ValueError('Reference PNG exceeds 50 MB. Reduce reference_max_pixels.')
    return raw, list(pil.size)


def compile_recipe(recipe_file):
    path = Path(recipe_file).resolve()
    recipe = read(path)
    if recipe.get('schema') != 1:
        raise ValueError('Unsupported recipe schema')
    g = recipe['generation']
    forbidden = {'openai_api_key', 'api_key', 'api_key_file', 'authorization'}
    if forbidden.intersection(g):
        raise ValueError('Keep credentials outside the recipe. Use --key-file or a named environment variable.')
    model = g.get('model', 'gpt-image-2')
    quality = g.get('quality', 'medium')
    if quality not in ('low', 'medium', 'high', 'auto', 'xhigh', 'max') or (quality in ('xhigh', 'max') and not model.startswith('gpt-image-2.5-')):
        raise ValueError('Unsupported quality for this model family')
    background = g.get('background', 'auto')
    if background not in ('auto', 'opaque', 'transparent') or (model == 'gpt-image-2' and background == 'transparent'):
        raise ValueError('Unsupported background for this model')
    if g.get('n', 1) != 1:
        raise ValueError('This entry point generates one image per run; compare one direction at a time.')
    max_pixels = g.get('reference_max_pixels', 4194304)
    if max_pixels not in (0, 1048576, 4194304):
        raise ValueError('reference_max_pixels must be 0, 1048576 or 4194304')
    variation = g.get('variation', 0)
    if type(variation) is not int or variation < 0:
        raise ValueError('variation must be a nonnegative local cache version, not a model seed')
    api_base = core.normalize_api_base(g.get('api_base_url', core.API_BASE))
    actual_model = g.get('custom_model', '').strip() or model
    if not actual_model or len(actual_model) > 200 or any(ch.isspace() for ch in actual_model):
        raise ValueError('Invalid model identifier')
    material = recipe['material']
    folder = (path.parent / material['folder']).resolve()
    paths = FabricMaterialFolder.files(str(folder), material['flat_filename'], material.get('extra_filenames', ''))
    captions = material.get('captions', [])
    if len(captions) < len(paths):
        raise ValueError('Each selected material photo needs its own caption object')
    texts = []
    source_info = []
    for i, source in enumerate(paths, 1):
        caption = FabricSceneCaption().describe(**captions[i - 1])[0]
        role = '完整平面图：大面积颜色、纹理分布和重复比例的首要依据' if i == 1 else '补充实拍：结合完整平面图判断，不单独放大组织'
        texts.append(f'[参考图 {i}] {source.name} | {role}。' + caption)
        source_info.append(dict(file=str(source), role='flat' if i == 1 else 'material', source_sha256=hashlib.sha256(source.read_bytes()).hexdigest()))
    style = recipe.get('style')
    if style:
        if '所有附图均为同一编号真实面料' in recipe['rules'].get('reference_hint', ''):
            raise ValueError('Style photo conflicts with all-images-are-material rule. Assign reference roles explicitly.')
        style_path = (path.parent / style['file']).resolve()
        if not style.get('caption', '').strip():
            raise ValueError('Style image needs an explicit caption separating style from fabric')
        paths.append(style_path)
        texts.append(f'[参考图 {len(paths)}] {style_path.name} | ' + style['caption'])
        source_info.append(dict(file=str(style_path), role='style', source_sha256=hashlib.sha256(style_path.read_bytes()).hexdigest()))
    a = FabricFaithfulness().assemble(**recipe['rules'], captions='\n\n'.join(texts))[0]
    prompt = FabricScenePreset().assemble(**recipe['scene'], faithfulness_prompt=a)[0].strip()
    if not prompt:
        raise ValueError('Empty prompt')
    payloads, sizes = zip(*(png_payload(p, max_pixels) for p in paths))
    request = dict(model=actual_model, prompt=prompt, quality=quality, background=background, n=1,
                   size=core.validate_size(model, g.get('size', '1024x1536'), g.get('custom_width', 1024), g.get('custom_height', 1536)), output_format='png')
    manifest = dict(schema=1, request=request, variation=variation, reference_max_pixels=max_pixels,
                    input_sha256=[hashlib.sha256(p).hexdigest() for p in payloads], uploaded_sizes=list(sizes), mask_sha256=None)
    if api_base != core.API_BASE:
        manifest['api_base_url'] = api_base
    request_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return dict(recipe=recipe, request=request, manifest=manifest, request_hash=request_hash,
                payloads=list(payloads), sources=source_info, api_base_url=api_base)


def run(args):
    compiled = compile_recipe(args.recipe)
    request = compiled['request']
    request_hash = compiled['request_hash']
    output = Path(args.output_dir).resolve()
    cache = Path(args.cache_dir).resolve()
    cache_file = cache / (request_hash + '.zip')
    plan = dict(recipe=str(Path(args.recipe).resolve()), material_id=compiled['recipe']['material']['id'],
                sources=compiled['sources'], model=request['model'], quality=request['quality'], size=request['size'],
                n=1, request_hash=request_hash, manifest=compiled['manifest'], cache_available=cache_file.is_file(),
                estimated_usd=None, note='Preflight is free. Cost is calculated from actual returned usage; no guaranteed per-call dollar cap.')
    if args.action == 'prepare':
        target = output / 'plans' / request_hash
        write(target / 'plan.json', plan)
        (target / 'prompt.txt').write_text(request['prompt'], encoding='utf-8')
        print(json.dumps(dict(prepared=str(target), material_id=plan['material_id'], reference_files=[x['file'] for x in compiled['sources']],
                              quality=request['quality'], cache_available=plan['cache_available'], request_hash=request_hash, api_calls=0), ensure_ascii=False))
        return
    if not args.offline and not args.allow_paid:
        raise ValueError('Choose --offline for cache-only use, or --allow-paid when the user has authorized generation.')
    cache.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    core.COST_LOG = output / 'fabric_cost_log.csv'
    # A request journal survives ambiguous network failures and blocks accidental paid retries.
    journal = output / 'requests' / (request_hash + '.json')
    with FileLock(str(cache_file) + '.lock', timeout=10):
        hit = cache_file.exists()
        if hit:
            images, metadata = core._load_cache(cache_file, request_hash, 1)
            core._log_request('cache_hit', request, len(compiled['payloads']),
                usage=dict(input_tokens=0, output_tokens=0, total_tokens=0), request_hash=request_hash,
                original_usage=metadata.get('usage'), api_base_url=compiled['api_base_url'])
        else:
            if args.offline:
                raise ValueError('Cache miss. Offline mode does not send HTTP or read a key.')
            if journal.exists():
                raise ValueError('This request was already attempted. Inspect its journal, usage and provider status before retrying; no automatic retry.')
            # Resolve keys only after an authorized cache miss; never write their value to artifacts.
            if args.key_file:
                key = Path(args.key_file).expanduser().read_text(encoding='utf-8-sig').strip()
            elif args.key_env:
                key = os.environ.get(args.key_env, '').strip()
            elif compiled['api_base_url'] == core.API_BASE:
                key = os.environ.get('OPENAI_API_KEY', '').strip()
            else:
                raise ValueError('For a relay, explicitly select its --key-file or --key-env. Official credentials are not forwarded automatically.')
            if not key:
                raise ValueError('No key found in the chosen local file/environment variable')
            write(journal, dict(state='submitting', request_hash=request_hash, started_at=datetime.datetime.now().isoformat()))
            try:
                images, metadata = core.FabricGPTImage2()._request(request, compiled['payloads'], None, key,
                                                                compiled['manifest'], request_hash)
                core._save_cache(cache_file, images, metadata)
            except Exception as exc:
                write(journal, dict(state='needs_review', request_hash=request_hash, error_type=type(exc).__name__,
                                    note='No automatic retry. Check usage and provider request status.'))
                raise
            finally:
                key = None
            write(journal, dict(state='complete', request_hash=request_hash, request_id=metadata.get('request_id')))
        destination = output / request_hash
        destination.mkdir(exist_ok=True)
        image_file = destination / 'image.png'
        if image_file.exists() and image_file.read_bytes() != images[0]:
            raise ValueError('Existing result differs; choose another output directory instead of overwriting it.')
        image_file.write_bytes(images[0])
        (destination / 'prompt.txt').write_text(request['prompt'], encoding='utf-8')
        write(destination / 'plan.json', plan)
        original_cost = core.endpoint_cost(request['model'], metadata.get('usage'), compiled['api_base_url'])
        result = dict(image=str(image_file), request_hash=request_hash, request_id=metadata.get('request_id'),
                      cache_hit=hit, new_api_calls=0 if hit else 1,
                      current_usage=dict(input_tokens=0,output_tokens=0,total_tokens=0) if hit else metadata.get('usage'),
                      original_usage=metadata.get('usage'), original_cost_estimate=original_cost,
                      new_cost_estimate_usd=0 if hit else original_cost.get('usd_max'), material_approved=False)
        write(destination / 'result.json', result)
        print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run'])
    parser.add_argument('recipe')
    parser.add_argument('--output-dir', default=str(HERE / 'results'))
    parser.add_argument('--cache-dir', default=str(HERE / 'cache'))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--offline', action='store_true')
    mode.add_argument('--allow-paid', action='store_true')
    key = parser.add_mutually_exclusive_group()
    key.add_argument('--key-file')
    key.add_argument('--key-env')
    args = parser.parse_args()
    try:
        if args.action == 'prepare' or args.offline:
            with patch.object(core.requests.sessions.Session, 'request', side_effect=AssertionError('HTTP disabled in this mode')):
                run(args)
        else:
            run(args)
    except (ValueError, RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
