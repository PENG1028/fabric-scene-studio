"""Single shared material-control node; never contacts an image API."""
import hashlib
from pathlib import Path
import numpy as np
import torch
from PIL import Image
from .transparency import render_controls


class FabricTransparencyControls:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {
            'original_refs': ('FABRIC_REFS',),
            'prompt': ('STRING', {'forceInput': True}),
            'transparency_level': ([1,2,3,4,5], {'default':4,'tooltip':'按示意图从最透到最不透排列；4=图中半透明，不是透光率'}),
            'reference_mode': (['text','image'], {'default':'text','tooltip':'text只发送档位文字；image附加所选一列示意图'}),
            'chart_path': ('STRING', {'default':''}),
            'chart_region': ('STRING', {'default':'0,0.235,1,0.845','tooltip':'本次五列对照图已标定区域；换图需重新标定左上右下归一化坐标'}),
        }}

    RETURN_TYPES = ('FABRIC_REFS','STRING','IMAGE')
    RETURN_NAMES = ('original_refs','prompt','transparency_preview')
    FUNCTION = 'apply'
    CATEGORY = 'fabric'

    def apply(self,original_refs,prompt,transparency_level,reference_mode,chart_path,chart_region):
        region=[float(x.strip()) for x in chart_region.split(',')]
        settings=dict(transparency_level=transparency_level,reference_mode=reference_mode,
                      chart_file=chart_path,chart_region=region)
        if reference_mode=='image' and not chart_path.strip():
            raise ValueError('请选择透明度对照图路径，或改为text模式')
        if reference_mode=='image' and '所有附图均为同一编号真实面料' in prompt:
            raise ValueError('先将提示词中“所有附图都是面料”的旧规则改为逐图分工')
        text,crop,info=render_controls(settings,Path.cwd())
        refs={'images':list(original_refs['images']),'captions':list(original_refs['captions'])}
        result=prompt.strip()+'\n\n'+text
        if crop is not None:
            tensor=torch.from_numpy(np.asarray(crop).copy().astype(np.float32)/255).unsqueeze(0)
            refs['images'].append(tensor)
            caption=f"[参考图 {len(refs['images'])}] "+info['caption']
            refs['captions'].append(caption)
            result+='\n\n'+caption
        else:
            tensor=torch.zeros((1,1,1,3),dtype=torch.float32)
        return refs,result,tensor

    @classmethod
    def IS_CHANGED(cls,reference_mode,chart_path,**kwargs):
        if reference_mode=='image':
            return hashlib.sha256(Path(chart_path).read_bytes()).hexdigest() if Path(chart_path).is_file() else 'missing'
        return 'text'


NODE_CLASS_MAPPINGS={'FabricTransparencyControls':FabricTransparencyControls}
NODE_DISPLAY_NAME_MAPPINGS={'FabricTransparencyControls':'面料 · 透明度档位（示意辅助）'}
