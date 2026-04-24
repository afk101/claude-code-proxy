# Kimi 推理模型支持修复总结

## 问题根源

**moonshotai/kimi-k2.6** 模型在响应中包含 `reasoning_content` 字段。在多轮对话中，这个字段需要在历史消息中保留，否则 API 会报错：

```
Missing `reasoning_content` field in the assistant message at index 7
```

**重要说明**：本修复方案专门针对 moonshotai/kimi-k2.6 模型设计。OpenAI 的推理模型（o1/o3）使用不同的思考字段机制，不适用此方案。

## 核心方案

使用 **Claude 的 `thinking` 块** 作为中间格式，实现 `reasoning_content` 的双向转换：

```
OpenAI API  ←→  Claude Code
reasoning_content ↔ thinking 块
```

### 数据流

**第一轮（推理响应）**：
```
OpenAI → Claude
reasoning_content: "推理" → thinking块: {type: "thinking", thinking: "推理"}
```

**第二轮（历史消息）**：
```
Claude → OpenAI  
thinking块 → reasoning_content: "推理"
```

## 最新改进（2026-04-24）

### 关键优化

1. **模型判断**：只对 Kimi 推理模型（包含 "kimi-k2.6"）填充 `reasoning_content`
2. **条件精确**：只在有 `tool_calls` 时填充（与 LiteLLM 一致）
3. **默认值**：使用空格 `" "` 作为占位符（被 Kimi API 接受）
4. **日志优化**：去除冗余调试日志，只保留关键警告

### 核心代码逻辑

```python
# 判断是否是 Kimi 推理模型
def is_kimi_reasoning_model(model: str) -> bool:
    return "kimi-k2.6" in model

# 只在 Kimi 推理模型 + 有 tool_calls 时填充
if reasoning_content is None:
    if is_kimi_reasoning_model(openai_model) and tool_calls:
        openai_message["reasoning_content"] = " "
        logger.warning(...)
```

### 与 LiteLLM 对齐

| 维度 | LiteLLM | 我们的实现 | 状态 |
|------|---------|-----------|------|
| 模型判断 | ✅ `supports_reasoning()` | ✅ `is_kimi_reasoning_model()` | ✅ 对齐 |
| 触发条件 | ✅ `assistant + tool_calls + 缺失` | ✅ `assistant + tool_calls + 缺失` | ✅ 对齐 |
| 默认值 | ✅ `" "` (空格) | ✅ `" "` (空格) | ✅ 对齐 |
| 日志警告 | ✅ 有警告 | ✅ 有警告 | ✅ 对齐 |

## 修改的文件

1. **`src/models/claude.py`**
   - 新增 `ClaudeContentBlockThinking` 类
   - 更新消息类型定义

2. **`src/conversion/response_converter.py`**
   - 响应转换：`reasoning_content` → `thinking` 块
   - 流式响应：正确发送 thinking 块事件

3. **`src/conversion/request_converter.py`** (最新)
   - 新增 `is_kimi_reasoning_model()` 模型判断函数
   - 改进 `convert_claude_assistant_message()` 函数签名
   - 添加模型判断和 tool_calls 条件
   - 使用空格作为默认值
   - 去除调试日志，只保留必要警告

## 支持的模型

### Kimi 推理模型（需要 reasoning_content）
- moonshotai/kimi-k2.6 ✅ **（唯一支持）**

**注意**：OpenAI 的推理模型（o1/o3 系列）使用不同的思考字段机制，不在本方案支持范围内。

### 非推理模型（不受影响）
- GPT-4 系列 ✅
- Claude 系列 ✅
- 其他模型 ✅

## 关键特性

- ✅ 多轮对话支持
- ✅ 推理内容正确传递
- ✅ 向后兼容（不影响其他模型）
- ✅ 流式和非流式都支持
- ✅ 与 LiteLLM 逻辑对齐
- ✅ 性能优化（按需填充）
- ✅ 日志精简（生产可用）

## 性能影响

- **模型范围**：仅 Kimi 推理模型填充
- **消息范围**：仅含 tool_calls 的消息填充
- **性能**：避免全局填充，减少冗余传输
- **兼容性**：其他模型零影响
