# 不依赖 ComfyUI 的单张配方

本 Skill 的 `scripts/fabric_scene.py` 与便携包中的同名文件是同一运行入口。需要 Python 3.10+，Pillow、requests、filelock；依赖清单随脚本保存。公开版包含配方与代码，用户自行提供照片与缓存。

所有配方的相对文件夹/风格图路径均相对于配方 JSON，不依赖运行时当前目录。

```powershell
python fabric_scene.py prepare recipes/scene-3.json
python fabric_scene.py run recipes/scene-3.json --offline
```

`prepare` 输出最终 prompt.txt、图片角色/文件/哈希/实际上传尺寸及缓存状态。它不读 Key、不发送 HTTP、不预估未经证明的单次价格。`--offline` 只回放已存在且校验通过的缓存，找不到就停；不会暗中补生成。

用户已授权生图时：

```powershell
python fabric_scene.py run recipes/scene-3.json --allow-paid --key-file "本机密钥文件的路径"
```

也可 `--key-env FABRIC_RELAY_KEY` 指定环境变量名，或官方地址用已有 `OPENAI_API_KEY`。Key 值不放在参数、配方或聊天里。`generation.api_base_url` 可改为 Images API 兼容中转根地址，`custom_model` 可用其模型标识；中转必须显式选它的 key-file/key-env，金额显示未知。仅兼容聊天API的中转不能靠改地址工作。

输出位置通过 `--output-dir`，缓存通过 `--cache-dir` 指定。结果包含 image.png、prompt.txt、plan.json、result.json；目录根包含完整 fabric_usage_log.jsonl 和拆分 fabric_cost_log.csv。单次 `n=1`。缓存文件名兼容当前 ComfyUI 实现；输入、quality、模型或端点变化会改变缓存。

已有请求日记会阻止模糊失败后的付费重发。检查日志和服务商状态再决定是否手动重试，不能自动通过换目录/variation 绕开它。不提供精确账单金额或美元硬封顶；单张限制不能保证单次价格。

## 配方字段

- `schema: 1`、`name`：版本与名称。
- `material.id`、`folder`、`flat_filename`、`extra_filenames`：材料编号、照片目录、完整平面图、补充图逐行列表。支持 `{stem}`。AUTO只按同编号匹配；先实际检查图片用途。
- `material.captions`：按顺序的独立对象，每个包含 `fabric/color/weave/surface/weight/extra_notes`。前五项字符串可空，不虚构不确定事实。
- `rules`：`preserve/reference_hint/joiner`，对应 A组规则。
- `scene`：`scene_preset/joiner/subject/scene/photography/size_hint/include_size_hint`。覆盖字段留空时加载随包 scenes 预设。
- `generation`：`model/quality/size/custom_width/custom_height/background/n/variation/reference_max_pixels/api_base_url/custom_model`。不接受密钥字段。
- 可选 `style`：`file` 与 `caption`，追加为最后一张风格图。若旧 reference_hint 还说所有附图都是材料，则拒绝混用；改为清楚的图号分工。
- `review.material_approved`：保真未验收时保持 false；这只是记录，不会改变模型行为。

更换同批材料：修改 flat_filename 与 material.id，补充图名跟随 stem；换新组织还需重写实际图片描述。增加风格图会增加输入成本，且存在颜色/材质串用风险；先逐张验证。

scene-2/3/4 是三个服装示例；scene-1/5 是 v7 的长马甲/窗纱配方。`study-*` 文件是下一步对照方案，准备成功不代表已生成或效果更好。JSON 与 v7 画布不会自动双向同步；要在节点里继续使用便携改动，按对应字段回填并验证。
