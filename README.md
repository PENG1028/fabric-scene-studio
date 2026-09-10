# Fabric Scene Studio / 面料场景工作室

用真实面料照片生成服装模特、窗帘和家居应用图。包含 ComfyUI 自定义节点、v7工作流、agent Skill 和独立 Images API 入口。

## 结构

- custom_nodes/fabric_prompt_tools：完整节点包、前端单张运行按钮、场景预设。
- workflows：v7模板，照片路径留空。
- portable：独立运行代码及可编辑示例配方，不需要ComfyUI或torch。
- skills/fabric-scene-studio：agent技能和运行代码。
- tools/install.py：配置路径、安装节点或Skill；不生成图片，不自动重启。

## ComfyUI

准备自己同一块布的完整平面图与补充实拍，示例名为1.JPG、1-3.JPG、1-2.JPG，可自行更改。逐图描述也必须按自己的布修改。

```sh
python tools/install.py install --comfy-root /path/to/ComfyUI --photos-dir /path/to/photos
```

使用ComfyUI所用Python安装节点requirements.txt中缺失的依赖，安全重启后导入local/fabric_lookbook_v7_filled.json。torch由ComfyUI环境提供。安装工具备份被替换的代码，保留现有api_key.txt。

## 独立API

需要Python 3.10+。

```sh
python -m pip install -r portable/requirements.txt
python portable/fabric_scene.py prepare portable/recipes/scene-3.json
python portable/fabric_scene.py run portable/recipes/scene-3.json --offline
# 准备好实际付费生图时：
python portable/fabric_scene.py run portable/recipes/scene-3.json --allow-paid --key-file /path/to/key.txt
```

先编辑配方material.folder、图片名称与各自描述。公开仓库不附照片、风格图或生成缓存。首次offline无缓存会停止。官方接口也可用OPENAI_API_KEY；兼容中转可设置api_base_url/custom_model，并显式指定该站密钥。只支持聊天接口的服务不能仅靠改地址工作。

## Skill

```sh
python tools/install.py install-skill
```

默认安装到~/.codex/skills，后续可调用 `$fabric-scene-studio`；其他agent也可以直接读取SKILL.md。

## 用量与限制

完整usage记录在fabric_usage_log.jsonl，拆分和估算在fabric_cost_log.csv；无usage不等于免费，中转价格未知时不套官方费用。默认单张运行。请求超时不自动重发。缓存复用已有结果，variation是本地缓存版本，不是seed。

low适合选款；提高质量也不能保证纱线、厚薄和色值精确复刻，需实物验收。配料与实际主布应明确区分。风格图只能提供人物/摄影等风格依据。示例提示词不是所有面料通用的事实。

模板与便携配方不是实时双向同步。维护共享逻辑时请同步便携快照和Skill代码。私人照片、密钥、缓存、日志与local目录不进入版本控制。

## 离线测试

```sh
python -m unittest discover -s tests
```

测试使用合成图片，不调用付费API。

## 透明度档位

新增共用透明度节点和所选档位预览，v7现为24节点。1–5档按五列示意图从最透到最不透，默认4档；text只传文字，image只附选中一列。真实面料不裁切、不模糊。参见[透明度与提示词说明](docs/transparency-controls.md)。示意图路径由使用者在本机设置，换图需重新标定裁切区域。
