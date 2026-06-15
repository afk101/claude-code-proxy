/**
 * CLI 参数相关常量和错误消息
 */

/** --append-system-prompt-file 参数名 */
export const ARG_APPEND_SYSTEM_PROMPT_FILE = '--append-system-prompt-file';

/** --append-system-prompt 参数名 */
export const ARG_APPEND_SYSTEM_PROMPT = '--append-system-prompt';

/**
 * 错误消息函数集合
 */
export const ERROR_MSG = {
  /** 文件不存在时的错误消息 */
  FILE_NOT_FOUND: (filePath) => `错误: 系统提示词文件不存在: ${filePath}`,

  /** 文件读取失败时的错误消息 */
  FILE_READ_FAILED: (filePath, reason) => `错误: 无法读取系统提示词文件: ${filePath}\n原因: ${reason}`,

  /** 缺少文件路径参数时的错误消息 */
  MISSING_FILE_PATH: '错误: --append-system-prompt-file 需要提供一个文件路径参数',
};
