import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  ARG_APPEND_SYSTEM_PROMPT_FILE,
  ARG_APPEND_SYSTEM_PROMPT,
  ERROR_MSG,
} from './constants.js';

/**
 * 读取提示词文件内容
 * 相对路径基于 process.cwd() 解析，绝对路径直接使用
 * @param {string} filePath - 文件路径（相对或绝对）
 * @returns {string} 文件内容
 */
function readPromptFile(filePath) {
  // 展开 ~ 为用户主目录，path.resolve 不会自动处理
  const expanded = filePath.startsWith('~')
    ? path.join(os.homedir(), filePath.slice(1))
    : filePath;
  const absolutePath = path.resolve(process.cwd(), expanded);

  if (!fs.existsSync(absolutePath)) {
    console.error(ERROR_MSG.FILE_NOT_FOUND(absolutePath));
    process.exit(1);
  }

  try {
    return fs.readFileSync(absolutePath, 'utf-8');
  } catch (err) {
    console.error(ERROR_MSG.FILE_READ_FAILED(absolutePath, err.message));
    process.exit(1);
  }
}

/**
 * 解析命令行参数中的 --append-system-prompt-file，将其替换为 --append-system-prompt
 *
 * 支持以下格式：
 *   --append-system-prompt-file ./my-prompt.txt   （空格分隔）
 *   --append-system-prompt-file=./my-prompt.txt   （等号分隔）
 *
 * 相对路径基于 process.cwd() 解析。
 * 支持多次使用 --append-system-prompt-file，每次生成独立的 --append-system-prompt 参数。
 * 其他参数原样透传，不受影响。
 *
 * @param {string[]} args - 原始命令行参数数组
 * @returns {string[]} 处理后的参数数组（--append-system-prompt-file 已替换为 --append-system-prompt）
 */
export function resolvePromptFileArgs(args) {
  const result = [];
  let i = 0;

  while (i < args.length) {
    const arg = args[i];

    // 格式1: --append-system-prompt-file=<path>（等号分隔）
    if (arg.startsWith(ARG_APPEND_SYSTEM_PROMPT_FILE + '=')) {
      const filePath = arg.slice(ARG_APPEND_SYSTEM_PROMPT_FILE.length + 1);
      if (!filePath) {
        console.error(ERROR_MSG.MISSING_FILE_PATH);
        process.exit(1);
      }
      const content = readPromptFile(filePath);
      result.push(ARG_APPEND_SYSTEM_PROMPT, content);
      i += 1;
      continue;
    }

    // 格式2: --append-system-prompt-file <path>（空格分隔，两个独立参数）
    if (arg === ARG_APPEND_SYSTEM_PROMPT_FILE) {
      const filePath = args[i + 1];
      // 防止将下一个标志位误认为文件路径
      if (!filePath || filePath.startsWith('-')) {
        console.error(ERROR_MSG.MISSING_FILE_PATH);
        process.exit(1);
      }
      const content = readPromptFile(filePath);
      result.push(ARG_APPEND_SYSTEM_PROMPT, content);
      i += 2;
      continue;
    }

    // 其他参数原样透传
    result.push(arg);
    i += 1;
  }

  return result;
}
