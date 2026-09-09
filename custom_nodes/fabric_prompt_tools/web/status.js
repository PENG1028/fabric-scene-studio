import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

app.registerExtension({
  name: "fabric.byok.status",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name === "FabricMaterialFolder") {
      const created = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function () {
        created?.apply(this, arguments);
        for (const [name,label] of Object.entries({folder_path:"照片文件夹",flat_filename:"完整平面图文件名（必填）",extra_filenames:"补充实拍（AUTO 或每行一个文件名）"})) {
          const w=this.widgets.find(w=>w.name===name);if(w) w.label=label;
        }
        this.addWidget("button", "预览参考图（不生图）", null, async () => {
          const payload = await app.graphToPrompt();
          const selected = {};
          const visit = (id) => {
            if (selected[id]) return;
            const n = payload.output[id];
            if (!n || n.class_type === "FabricGPTImage2") throw new Error("预览分支不能包含生图节点");
            selected[id] = n;
            for (const value of Object.values(n.inputs)) {
              if (Array.isArray(value) && typeof value[0] === "string" && Number.isInteger(value[1])) visit(value[0]);
            }
          };
          const previews = Object.keys(payload.output).filter(id => payload.output[id].class_type === "PreviewImage" && payload.output[id].inputs.images?.[0] === String(this.id));
          if (!previews.length) { window.alert("请将 preview_images 连接到预览图像节点。"); return; }
          previews.forEach(visit);
          await api.queuePrompt(0, { ...payload, output: selected });
        }, { serialize: false });
      };
      return;
    }
    if (nodeData.name !== "FabricGPTImage2") return;
    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      created?.apply(this, arguments);
      for (const [name,label] of Object.entries({prompt:"最终提示词",model:"图像模型",quality:"生成质量",size:"输出尺寸",background:"背景",n:"生成张数",openai_api_key:"API Key（可留空使用密钥文件）",api_base_url:"API 接口地址",custom_model:"自定义模型名（可留空）",api_key_file:"密钥文件路径（可留空）",cache_mode:"缓存方式",variation:"新方案编号",reference_max_pixels:"每张参考图像素上限",cost_preview:"本次消耗",token_estimate:"参考图输入",last_tokens:"上次 API 用量",last_cost:"费用记录"})) {
        const w=this.widgets.find(w=>w.name===name);if(w) w.label=label;
      }
      const setStatus = (text) => {
        const w = this.widgets.find(w => w.name === "cost_preview");
        if (w) w.value = text;
        this.setDirtyCanvas(true, true);
      };
      this.addWidget("button", "只生成这一张（API）", null, async () => {
        if (this.mode !== 0) { setStatus("当前节点已关闭，请先手动启用。"); return; }
        try {
          const payload = await app.graphToPrompt();
          const all = payload.output;
          const id = String(this.id);
          const saves = Object.keys(all).filter(k => all[k].class_type === "SaveImage" &&
            all[k].inputs.images?.[0] === id);
          if (!all[id] || !saves.length) throw new Error("请连接并启用本场景的 SaveImage 节点。");
          const selected = {};
          const visit = (key) => {
            if (selected[key]) return;
            if (!all[key]) throw new Error(`缺少上游节点 ${key}`);
            selected[key] = all[key];
            for (const value of Object.values(all[key].inputs)) {
              if (Array.isArray(value) && value.length === 2 && typeof value[0] === "string" && Number.isInteger(value[1])) visit(value[0]);
            }
          };
          saves.forEach(visit);
          await api.queuePrompt(0, { ...payload, output: selected });
          setStatus("本场景已排队；其他场景保持原开关状态。");
        } catch (e) { setStatus(`未排队：${e.message}`); }
      }, { serialize: false });
    };
    const original = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      original?.apply(this, arguments);
      const status = message.fabric_status?.[0];
      if (!status) return;
      for (const name of ["cost_preview", "token_estimate", "last_tokens", "last_cost"]) {
        const widget = this.widgets?.find((w) => w.name === name);
        if (widget && status[name] !== undefined) {
          widget.value = status[name];
        }
      }
      this.setDirtyCanvas(true, true);
    };
  },
});
