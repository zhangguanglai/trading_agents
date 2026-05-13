import os
from typing import Dict, Any

# Default configuration for TradingAgents
DEFAULT_CONFIG: Dict[str, Any] = {
    # LLM provider settings
    "llm_provider": os.getenv("TA_LLM_PROVIDER", "deepseek"),
    "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
    "backend_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    
    # Model selection
    "deep_think_llm": os.getenv("TA_LLM_DEEP", "deepseek-chat"),
    "quick_think_llm": os.getenv("TA_LLM_QUICK", "deepseek-chat"),
    
    # Data vendor configuration
    "data_vendors": {
        "stock_data": os.getenv("TA_DATA_VENDOR", "akshare"),
        "fundamentals": os.getenv("TA_FUND_VENDOR", "akshare"),
        "news": os.getenv("TA_NEWS_VENDOR", "akshare"),
        "sentiment": os.getenv("TA_SENTIMENT_VENDOR", "akshare"),
    },
    
    # Debate and discussion settings
    # 优化：生产环境减少辩论轮数以提升响应速度
    "max_debate_rounds": int(os.getenv("TA_MAX_DEBATE") or "1"),
    "max_risk_discuss_rounds": int(os.getenv("TA_MAX_RISK") or "1"),
    
    # Prompt and output settings
    "prompt_language": os.getenv("TA_PROMPT_LANG", "zh"),
    "output_language": os.getenv("TA_OUTPUT_LANG", "zh"),
    
    # Feature flags
    "enable_streaming": os.getenv("TA_ENABLE_STREAMING", "true").lower() == "true",
    "enable_caching": os.getenv("TA_ENABLE_CACHING", "true").lower() == "true",
    "debug_mode": os.getenv("TA_DEBUG", "false").lower() == "true",
}

def get_config() -> Dict[str, Any]:
    """Get a copy of the default configuration."""
    return DEFAULT_CONFIG.copy()

def update_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Update the default configuration with new values."""
    config = get_config()
    config.update(updates)
    return config
