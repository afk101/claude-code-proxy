# reasoning_content 字段处理修复（针对 moonshotai/kimi-k2.6）

## 问题描述

moonshotai/kimi-k2.6 模型会在响应中包含 `reasoning_content` 字段。在多轮对话中，如果推理内容没有作为独立字段保留，API 会因历史消息缺少该字段而报错：

```
Error code: 400 - {'error': {'message': 'Missing `reasoning_content` field in the assistant message at index 7', 'type': 'BadRequest'}}
```

**重要说明**：本修复方案专门针对 moonshotai/kimi-k2.6 模型设计。OpenAI 的推理模型（o1/o3）使用不同的思考字段机制，不适用此方案。

## 修复方案

### 核心思路

使用 Claude 的 `thinking` 块作为中间格式，实现 `reasoning_content` 的双向转换：

```
OpenAI API  <--->  Claude Code
reasoning_content <-> thinking 块
```

### 1. 响应转换（OpenAI -> Claude）

**非流式响应**：
- 将 `reasoning_content` 转换为 Claude 的 `thinking` 块
- 保持推理内容和普通文本内容为独立的内容块

**流式响应**：
- 使用 `content_block_start` 事件发送 thinking 块的开始
- 使用 `thinking_delta` 类型的增量发送推理内容
- 确保推理块和文本块使用不同的索引

### 2. 请求转换（Claude -> OpenAI）

在转换 assistant 历史消息时：
- 识别 Claude 响应中的 `thinking` 块
- 将 `thinking` 块内容提取为 OpenAI 格式的 `reasoning_content` 字段
- **关键改进**：只对 Kimi 推理模型 + 有 tool_calls 的消息填充

## 最新改进（2026-04-24）

### 模型判断

```python
def is_kimi_reasoning_model(model: str) -> bool:
    """判断是否是 Kimi 推理模型（需要 reasoning_content 字段）"""
    return "kimi-k2.6" in model
```

**作用**：
- 只对 Kimi 推理模型填充 `reasoning_content`
- 避免对其他模型造成不必要的影响

### 条件精确（与 LiteLLM 一致）

```python
# 只在满足以下三个条件时填充：
# 1. 是 Kimi 推理模型
# 2. 有 tool_calls
# 3. reasoning_content 为空
if is_kimi_reasoning_model(openai_model) and tool_calls:
    if reasoning_content is None:
        openai_message["reasoning_content"] = " "
        logger.warning(...)
```

### 默认值策略

- 使用空格 `" "` 作为占位符
- Kimi API 接受非空字符串，拒绝空字符串 `""`
- 与 LiteLLM 实现完全一致

### 日志优化

- 去除所有调试日志（共 9 处）
- 只保留 1 个关键警告日志（填充时触发）
- 生产环境友好

## 实现细节

### 修改的文件

1. **`src/models/claude.py`** - 模型定义
   - 新增 `ClaudeContentBlockThinking` 类
   - 更新 `ClaudeMessage` 的 content 类型定义

2. **`src/conversion/response_converter.py`** - 响应转换器
   - `convert_openai_to_claude_response` - 非流式响应转换
   - `convert_openai_streaming_to_claude` - 流式响应转换
   - `convert_openai_streaming_to_claude_with_cancellation` - 带取消的流式转换

3. **`src/conversion/request_converter.py`** - 请求转换器（最新）
   - 新增 `is_kimi_reasoning_model()` - 模型判断函数
   - 改进 `convert_claude_assistant_message()` - 添加模型参数和条件判断
   - 去除调试日志 - 保持代码简洁

### 数据流程

**第一轮对话（推理响应）**：
```
OpenAI Response:
{
  "message": {
    "reasoning_content": "推理过程",
    "content": "最终答案"
  }
}
    ↓ 转换
Claude Response:
{
  "content": [
    {"type": "thinking", "thinking": "推理过程"},
    {"type": "text", "text": "最终答案"}
  ]
}
```

**第二轮对话（历史消息）**：
```
Claude Request (历史消息):
{
  "role": "assistant",
  "content": [
    {"type": "thinking", "thinking": "推理过程"},
    {"type": "text", "text": "最终答案"}
  ]
}
    ↓ 转换
OpenAI Request:
{
  "role": "assistant",
  "reasoning_content": "推理过程",
  "content": "最终答案"
}
```

**填充场景（仅 Kimi 推理模型 + tool_calls）**：
```
Claude Request (无 thinking 块):
{
  "role": "assistant",
  "content": [
    {"type": "text", "text": "调用工具"},
    {"type": "tool_use", "id": "...", "name": "search", ...}
  ]
}
    ↓ 转换 (kimi-k2.6 模型)
OpenAI Request:
{
  "role": "assistant",
  "content": "调用工具",
  "reasoning_content": " ",  ← 自动填充空格
  "tool_calls": [...]
}
```

## 与 LiteLLM 对比

| 维度 | LiteLLM | 我们的实现 | 状态 |
|------|---------|-----------|------|
| **模型判断** | ✅ `supports_reasoning()` | ✅ `is_kimi_reasoning_model()` | ✅ 对齐 |
| **触发条件** | ✅ `assistant + tool_calls + 缺失` | ✅ `assistant + tool_calls + 缺失` | ✅ 对齐 |
| **默认值** | ✅ `" "` (空格) | ✅ `" "` (空格) | ✅ 对齐 |
| **日志警告** | ✅ 有警告 | ✅ 有警告 | ✅ 对齐 |
| **provider_specific_fields** | ✅ 支持提升 | ❌ 无（合理） | ⚠️ 差异（不影响） |

## 兼容性

### Kimi 推理模型
- ✅ moonshotai/kimi-k2.6 - 完整支持 **（唯一支持）**

**注意**：OpenAI 的推理模型（o1/o3 系列）使用不同的思考字段机制，不在本方案支持范围内。

### 非推理模型（不受影响）
- ✅ GPT-4 系列 - 零影响
- ✅ Claude 系列 - 零影响
- ✅ 其他模型 - 零影响

### 特性保证
- ✅ **向后兼容**：对于不包含 `reasoning_content` 的响应，处理逻辑保持不变
- ✅ **多轮对话支持**：推理内容在多轮对话中正确传递
- ✅ **流式和非流式**：两种模式都已支持推理内容处理
- ✅ **性能优化**：只对需要的模型和消息填充，避免冗余
- ✅ **生产可用**：代码简洁，日志精简

## 性能影响

### 改进前
- 对所有模型的所有 assistant 消息填充
- 冗余传输，浪费带宽

### 改进后
- 只对 Kimi 推理模型填充
- 只对含 tool_calls 的消息填充
- 性能影响最小化

## 与之前方案的区别

**之前方案（v1）**：
- 将推理内容合并到文本块中，使用 `[Reasoning]...[/Reasoning]` 标记
- 问题：历史消息中缺少 `reasoning_content` 字段，导致 API 验证失败

**v2 方案**：
- 使用 Claude 的 `thinking` 块作为独立结构
- 实现真正的双向转换，保持数据完整性
- 支持多轮对话，符合 API 规范
- **但**：对所有 assistant 消息填充（过度）

**当前方案（v3 - 最终）**：
- 继承 v2 的核心方案
- 添加模型判断：只对 Kimi 推理模型填充
- 添加条件判断：只对有 tool_calls 的消息填充
- 与 LiteLLM 逻辑完全对齐
- 性能优化，生产可用
