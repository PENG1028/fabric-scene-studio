"""Semantic transparency controls and calibrated chart crops; no image API calls."""
from pathlib import Path
from PIL import Image, ImageOps

LEVELS = {
    1: ('非常透明', '单层覆盖下身体轮廓清晰，纱线仍可见并产生局部遮挡。'),
    2: ('透明', '单层下身体轮廓可见，织物自身颜色和遮挡比1档更明显。'),
    3: ('微透明', '对应原示意图第三列：身体轮廓模糊可见；不把名称当作透光率。'),
    4: ('半透明', '对应原示意图第四列：身体轮廓隐约可见，贴肤可透出较弱的肤色。'),
    5: ('几乎不透明', '单层下基本看不见身体轮廓，但保持实拍的真实组织与表面。'),
}


def instruction(level):
    if type(level) is not int or level not in LEVELS:
        raise ValueError('Transparency level must be an integer from 1 to 5')
    name, description = LEVELS[level]
    return (f'用户指定单层透明度视觉目标：{level}档（{name}）。{description}'
            '这不是实测透光率或毫米厚度；实拍材料特性优先，冲突时不通过改写纱线、孔隙或颜色来强行匹配示意。'
            '按皮肤、纱线遮挡、背景和光线形成自然综合色，不使用均匀透明蒙版。'
            '贴肤处可受肤色影响，悬空处呈现纱线本色与透底；叠层改变遮挡，颜色变化依实拍和光线判断，不规定一律变深。'
            '胸部和臀胯由独立不透内搭遮蔽，手臂、肩侧或悬空衣摆展示单层关系。'
            '双层、三层为局部结构推演，不代表已经拍摄或测量；没有依据不指定厚度数值。')


def crop_box(size, level, region):
    instruction(level)
    if len(region) != 4 or not all(0 <= x <= 1 for x in region):
        raise ValueError('Chart region needs four normalized coordinates')
    left, top, right, bottom = region
    if not left < right or not top < bottom:
        raise ValueError('Invalid chart region')
    width, height = size
    column = (right - left) / 5
    # Small horizontal inset removes separators; calibrated crop excludes title and feet.
    box = (round(width*(left+(level-1)*column+column*.04)), round(height*top),
           round(width*(left+level*column-column*.04)), round(height*bottom))
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError('Chart too small')
    return box


def load_crop(chart, level, region):
    with Image.open(chart) as source:
        original = ImageOps.exif_transpose(source).convert('RGB')
    box = crop_box(original.size, level, region)
    return original.crop(box), box


def render_controls(controls, base):
    level = controls.get('transparency_level', 4)
    text = instruction(level)
    mode = controls.get('reference_mode', 'text')
    if mode not in ('text', 'image'):
        raise ValueError('reference_mode must be text or image')
    if mode == 'text':
        return text, None, None
    chart = (Path(base)/controls['chart_file']).resolve()
    crop, box = load_crop(chart, level, controls['chart_region'])
    info = dict(file=str(chart), role='transparency', crop_box=list(box), level=level,
                caption='仅透明度示意，不提供颜色、网眼、纱线、光泽、软硬、款式、人物或内衣依据。'+text)
    return text, crop, info
