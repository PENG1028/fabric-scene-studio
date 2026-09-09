# Generated pure prompt/file helpers; no ComfyUI or torch dependency.
import re
from pathlib import Path
SCENES_DIR=Path(__file__).parent/"scenes"

def _list_scene_presets():
    if not SCENES_DIR.is_dir():
        return ['Custom']
    names = sorted([p.stem for p in SCENES_DIR.glob('*.md')])
    return names if names else ['Custom']

def _parse_yaml_frontmatter(text):
    """极简 YAML frontmatter 解析: 支持 'key: "value"' 单行."""
    if not text.startswith('---'):
        return {}
    end = text.find('\n---', 3)
    if end < 0:
        return {}
    block = text[3:end].strip()
    out = {}
    for line in block.splitlines():
        m = re.match('\\s*([A-Za-z_]+)\\s*:\\s*"?(.*?)"?\\s*$', line)
        if m:
            out[m.group(1).strip()] = m.group(2).strip()
    return out

def _clean_part(part):
    if not part:
        return ''
    lines = [re.sub('[ \\t]{2,}', ' ', ln.strip()) for ln in part.splitlines()]
    return '\n'.join((l for l in lines if l))

def _load_preset(name):
    if name not in _list_scene_presets():
        raise ValueError(f'场景预设不存在: {name}')
    path = SCENES_DIR / f'{name}.md'
    if not path.exists():
        return {'subject': '', 'scene': '', 'photography': '', 'size_hint': ''}
    text = path.read_text(encoding='utf-8')
    return _parse_yaml_frontmatter(text)

class FabricFaithfulness:

    def assemble(self, preserve, reference_hint, joiner, captions=None):
        parts = []
        if captions and captions.strip():
            parts.append(('面料描述', _clean_part(captions)))
        if preserve.strip():
            parts.append(('还原', _clean_part(preserve)))
        if reference_hint.strip():
            parts.append(('参考图说明', _clean_part(reference_hint)))
        text = joiner.join((t for _, t in parts)).strip()
        preview = f'A 组模块: {[k for k, _ in parts]}  |  字符: {len(text)}'
        return (text, len(text), preview)

class FabricSceneCaption:

    def describe(self, fabric, color, weave, surface, weight, extra_notes=''):
        parts = []
        if fabric.strip():
            parts.append(f'材质: {fabric.strip()}')
        if color.strip():
            parts.append(f'颜色: {color.strip()}')
        if weave.strip():
            parts.append(f'组织: {weave.strip()}')
        if surface.strip():
            parts.append(f'表面: {surface.strip()}')
        if weight.strip():
            parts.append(f'重量/垂坠: {weight.strip()}')
        if extra_notes.strip():
            parts.append(extra_notes.strip())
        if not parts:
            text = ''
        else:
            text = '; '.join(parts)
        fields = dict(fabric=fabric, color=color, weave=weave, surface=surface, weight=weight, extra=extra_notes)
        preview = f'已填字段: {[k for k, v in fields.items() if v.strip()]}  |  字符: {len(text)}'
        return (text, preview)

class FabricScenePreset:

    def assemble(self, scene_preset, joiner, faithfulness_prompt='', subject='', scene='', photography='', size_hint='', include_size_hint=True):
        preset_data = _load_preset(scene_preset)
        eff_subject = subject.strip() or preset_data.get('subject', '')
        eff_scene = scene.strip() or preset_data.get('scene', '')
        eff_photo = photography.strip() or preset_data.get('photography', '')
        eff_size = size_hint.strip() or preset_data.get('size_hint', '')
        parts = []
        if faithfulness_prompt and faithfulness_prompt.strip():
            parts.append(('A组', _clean_part(faithfulness_prompt)))
        if eff_subject:
            parts.append(('主体', _clean_part(eff_subject)))
        if eff_scene:
            parts.append(('场景', _clean_part(eff_scene)))
        if eff_photo:
            parts.append(('摄影', _clean_part(eff_photo)))
        if include_size_hint and eff_size:
            parts.append(('构图', _clean_part(eff_size)))
        text = joiner.join((t for _, t in parts)).strip()
        preview = f'场景: {scene_preset}  |  模块: {[k for k, _ in parts]}  |  字符: {len(text)}'
        return (text, len(text), preview)

class FabricMaterialFolder:

    @staticmethod
    def files(folder_path, flat_filename, extra_filenames='AUTO'):
        root = Path(folder_path.strip()).resolve()
        name = flat_filename.strip()
        if not root.is_dir() or not name or Path(name).name != name:
            raise ValueError('请填写有效的面料文件夹和完整平面图文件名。')
        flat = (root / name).resolve()
        if not flat.is_file() or flat.parent != root:
            raise ValueError('找不到完整平面图；不会自动用特写图代替。')
        if extra_filenames.strip().upper() == 'AUTO':
            pattern = re.compile(re.escape(flat.stem) + '-(\\d+)\\.(jpe?g|png|webp|tiff?)$', re.I)
            extras = sorted((p for p in root.iterdir() if p.is_file() and pattern.fullmatch(p.name)), key=lambda p: int(pattern.fullmatch(p.name).group(1)))
        else:
            names = [x.strip().replace('{stem}', flat.stem) for x in extra_filenames.splitlines() if x.strip()]
            if any((Path(x).name != x for x in names)):
                raise ValueError('补充图请只填写当前文件夹内的文件名，每行一张。')
            extras = [(root / x).resolve() for x in names]
        if len(extras) > 3:
            raise ValueError('发现超过3张补充图。请在 extra_filenames 中逐行指定本次使用的图片。')
        paths = [flat] + extras
        if len(set(paths)) != len(paths) or any((p.parent != root or not p.is_file() for p in paths)):
            raise ValueError('参考图片重复、缺失或不在指定文件夹内。')
        return paths
