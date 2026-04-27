#!/usr/bin/env node

if (process.argv.includes("--help") || process.argv.includes("-h")) {
  console.log("Usage: node openai_sdk_compat.mjs [prompt]");
  console.log("Environment variables:");
  console.log("  API_BASE_URL=http://127.0.0.1:8000");
  console.log("  MODEL_ID=deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B");
  console.log("  API_KEY=sk-local-placeholder");
  console.log("  MAX_TOKENS=16");
  process.exit(0);
}

async function main() {
  const { default: OpenAI } = await import("openai");

  const prompt = process.argv[2] || "Introduce RISC-V architecture.";
  const baseUrl = process.env.API_BASE_URL || "http://127.0.0.1:8000";
  const model = process.env.MODEL_ID || "deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B";
  const apiKey = process.env.API_KEY || "sk-local-placeholder";
  const maxTokens = Number.parseInt(process.env.MAX_TOKENS || "16", 10);

  const client = new OpenAI({
    apiKey,
    baseURL: `${baseUrl}/v1`,
  });

  const models = await client.models.list();
  console.log("Available models:");
  for (const item of models.data) {
    console.log(`  - ${item.id}`);
  }

  const response = await client.chat.completions.create({
    model,
    messages: [{ role: "user", content: prompt }],
    max_tokens: maxTokens,
  });

  console.log("\nAssistant response:");
  const firstChoice = response.choices && response.choices[0] ? response.choices[0] : null;
  const firstMessage = firstChoice && firstChoice.message ? firstChoice.message : null;
  console.log(firstMessage && firstMessage.content ? firstMessage.content : "");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
