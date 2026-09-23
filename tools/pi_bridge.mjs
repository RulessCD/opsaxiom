#!/usr/bin/env node
/**
 * pi_bridge.mjs —— OpsAxiom → Pi Agent Harness 的多 provider 桥（M-3）。
 *
 * stdin 读一个 JSON：{provider, model, endpoint?, apiKey?, system, prompt, maxTokens?}
 * stdout 写一个 JSON：{text} 或 {error}
 *
 * 依赖 @earendil-works/pi-ai（node >= 22.19）：
 *   npm install --prefix ~/.local/pi-agent @earendil-works/pi-ai
 * 解析顺序：tools/（桥同目录）→ ~/.local/pi-agent/node_modules（README 智能入口
 * 同一份安装，装一次两处共用）。都不满足时本脚本以非零退出，
 * llm.py 侧按约定降级（宁可拒绝，不可翻车 R10）。
 *
 * 边界：桥只做"一问一答文本补全"——OpsAxiom 的 LLM 适配层三调用点只需要这个；
 * 不透传工具调用（判读/命令永远不走模型，宪法 R7/R9/R10）。
 */

// 标准解析（桥同目录/上层 node_modules）失败时，兜底再试 README 指引的
// 智能入口安装位 ~/.local/pi-agent——"装一次 pi-ai，模型后端与 pi 入口共用"。
// 二次 import 直指官方导出 ./providers/all 对应的真实文件（file://）。
async function loadPiAi() {
  try {
    return await import("@earendil-works/pi-ai/providers/all");
  } catch (e1) {
    if (e1.code !== "ERR_MODULE_NOT_FOUND") throw e1;   // 缺依赖以外的问题原样抛
    const os = await import("node:os");
    const alt = os.homedir() + "/.local/pi-agent/node_modules/@earendil-works/pi-ai";
    return import("file://" + alt + "/dist/providers/all.js");
  }
}

async function main() {
  const chunks = [];
  for await (const c of process.stdin) chunks.push(c);
  const req = JSON.parse(Buffer.concat(chunks).toString("utf-8"));

  const { builtinModels } = await loadPiAi();

  // apiKey 优先显式传入；否则走 pi-ai 自己的 env 解析（OPENAI_API_KEY 等）
  if (req.apiKey) {
    const envKey = `${String(req.provider || "openai").toUpperCase().replace(/-/g, "_")}_API_KEY`;
    process.env[envKey] = req.apiKey;
  }

  const models = builtinModels();
  const model = models.getModel(req.provider || "openai", req.model);
  if (!model) {
    process.stdout.write(JSON.stringify({
      error: `unknown model ${req.provider}/${req.model}` }));
    process.exit(3);
  }

  const context = {
    systemPrompt: req.system || "",
    messages: [{ role: "user", content: req.prompt || "", timestamp: Date.now() }],
    tools: [],
  };

  let text = "";
  const s = models.stream(model, context, { maxTokens: req.maxTokens || 512 });
  for await (const ev of s) {
    if (ev.type === "text_delta") text += ev.delta;
    if (ev.type === "error") {
      // pi-ai 的 error 事件是 assistant 消息对象，人话文本在 errorMessage
      const a = ev.error || {};
      const msg = a.errorMessage || a.error?.message || String(a);
      process.stdout.write(JSON.stringify({ error: msg }));
      process.exit(4);
    }
  }
  process.stdout.write(JSON.stringify({ text }));
}

main().catch((e) => {
  process.stdout.write(JSON.stringify({ error: String(e && e.message || e) }));
  process.exit(2);
});
