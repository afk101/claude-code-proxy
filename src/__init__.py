"""Claude Code Proxy

A proxy server that enables Claude Code to work with OpenAI-compatible API providers.
"""

from dotenv import load_dotenv

# 从 .env 文件加载环境变量，覆盖进程已有变量
load_dotenv(override=True)
__version__ = "1.0.0"
__author__ = "Claude Code Proxy"
