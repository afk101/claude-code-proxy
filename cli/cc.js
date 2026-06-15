#!/usr/bin/env node

import { spawn } from 'node:child_process';
import Enquirer from 'enquirer';
import { parseProxyModelsConf, getDefaultConfigPath } from './config-parser.js';
import { detectRunningProxies } from './process-detector.js';
import { resolvePromptFileArgs } from './prompt-file-resolver.js';

/**
 * 构建 claude 子进程的环境变量
 * 当 ANTHROPIC_API_KEY 和 ANTHROPIC_AUTH_TOKEN 都不存在时，注入默认的 ANTHROPIC_AUTH_TOKEN
 * @param {Record<string, string|undefined>} baseEnv - 基础环境变量
 * @param {string} baseUrl - 代理地址
 * @returns {Record<string, string|undefined>} 合并后的环境变量
 */
function buildClaudeEnv(baseEnv, baseUrl) {
  const env = { ...baseEnv, ANTHROPIC_BASE_URL: baseUrl };
  if (!baseEnv.ANTHROPIC_API_KEY && !baseEnv.ANTHROPIC_AUTH_TOKEN) {
    env.ANTHROPIC_AUTH_TOKEN = 'claude_code_proxy_inject';
  }
  return env;
}

/**
 * 连接到指定端口的代理
 * @param {number} port - 代理端口
 * @param {string[]} args - 透传给 claude 的命令行参数
 */
function connectToProxy(port, args = []) {
  const baseUrl = `http://127.0.0.1:${port}`;
  console.log(`连接到代理: ${baseUrl}\n`);
  spawn('claude', args, {
    stdio: 'inherit',
    env: buildClaudeEnv(process.env, baseUrl),
  });
}

async function main() {
  // 收集 cc 命令后的参数，透传给 claude
  const passthrough = resolvePromptFileArgs(process.argv.slice(2));

  // 解析配置
  const configPath = getDefaultConfigPath();
  const models = parseProxyModelsConf(configPath);
  if (models.length === 0) {
    console.log('未在 proxy_models.conf 中找到任何模型配置');
    process.exit(1);
  }

  // 检测运行中的代理
  console.log('正在检测运行中的代理...');
  const runningStatus = await detectRunningProxies(models);

  // 过滤出运行中的代理
  const runningModels = models.filter((m) => runningStatus.get(m.id));

  if (runningModels.length === 0) {
    console.log('没有检测到运行中的代理。');
    console.log('请先使用 ccc -auto -d -i 启动代理。');
    process.exit(1);
  }

  if (runningModels.length === 1) {
    // 只有一个运行中的代理，直接连接
    const model = runningModels[0];
    console.log(`检测到 1 个运行中的代理: ${model.description}\n`);
    connectToProxy(model.port, passthrough);
    return;
  }

  // 多个运行中的代理，弹出单选菜单
  console.log(`检测到 ${runningModels.length} 个运行中的代理\n`);

  const { Select } = Enquirer;
  const prompt = new Select({
    name: 'proxy',
    message: '选择要连接的代理',
    choices: runningModels.map((model) => ({
      name: String(model.port),
      message: `${model.description}  (端口: ${model.port})`,
    })),
  });

  let selectedPort;
  try {
    selectedPort = await prompt.run();
  } catch {
    // 用户按 Ctrl+C 取消
    console.log('已取消');
    process.exit(0);
  }

  connectToProxy(parseInt(selectedPort, 10), passthrough);
}

main().catch((err) => {
  console.error('错误:', err.message);
  process.exit(1);
});
