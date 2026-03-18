import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/**
 * 解析 proxy_models.conf 配置文件
 * 按 [section] 分块，解析 key=value，忽略 # 注释和空行
 * @param {string} configPath - 配置文件的绝对路径
 * @returns {Array<{id: string, name: string, port: number, model: string, max_tokens: number, description: string}>} 模型配置对象数组
 */
function parseProxyModelsConf(configPath) {
  const content = fs.readFileSync(configPath, 'utf-8');
  const lines = content.split('\n');
  const models = [];
  let currentSection = null;
  let currentModel = null;

  for (const rawLine of lines) {
    const line = rawLine.trim();

    // 忽略空行和注释行
    if (!line || line.startsWith('#')) {
      continue;
    }

    // 检测 section 头部 [section-name]
    const sectionMatch = line.match(/^\[([^\]]+)\]$/);
    if (sectionMatch) {
      // 保存上一个 section
      if (currentModel) {
        models.push(currentModel);
      }
      currentSection = sectionMatch[1];
      currentModel = { id: currentSection };
      continue;
    }

    // 解析 key=value
    const kvMatch = line.match(/^([^=]+)=(.*)$/);
    if (kvMatch && currentModel) {
      const key = kvMatch[1].trim();
      const value = kvMatch[2].trim();

      if (key === 'port' || key === 'max_tokens') {
        currentModel[key] = parseInt(value, 10);
      } else {
        currentModel[key] = value;
      }
    }
  }

  // 保存最后一个 section
  if (currentModel) {
    models.push(currentModel);
  }

  return models;
}

/**
 * 获取默认配置文件路径
 * @returns {string} proxy_models.conf 的绝对路径
 */
function getDefaultConfigPath() {
  return path.resolve(__dirname, '..', 'proxy_models.conf');
}

export { parseProxyModelsConf, getDefaultConfigPath };
