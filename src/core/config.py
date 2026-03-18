import os
import sys

# 模型到 MAX_TOKENS_LIMIT 的映射
MODEL_TOKENS_MAP = {
    "copilotcode-15": 200000,
    "copilotcode-14": 200000,
    "copilotcode-13": 200000,
    "lyra-flash-11": 1000000,
    "lyra-flash-6": 1000000,
    "cortex-17": 1000000,
    "cortex-16": 400000,
    "cortex-15":400000,
    "cortex-12":200000
}

# Configuration
class Config:
    def __init__(self):
        self.openai_api_key = os.environ.get("OPENAI_API_KEY")
        if not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY not found in environment variables")
        
        # Add Proxy API key for client validation
        # We use PROXY_API_KEY instead of ANTHROPIC_API_KEY to avoid conflicts with client tools
        self.client_api_key = os.environ.get("PROXY_API_KEY")
        if not self.client_api_key:
            print("Warning: PROXY_API_KEY not set. Client API key validation will be disabled.")
        
        self.openai_base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.azure_api_version = os.environ.get("AZURE_API_VERSION")  # For Azure OpenAI
        self.host = os.environ.get("HOST", "0.0.0.0")
        self.port = int(os.environ.get("PORT", "8082"))
        self.log_level = os.environ.get("LOG_LEVEL", "INFO")
        self.max_tokens_limit = int(os.environ.get("MAX_TOKENS_LIMIT", "4096"))
        self.min_tokens_limit = int(os.environ.get("MIN_TOKENS_LIMIT", "100"))
        
        # Connection settings
        self.request_timeout = int(os.environ.get("REQUEST_TIMEOUT", "90"))
        self.read_timeout = int(os.environ.get("READ_TIMEOUT", "240"))
        self.max_retries = int(os.environ.get("MAX_RETRIES", "2"))
        
        # Model settings - BIG and SMALL models
        self.big_model = os.environ.get("BIG_MODEL", "gpt-4o")
        self.middle_model = os.environ.get("MIDDLE_MODEL", self.big_model)
        self.small_model = os.environ.get("SMALL_MODEL", "gpt-4o-mini")

        # Auto tokens mode - 根据模型自动设置 MAX_TOKENS_LIMIT
        self.auto_tokens_mode = os.environ.get("AUTO_TOKENS_MODE", "").lower() == "true"

    def get_max_tokens_for_model(self, model: str) -> int:
        """根据模型获取 MAX_TOKENS_LIMIT

        如果启用了 auto_tokens_mode，则从 MODEL_TOKENS_MAP 中查找对应的值
        否则返回配置的 max_tokens_limit
        """
        if self.auto_tokens_mode and model in MODEL_TOKENS_MAP:
            return MODEL_TOKENS_MAP[model]
        return self.max_tokens_limit
        
    def validate_api_key(self):
        """Basic API key validation"""
        if not self.openai_api_key:
            return False
        # Basic format check for OpenAI API keys
        if not self.openai_api_key.startswith('sk-'):
            return False
        return True
        
    def validate_client_api_key(self, client_api_key):
        """Validate client's API key"""
        # If no PROXY_API_KEY is set in environment, skip validation
        if not self.client_api_key:
            return True
            
        # Check if the client's API key matches the expected value
        return client_api_key == self.client_api_key
    
    def get_custom_headers(self):
        """Get custom headers from environment variables"""
        custom_headers = {}
        
        # Get all environment variables
        env_vars = dict(os.environ)
        
        # Find CUSTOM_HEADER_* environment variables
        for env_key, env_value in env_vars.items():
            if env_key.startswith('CUSTOM_HEADER_'):
                # Convert CUSTOM_HEADER_KEY to Header-Key
                # Remove 'CUSTOM_HEADER_' prefix and convert to header format
                header_name = env_key[14:]  # Remove 'CUSTOM_HEADER_' prefix
                
                if header_name:  # Make sure it's not empty
                    # Convert underscores to hyphens for HTTP header format
                    header_name = header_name.replace('_', '-')
                    custom_headers[header_name] = env_value
        
        return custom_headers

try:
    config = Config()
    print(f" Configuration loaded: API_KEY={'*' * 20}..., BASE_URL='{config.openai_base_url}'")
except Exception as e:
    print(f"=4 Configuration Error: {e}")
    sys.exit(1)
