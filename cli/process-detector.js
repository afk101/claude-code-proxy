import { execSync } from 'node:child_process';
import http from 'node:http';

/**
 * 使用 lsof 检测指定端口是否有进程在监听
 * @param {number} port - 要检测的端口号
 * @returns {boolean} 端口是否正在监听
 */
function isPortListening(port) {
  try {
    execSync(`lsof -i :${port} -sTCP:LISTEN`, { stdio: 'ignore' });
    return true;
  } catch {
    return false;
  }
}

/**
 * 通过 HTTP 请求 /health 端点验证是否是我们的代理服务
 * @param {number} port - 要检测的端口号
 * @param {number} [timeout=2000] - 超时时间（毫秒）
 * @returns {Promise<boolean>} 是否是代理服务
 */
function checkHealthEndpoint(port, timeout = 2000) {
  return new Promise((resolve) => {
    const req = http.get(`http://127.0.0.1:${port}/health`, { timeout }, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        try {
          const json = JSON.parse(data);
          // 检查是否包含我们代理特有的字段
          resolve(json.status === 'healthy' && json.openai_api_configured !== undefined);
        } catch {
          resolve(false);
        }
      });
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => { req.destroy(); resolve(false); });
  });
}

/**
 * 批量检测所有模型的运行状态
 * @param {Array<{id: string, port: number}>} models - 模型配置数组
 * @returns {Promise<Map<string, boolean>>} 模型 id 到运行状态的映射
 */
async function detectRunningProxies(models) {
  const statusMap = new Map();
  const checks = models.map(async (model) => {
    const portListening = isPortListening(model.port);
    if (portListening) {
      // 端口在监听，进一步通过 /health 验证
      const isProxy = await checkHealthEndpoint(model.port);
      statusMap.set(model.id, isProxy);
    } else {
      statusMap.set(model.id, false);
    }
  });
  await Promise.all(checks);
  return statusMap;
}

/**
 * 获取监听指定端口的进程 PID 列表
 * @param {number} port - 要查询的端口号
 * @returns {number[]} 监听该端口的 PID 列表
 */
function getPortPids(port) {
  try {
    const output = execSync(`lsof -i :${port} -sTCP:LISTEN -t`, { encoding: 'utf8' });
    return output.trim().split('\n').map(Number).filter(Boolean);
  } catch {
    return [];
  }
}

/**
 * 通过进程组关闭指定端口的代理进程
 * 代理启动链为 bash(ccc) → caffeinate → uv → python，守护模式有 while true 循环
 * 通过获取 PGID 并 kill 整个进程组，确保完整关闭
 * @param {number} port - 要关闭的代理端口号
 * @returns {{ success: boolean, pids: number[] }} 关闭结果
 */
function killProxyByPort(port) {
  const pids = getPortPids(port);
  if (pids.length === 0) {
    return { success: false, pids: [] };
  }

  const killedPgids = new Set();
  for (const pid of pids) {
    try {
      // 获取进程组 ID
      const pgidStr = execSync(`ps -o pgid= -p ${pid}`, { encoding: 'utf8' }).trim();
      const pgid = Number(pgidStr);
      if (pgid && !killedPgids.has(pgid)) {
        killedPgids.add(pgid);
        try {
          // kill 整个进程组
          execSync(`kill -- -${pgid}`, { stdio: 'ignore' });
        } catch {
          // 进程组可能已退出，尝试直接 kill 单个进程
          try {
            execSync(`kill ${pid}`, { stdio: 'ignore' });
          } catch {
            // 进程已不存在，忽略
          }
        }
      }
    } catch {
      // 获取 PGID 失败，尝试直接 kill 单个进程
      try {
        execSync(`kill ${pid}`, { stdio: 'ignore' });
      } catch {
        // 进程已不存在，忽略
      }
    }
  }

  return { success: true, pids };
}

export { isPortListening, checkHealthEndpoint, detectRunningProxies, getPortPids, killProxyByPort };
