from src.core.config import config, MODEL_TOKENS_MAP

class ModelManager:
    def __init__(self, config):
        self.config = config

    def map_claude_model_to_openai(self, claude_model: str) -> str:
        """Map Claude model names to OpenAI model names based on BIG/SMALL pattern"""
        # 如果 claude_model 匹配 MODEL_TOKENS_MAP 中的任意一个键，直接返回（适配ClaudeCodeAgent的model直接调用）
        if claude_model in MODEL_TOKENS_MAP:
            return claude_model

        # If it's already an OpenAI model, return as-is
        if claude_model.startswith("gpt-") or claude_model.startswith("o1-"):
            return claude_model

        # If it's other supported models (ARK/Doubao/DeepSeek), return as-is
        if (claude_model.startswith("ep-") or claude_model.startswith("doubao-") or 
            claude_model.startswith("deepseek-")):
            return claude_model
        
        # Map based on model naming patterns
        model_lower = claude_model.lower()
        if 'haiku' in model_lower:
            return self.config.small_model
        elif 'sonnet' in model_lower:
            return self.config.middle_model
        elif 'opus' in model_lower:
            return self.config.big_model
        else:
            # Default to big model for unknown models
            return self.config.big_model

model_manager = ModelManager(config)