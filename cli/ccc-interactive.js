#!/usr/bin/env node

import { execSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import Enquirer from 'enquirer';
import { parseProxyModelsConf, getDefaultConfigPath } from './config-parser.js';
import { detectRunningProxies, killProxyByPort } from './process-detector.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/**
 * 收集透传参数（过滤掉 PORT/MODEL/MAX_TOKENS_LIMIT 相关参数和 -i）
 * @param {string[]} args - 原始命令行参数
 * @returns {string[]} 过滤后的透传参数
 */
function collectPassthroughArgs(args) {
  const filterPatterns = [
    /^-i$/,
    /^--interactive$/,
    /^PORT=/i,
    /^BIG_MODEL=/i,
    /^MIDDLE_MODEL=/i,
    /^SMALL_MODEL=/i,
    /^MAX_TOKENS_LIMIT=/i,
  ];
  return args.filter((arg) => !filterPatterns.some((p) => p.test(arg)));
}

/**
 * 启动单个代理实例
 * @param {{id: string, port: number, model: string, max_tokens: number, description: string}} modelConfig - 模型配置
 * @param {string} cccPath - ccc 脚本路径
 * @param {string[]} passthroughArgs - 透传参数
 */
function startProxy(modelConfig, cccPath, passthroughArgs) {
  const args = [
    '-auto',
    `PORT=${modelConfig.port}`,
    `BIG_MODEL=${modelConfig.model}`,
    `MIDDLE_MODEL=${modelConfig.model}`,
    `SMALL_MODEL=${modelConfig.model}`,
    `MAX_TOKENS_LIMIT=${modelConfig.max_tokens}`,
    ...passthroughArgs,
  ];
  const cmd = `nohup "${cccPath}" ${args.join(' ')} > /dev/null 2>&1 &`;
  console.log(`  启动: ${modelConfig.description} (端口 ${modelConfig.port})`);
  execSync(cmd, { shell: '/bin/bash', stdio: 'ignore' });
}

async function main() {
  // 解析配置
  const configPath = getDefaultConfigPath();
  const models = parseProxyModelsConf(configPath);
  if (models.length === 0) {
    console.log('未在 proxy_models.conf 中找到任何模型配置');
    process.exit(1);
  }

  // 检测已运行的代理
  console.log('正在检测已运行的代理...');
  const runningStatus = await detectRunningProxies(models);

  // 构建多选菜单的 choices（运行中的可选中关闭，未运行的可选中启动）
  const choices = models.map((model) => {
    const isRunning = runningStatus.get(model.id);
    const label = `${model.description}  (端口: ${model.port}, tokens: ${model.max_tokens})`;
    if (isRunning) {
      return {
        name: model.id,
        message: `${label}  [运行中 - 选中关闭]`,
      };
    }
    return {
      name: model.id,
      message: label,
    };
  });

  // 使用 enquirer 弹出多选菜单
  const { MultiSelect } = Enquirer;
  const prompt = new MultiSelect({
    name: 'proxies',
    message: '选择要操作的代理（未运行=启动，运行中=关闭）',
    choices,
    symbols: { indicator: { on: '●', off: '○' } },
  });

  let selected;
  try {
    selected = await prompt.run();
  } catch {
    // 用户按 Ctrl+C 取消
    console.log('已取消');
    process.exit(0);
  }

  if (!selected || selected.length === 0) {
    console.log('未选择任何模型');
    process.exit(0);
  }

  // 按运行状态分流处理
  const toStart = selected.filter((id) => !runningStatus.get(id));
  const toStop = selected.filter((id) => runningStatus.get(id));

  // 关闭已运行的代理
  if (toStop.length > 0) {
    console.log(`\n正在关闭 ${toStop.length} 个代理...\n`);
    for (const modelId of toStop) {
      const modelConfig = models.find((m) => m.id === modelId);
      if (modelConfig) {
        const result = killProxyByPort(modelConfig.port);
        if (result.success) {
          console.log(`  已关闭: ${modelConfig.description} (端口 ${modelConfig.port}, PID: ${result.pids.join(', ')})`);
        } else {
          console.log(`  关闭失败: ${modelConfig.description} (端口 ${modelConfig.port}，未找到进程)`);
        }
      }
    }
  }

  // 启动未运行的代理
  if (toStart.length > 0) {
    const projectRoot = path.resolve(__dirname, '..');
    const cccPath = path.join(projectRoot, 'start_claude_code_proxy.sh');
    const passthroughArgs = collectPassthroughArgs(process.argv.slice(2));

    console.log(`\n正在启动 ${toStart.length} 个代理...\n`);
    for (const modelId of toStart) {
      const modelConfig = models.find((m) => m.id === modelId);
      if (modelConfig) {
        startProxy(modelConfig, cccPath, passthroughArgs);
      }
    }
  }

  // 输出摘要
  const summaryParts = [];
  if (toStop.length > 0) summaryParts.push(`关闭 ${toStop.length} 个`);
  if (toStart.length > 0) summaryParts.push(`启动 ${toStart.length} 个`);
  console.log(`\n操作完成：${summaryParts.join('，')}。`);
  if (toStart.length > 0) {
    console.log('使用 cc 命令可快捷连接到运行中的代理。');
  }
}

main().catch((err) => {
  console.error('错误:', err.message);
  process.exit(1);
});
