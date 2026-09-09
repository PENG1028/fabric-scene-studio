# -*- coding: utf-8 -*-
"""
Fabric Prompt Tools - 节点集 v4 (职责分离 + 可独立 Mute)

设计原则:
  - 每个节点只做一件事 (单一职责)
  - 节点之间靠 STRING 连线传文本 (A 组→B 组, B 组→BYOK)
  - 任何一条链路都可以右键 → Mode → Mute 跳过, 不影响其它链路
  - 字段尽量少, 不强迫用户填一遍又一遍

节点:
  1) FabricFaithfulness (A 组)   输入侧: 把"面料描述/还原要求/参考图说明"拼成一段忠实度 prompt
  2) FabricScenePreset  (B 组)   输出侧: 选场景预设, 把"主体/场景/摄影/尺寸"拼成一段场景 prompt
  3) FabricPromptAssembler (v3 兼容) 一站式版本, 保留以免老模板失效
"""

import re
import hashlib
from pathlib import Path

import numpy as np
import torch
from PIL import Image as PILImage, ImageOps

SCENES_DIR = Path(__file__).parent / "scenes"


# ----------------------------- 工具 -----------------------------

def _list_scene_presets():
    if not SCENES_DIR.is_dir():
        return ["Custom"]
    names = sorted([p.stem for p in SCENES_DIR.glob("*.md")])
    return names if names else ["Custom"]


def _parse_yaml_frontmatter(text):
    """极简 YAML frontmatter 解析: 支持 'key: "value"' 单行."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    block = text[3:end].strip()
    out = {}
    for line in block.splitlines():
        m = re.match(r'\s*([A-Za-z_]+)\s*:\s*"?(.*?)"?\s*$', line)
        if m:
            out[m.group(1).strip()] = m.group(2).strip()
    return out


def _clean_part(part):
    if not part:
        return ""
    lines = [re.sub(r"[ \t]{2,}", " ", ln.strip()) for ln in part.splitlines()]
    return "\n".join(l for l in lines if l)


def _load_preset(name):
    if name not in _list_scene_presets():
        raise ValueError(f"场景预设不存在: {name}")
    path = SCENES_DIR / f"{name}.md"
    if not path.exists():
        return {"subject": "", "scene": "", "photography": "", "size_hint": ""}
    text = path.read_text(encoding="utf-8")
    return _parse_yaml_frontmatter(text)


# =====================================================================
# 节点 1: FabricFaithfulness (A 组 - 通用忠实度规则)
# 输入: 通用规则模板 + RefMerger 汇总的图片说明
# 输出: A 组 prompt 字符串
# 设计原则:
#   - Faithfulness 不再有"具体面料描述"字段 (搬到 FabricSceneCaption)
#   - 只装"通用规则": 不要做什么 / 不要复刻什么 / 模板拼接方式
#   - 多份说明先在 RefMerger 按图片顺序合并，再接入 captions
# =====================================================================

class FabricFaithfulness:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                # 通用规则模板 (面料描述的具体内容改到 SceneCaption, 这里只装"不要做什么")
                "preserve": ("STRING", {
                    "multiline": True,
                    "default": "保持面料真实颜色与纹理; 不要新增花纹; 不要把哑光变油亮; 不要把细平纹变成提花; 不要模糊或虚化面料"
                }),
                "reference_hint": ("STRING", {
                    "multiline": True,
                    "default": "参考图只用于面料参考, 不要复刻图里的具体物品 / 人物 / 背景 / 构图"
                }),
                "joiner": ("STRING", {"default": ". "}),
            },
            "optional": {
                # 单个 STRING 输入；多张图的说明由 RefMerger 汇总。
                "captions": ("STRING", {"forceInput": True}),
            },
        }

    RETURN_TYPES = ("STRING", "INT", "STRING")
    RETURN_NAMES = ("prompt", "chars", "preview")
    FUNCTION = "assemble"
    CATEGORY = "fabric"

    def assemble(self, preserve, reference_hint, joiner, captions=None):
        parts = []

        # 1) 所有 caption (来自各 SceneCaption 节点) → "面料描述"段
        if captions and captions.strip():
            parts.append(("面料描述", _clean_part(captions)))

        # 2) 通用规则
        if preserve.strip():
            parts.append(("还原", _clean_part(preserve)))
        if reference_hint.strip():
            parts.append(("参考图说明", _clean_part(reference_hint)))

        text = joiner.join(t for _, t in parts).strip()
        preview = f"A 组模块: {[k for k, _ in parts]}  |  字符: {len(text)}"
        return (text, len(text), preview)


# =====================================================================
# 节点 2: FabricSceneCaption (单张参考图的具体描述)
# 输入: 5 个字段 (fabric/color/weave/surface/weight) + 可选 extra_notes
# 输出: 一段 caption 字符串 (给 FabricFaithfulness 用)
# 用途: 每张参考图拖一个这种节点; 想加就拖, 不想加就不拖
#       想用 LLM 自动识别: 在前面挂 FabricVisionAnalyst (后续)
# =====================================================================

class FabricSceneCaption:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "fabric": ("STRING", {"multiline": False,
                                       "default": "",
                                       "placeholder": "面料: 棉 / 麻 / 丝 / 羊毛 / 化纤 / 混纺"}),
                "color": ("STRING", {"multiline": False,
                                      "default": "",
                                      "placeholder": "颜色: 暖金黄色 / amber-gold / 蜂蜜黄"}),
                "weave": ("STRING", {"multiline": False,
                                      "default": "",
                                      "placeholder": "组织: 稀疏方平网眼 / 平纹 / 斜纹 / 缎纹"}),
                "surface": ("STRING", {"multiline": False,
                                        "default": "",
                                        "placeholder": "表面: 半哑光 弱丝光 褶皱自然 S 形"}),
                "weight": ("STRING", {"multiline": False,
                                       "default": "",
                                       "placeholder": "重量垂坠: 极轻 透明感 垂坠松软随形"}),
            },
            "optional": {
                "extra_notes": ("STRING", {"multiline": True,
                                            "default": "",
                                            "placeholder": "可选: 其他需要补充的细节 (花纹/瑕疵/印刷等)"}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("caption", "preview")
    FUNCTION = "describe"
    CATEGORY = "fabric"

    def describe(self, fabric, color, weave, surface, weight, extra_notes=""):
        parts = []
        # 5 个字段: 至少有一个填了才输出
        if fabric.strip():
            parts.append(f"材质: {fabric.strip()}")
        if color.strip():
            parts.append(f"颜色: {color.strip()}")
        if weave.strip():
            parts.append(f"组织: {weave.strip()}")
        if surface.strip():
            parts.append(f"表面: {surface.strip()}")
        if weight.strip():
            parts.append(f"重量/垂坠: {weight.strip()}")
        if extra_notes.strip():
            parts.append(extra_notes.strip())

        if not parts:
            text = ""
        else:
            text = "; ".join(parts)
        fields = dict(fabric=fabric, color=color, weave=weave, surface=surface,
                      weight=weight, extra=extra_notes)
        preview = f"已填字段: {[k for k, v in fields.items() if v.strip()]}  |  字符: {len(text)}"
        return (text, preview)


# =====================================================================
# 节点 2: FabricScenePreset (B 组 - 输出侧/场景)
# 输入: 可选 A 组字符串 (从 FabricFaithfulness 连过来)
#       scene_preset (下拉: Lookbook/Sofa/Curtain/Flatlay/Cushion/Custom)
#       可选: 4 个手动覆盖字段 (留空=用预设)
# 输出: 一段完整 prompt (A 组 + B 组) + 字符数 + 预览
# 用途: 一个场景一个节点, 想要哪个场景就把哪个节点 Mute 掉就行
# =====================================================================

class FabricScenePreset:
    @classmethod
    def IS_CHANGED(cls, scene_preset, **kwargs):
        return hashlib.sha256((SCENES_DIR / f"{scene_preset}.md").read_bytes()).hexdigest() if scene_preset in _list_scene_presets() else "missing-preset"

    @classmethod
    def INPUT_TYPES(cls):
        scenes = _list_scene_presets()
        return {
            "required": {
                "scene_preset": (scenes, {"default": "Lookbook"}),
                "joiner": ("STRING", {"default": ". "}),
            },
            "optional": {
                # 可选 A 组 (从 FabricFaithfulness 拉过来共用)
                "faithfulness_prompt": ("STRING", {"forceInput": True}),
                # 4 个覆盖字段 (留空=用预设值)
                "subject": ("STRING", {
                    "multiline": True,
                    "default": ""
                }),
                "scene": ("STRING", {
                    "multiline": True,
                    "default": ""
                }),
                "photography": ("STRING", {
                    "multiline": True,
                    "default": ""
                }),
                "size_hint": ("STRING", {
                    "multiline": True,
                    "default": ""
                }),
                # 开关
                "include_size_hint": ("BOOLEAN", {"default": True, "label_on": "含尺寸", "label_off": "不含尺寸"}),
            },
        }

    RETURN_TYPES = ("STRING", "INT", "STRING")
    RETURN_NAMES = ("prompt", "chars", "preview")
    FUNCTION = "assemble"
    CATEGORY = "fabric"

    def assemble(self, scene_preset, joiner, faithfulness_prompt="",
                 subject="", scene="", photography="", size_hint="",
                 include_size_hint=True):
        # 1) 加载预设 (用于填充用户没填的字段)
        preset_data = _load_preset(scene_preset)

        # 2) 用户填了 → 用户优先; 否则用预设; 否则空
        eff_subject = subject.strip() or preset_data.get("subject", "")
        eff_scene = scene.strip() or preset_data.get("scene", "")
        eff_photo = photography.strip() or preset_data.get("photography", "")
        eff_size = size_hint.strip() or preset_data.get("size_hint", "")

        # 3) 拼接: A 组 (如有) + B 组
        parts = []
        if faithfulness_prompt and faithfulness_prompt.strip():
            parts.append(("A组", _clean_part(faithfulness_prompt)))
        if eff_subject:
            parts.append(("主体", _clean_part(eff_subject)))
        if eff_scene:
            parts.append(("场景", _clean_part(eff_scene)))
        if eff_photo:
            parts.append(("摄影", _clean_part(eff_photo)))
        if include_size_hint and eff_size:
            parts.append(("构图", _clean_part(eff_size)))

        text = joiner.join(t for _, t in parts).strip()
        preview = f"场景: {scene_preset}  |  模块: {[k for k,_ in parts]}  |  字符: {len(text)}"
        return (text, len(text), preview)


# =====================================================================
# 节点 3: FabricPromptAssembler (v3 兼容 - 老模板)
# =====================================================================

class FabricPromptAssembler:
    @classmethod
    def INPUT_TYPES(cls):
        scenes = _list_scene_presets()
        return {
            "required": {
                "scene_preset": (scenes, {"default": "Lookbook"}),
                "fabric": ("STRING", {"multiline": True,
                    "default": "面料描述: 暖米黄色细平纹棉, 哑光, 薄而微透, 柔软垂坠"}),
                "preserve": ("STRING", {"multiline": True,
                    "default": "保持面料真实颜色与纹理; 不要新增花纹; 不要把哑光变油亮; 不要把细平纹变成提花"}),
                "reference_hint": ("STRING", {"multiline": True,
                    "default": "参考图只用于面料参考, 不要复刻图里的具体物品 / 人物 / 背景"}),
                "subject": ("STRING", {"multiline": True,
                    "default": "年轻女性, 25-30 岁, 穿着本块面料制成的宽松夏季衬衫"}),
                "scene": ("STRING", {"multiline": True,
                    "default": "摄影棚纯色背景(米色或浅灰)"}),
                "photography": ("STRING", {"multiline": True,
                    "default": "自然光, 全身 + 半身 + 面料特写三联图, 写实, 柔和阴影"}),
                "size_hint": ("STRING", {"multiline": True,
                    "default": "横构图 1536x1024, 三分镜 Lookbook 拼贴"}),
                "use_extra": ("BOOLEAN", {"default": False, "label_on": "包含附加", "label_off": "不含附加"}),
                "include_size_hint": ("BOOLEAN", {"default": True, "label_on": "包含尺寸提示", "label_off": "不含尺寸提示"}),
                "joiner": ("STRING", {"default": ". "}),
            },
            "optional": {
                "extra": ("STRING", {"multiline": True, "default": "附加要求: 写在这里的内容会拼到最末尾"}),
            },
        }

    RETURN_TYPES = ("STRING", "INT", "STRING")
    RETURN_NAMES = ("prompt", "chars", "preview")
    FUNCTION = "assemble"
    CATEGORY = "fabric"

    def assemble(self, scene_preset, fabric, preserve, reference_hint,
                 subject, scene, photography, size_hint,
                 use_extra, include_size_hint, joiner, extra=""):
        parts = []
        if fabric.strip():
            parts.append(("面料", _clean_part(fabric)))
        if preserve.strip():
            parts.append(("还原要求", _clean_part(preserve)))
        if reference_hint.strip():
            parts.append(("参考图说明", _clean_part(reference_hint)))
        if subject.strip():
            parts.append(("主体", _clean_part(subject)))
        if scene.strip():
            parts.append(("场景", _clean_part(scene)))
        if photography.strip():
            parts.append(("摄影", _clean_part(photography)))
        if include_size_hint and size_hint.strip():
            parts.append(("构图/尺寸", _clean_part(size_hint)))
        if use_extra and extra.strip():
            parts.append(("附加", _clean_part(extra)))

        text = joiner.join(t for _, t in parts).strip()
        preview = f"模块: {[k for k, _ in parts]}  |  字符数: {len(text)}  |  场景预设: {scene_preset}"
        return (text, len(text), preview)


# =====================================================================
# 节点 4: FabricRefImage (单张参考图节点)
# 输入: 1 张 IMAGE + 1 个 size_text (描述尺寸/语义)
# 输出: image (IMAGE) + caption (STRING: 自动生成的"参考图 N (尺寸, 构图, 语义) → ...")
# 用途: 每张参考图拖一个 FabricRefImage; 想加几张就拖几个
#       FabricRefMerger 节点会收集所有 RefImage 的 image 和 caption, 合并输出
# =====================================================================

class FabricRefImage:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "size_text": ("STRING", {"default": "", "multiline": False,
                                          "placeholder": "可选: 1504x2256 或 细/粗/特写/平铺"}),
                "label": ("STRING", {"default": "", "multiline": False,
                                      "placeholder": "可选标签: 例如 图1-细网眼特写"}),
            },
            "optional": {
                "image": ("IMAGE",),
                "image_path": ("STRING", {"default": "",
                                           "placeholder": "可选: 本地路径 (与 image 二选一)"}),
                "detail_caption": ("STRING", {"forceInput": True}),
            },
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("image", "caption")
    FUNCTION = "describe"
    CATEGORY = "fabric"

    def describe(self, image=None, size_text="", label="", image_path="", detail_caption=""):
        ten = image
        # 路径备用
        if ten is None and image_path and str(image_path).strip():
            with PILImage.open(str(image_path).strip()) as source:
                pil = ImageOps.exif_transpose(source).convert("RGB")
                arr = np.array(pil).astype(np.float32) / 255.0
                ten = torch.from_numpy(arr).unsqueeze(0)
        if ten is None:
            raise ValueError("参考图缺失: 请连接 LoadImage 或填写有效 image_path。")
        if ten.dim() != 4 or ten.shape[0] != 1 or ten.shape[-1] != 3:
            raise ValueError("每个单张参考图节点只接收一张 RGB 图片。")

        # 解析尺寸
        try:
            if ten.dim() == 4:
                h, w = int(ten.shape[1]), int(ten.shape[2])
            else:
                h, w = int(ten.shape[0]), int(ten.shape[1])
        except Exception:
            w, h = 0, 0

        sem = _parse_size_semantic(size_text or f"{w}x{h}")
        orient = "横构图" if w > h else ("竖构图" if h > w else "方形")
        size_final = f"{w}x{h}" + (f", 说明: {size_text.strip()}" if size_text.strip() else "")

        head = f"({size_final}, {orient}, {sem})"
        if label and label.strip():
            head = f"{label.strip()} {head}"
        caption = f"参考图 {head} → 用于面料颜色/纹理/光泽还原"
        if detail_caption.strip():
            caption += "\n" + detail_caption.strip()
        return (ten, caption)

    @classmethod
    def IS_CHANGED(cls, image=None, image_path="", **kwargs):
        if image is not None or not image_path.strip():
            return "connected-image"
        return hashlib.sha256(Path(image_path.strip()).read_bytes()).hexdigest()


# =====================================================================
# 节点 5: FabricRefMerger (合并任意数量的 FabricRefImage 输出)
# 输入: 多个 image (IMAGE) + 多个 caption (STRING) 输入槽
#       每对槽位只接一张图片及对应说明，按编号汇总
# 输出: reference_prompt + 兼容旧工作流的 batch + 保留原图的 original_refs
# 用途: 用户拖 N 个 FabricRefImage, 输出汇总后给 Faithfulness 和 BYOK
# 设计: 保留现有 8 对槽位，保持旧工作流连线兼容
# =====================================================================

class FabricRefMerger:
    @classmethod
    def INPUT_TYPES(cls):
        inputs = {
            "required": {
                "joiner": ("STRING", {"default": "; "}),
            },
            "optional": {},
        }
        # 8 对 image/caption 输入槽
        for i in range(1, 9):
            inputs["optional"][f"image_{i}"] = ("IMAGE",)
            inputs["optional"][f"caption_{i}"] = ("STRING", {"forceInput": True})
        return inputs

    RETURN_TYPES = ("STRING", "IMAGE", "FABRIC_REFS")
    RETURN_NAMES = ("reference_prompt", "ref_images", "original_refs")
    FUNCTION = "merge"
    CATEGORY = "fabric"

    def merge(self, joiner="; ", **kwargs):
        images = []
        captions = []
        for i in range(1, 9):
            img = kwargs.get(f"image_{i}")
            cap = kwargs.get(f"caption_{i}")
            if img is not None:
                if img.dim() != 4 or img.shape[0] != 1 or img.shape[-1] != 3:
                    raise ValueError(f"image_{i} 需要单张 RGB 图片。")
                images.append(img)
                captions.append(f"[参考图 {len(images)}] " + (str(cap).strip() if cap else "未填写描述，请以图为准"))
            elif cap and str(cap).strip():
                raise ValueError(f"caption_{i} 没有对应图片，请连接 image_{i}。")

        # 拼 reference_prompt
        if not images:
            raise ValueError("没有参考图，请至少连接一张真实面料照片。")
        prompt_text = joiner.join(captions)
        # 兼容旧 IMAGE batch: 等比缩小并留白。original_refs 保留逐张原图及顺序。
        thumbnails = []
        for img in images:
            pil = PILImage.fromarray((img[0].cpu().float().numpy().clip(0, 1) * 255).round().astype(np.uint8))
            pil.thumbnail((2048, 2048), PILImage.Resampling.LANCZOS)
            thumbnails.append(pil)
        width = max(p.width for p in thumbnails)
        height = max(p.height for p in thumbnails)
        padded = []
        for pil in thumbnails:
            canvas = PILImage.new("RGB", (width, height), "white")
            canvas.paste(pil, ((width - pil.width) // 2, (height - pil.height) // 2))
            padded.append(torch.from_numpy(np.asarray(canvas).astype(np.float32) / 255.0))
        batch = torch.stack(padded)
        return (prompt_text, batch, {"images": images, "captions": captions})


def _parse_size_semantic(text):
    """从尺寸/描述文字提取语义标签: 细 / 粗犷 / 特写 / 平铺 / 褶皱 / 中等"""
    t = (text or "").lower()
    if "细" in t or "fine" in t:
        return "细纹理"
    if "粗" in t or "coarse" in t or "粗犷" in t:
        return "粗犷纹理"
    if "特写" in t or "closeup" in t or "macro" in t:
        return "特写"
    if "平铺" in t or "flat" in t:
        return "平铺"
    if "褶皱" in t or "drape" in t or "draped" in t:
        return "褶皱垂坠"
    return "未指定观察用途；像素尺寸不代表纱线粗细"


class FabricMaterialFolder:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "folder_path": ("STRING", {"default": "", "tooltip": "同一批面料照片所在文件夹"}),
            "flat_filename": ("STRING", {"default": "1.JPG", "tooltip": "必填：完整平面图文件名。始终作为第一张参考图，不自动裁切。"}),
            "extra_filenames": ("STRING", {"default": "AUTO", "multiline": True,
                "tooltip": "AUTO 读取同编号补充图；也可每行指定文件名，最多3张。{stem} 代表主图编号，例如 {stem}-3.JPG 会随 1.JPG / 5.JPG 自动切换。留空仅用平面图。"}),
        }, "optional": {f"caption_{i}": ("STRING", {"forceInput": True}) for i in range(1, 5)}}

    RETURN_TYPES = ("STRING", "IMAGE", "FABRIC_REFS")
    RETURN_NAMES = ("reference_prompt", "preview_images", "original_refs")
    FUNCTION = "load"
    CATEGORY = "fabric"

    @staticmethod
    def files(folder_path, flat_filename, extra_filenames="AUTO"):
        root = Path(folder_path.strip()).resolve()
        name = flat_filename.strip()
        if not root.is_dir() or not name or Path(name).name != name:
            raise ValueError("请填写有效的面料文件夹和完整平面图文件名。")
        flat = (root / name).resolve()
        if not flat.is_file() or flat.parent != root:
            raise ValueError("找不到完整平面图；不会自动用特写图代替。")
        if extra_filenames.strip().upper() == "AUTO":
            pattern = re.compile(re.escape(flat.stem) + r"-(\d+)\.(jpe?g|png|webp|tiff?)$", re.I)
            extras = sorted((p for p in root.iterdir() if p.is_file() and pattern.fullmatch(p.name)),
                            key=lambda p: int(pattern.fullmatch(p.name).group(1)))
        else:
            names = [x.strip().replace("{stem}", flat.stem) for x in extra_filenames.splitlines() if x.strip()]
            if any(Path(x).name != x for x in names):
                raise ValueError("补充图请只填写当前文件夹内的文件名，每行一张。")
            extras = [(root / x).resolve() for x in names]
        if len(extras) > 3:
            raise ValueError("发现超过3张补充图。请在 extra_filenames 中逐行指定本次使用的图片。")
        paths = [flat] + extras
        if len(set(paths)) != len(paths) or any(p.parent != root or not p.is_file() for p in paths):
            raise ValueError("参考图片重复、缺失或不在指定文件夹内。")
        return paths

    def load(self, folder_path, flat_filename, extra_filenames="AUTO", **kwargs):
        paths = self.files(folder_path, flat_filename, extra_filenames)
        merged = {}
        for i, path in enumerate(paths, 1):
            with PILImage.open(path) as source:
                pil = ImageOps.exif_transpose(source).convert("RGB")
                tensor = torch.from_numpy(np.asarray(pil).astype(np.float32) / 255.0).unsqueeze(0)
            role = "完整平面图：大面积颜色、纹理分布和重复比例的首要依据" if i == 1 else "补充实拍：结合完整平面图判断，不单独放大组织"
            caption = f"{path.name} | {role}。" + kwargs.get(f"caption_{i}", "")
            merged[f"image_{i}"] = tensor
            merged[f"caption_{i}"] = caption
        return FabricRefMerger().merge(joiner="\n\n", **merged)

    @classmethod
    def IS_CHANGED(cls, folder_path, flat_filename, extra_filenames="AUTO", **kwargs):
        digest = hashlib.sha256()
        for path in cls.files(folder_path, flat_filename, extra_filenames):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()


# ----------------------------- 注册 -----------------------------

NODE_CLASS_MAPPINGS = {
    "FabricFaithfulness": FabricFaithfulness,
    "FabricSceneCaption": FabricSceneCaption,
    "FabricScenePreset": FabricScenePreset,
    "FabricRefImage": FabricRefImage,
    "FabricRefMerger": FabricRefMerger,
    "FabricMaterialFolder": FabricMaterialFolder,
    "FabricPromptAssembler": FabricPromptAssembler,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "FabricFaithfulness": "面料 · 忠实度 (A 组 · 通用规则)",
    "FabricSceneCaption": "面料 · 单图描述 (Caption)",
    "FabricScenePreset": "面料 · 场景预设 (B 组)",
    "FabricRefImage": "面料 · 单张参考图",
    "FabricRefMerger": "面料 · 参考图合并 (1-8)",
    "FabricMaterialFolder": "面料 · 文件夹输入（完整平面图优先）",
    "FabricPromptAssembler": "面料 Prompt 组装器 v3",
}
