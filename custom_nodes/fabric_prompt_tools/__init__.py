# Fabric Prompt Tools：参考图、独立说明、场景与 BYOK 生图。
# FabricPromptAssembler 仅为旧工作流兼容保留。

from .nodes import NODE_CLASS_MAPPINGS as _A, NODE_DISPLAY_NAME_MAPPINGS as _AD
from .fabric_gpt import NODE_CLASS_MAPPINGS as _B, NODE_DISPLAY_NAME_MAPPINGS as _BD

NODE_CLASS_MAPPINGS = {**_A, **_B}
NODE_DISPLAY_NAME_MAPPINGS = {**_AD, **_BD}

WEB_DIRECTORY = "./web"
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
