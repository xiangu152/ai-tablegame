"""
配置加载器 - 读取 config.yaml 中的 API 配置。

config.yaml 格式:
  api:
    base_url: https://api.deepseek.com/anthropic
    api_key: sk-xxx
    model: deepseek-v4-pro
"""

import os
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None


class Config:
    """API 配置数据类"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        config_path: Optional[str] = None,
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.config_path = config_path

    def __repr__(self) -> str:
        return (
            f"Config(base_url={self.base_url!r}, "
            f"model={self.model!r}, "
            f"config_path={self.config_path!r})"
        )

    @property
    def masked_key(self) -> str:
        """返回脱敏后的 API key"""
        if len(self.api_key) <= 8:
            return "*" * len(self.api_key)
        return self.api_key[:4] + "*" * (len(self.api_key) - 8) + self.api_key[-4:]


def load_config(config_path: str = "config.yaml") -> Config:
    """
    从 YAML 配置文件加载 API 配置。

    Args:
        config_path: config.yaml 文件路径，默认为项目根目录的 config.yaml

    Returns:
        Config 对象，包含 base_url, api_key, model

    Raises:
        FileNotFoundError: 配置文件不存在
        KeyError: 配置文件缺少必需字段
        ValueError: yaml 未安装或配置文件格式错误
    """
    if yaml is None:
        raise ImportError(
            "PyYAML is required to load config. Install with: pip install pyyaml"
        )

    path = Path(config_path)
    if not path.is_absolute():
        # 相对于项目根目录
        project_root = Path(__file__).parent.parent
        path = project_root / config_path

    if not path.exists():
        # 尝试从环境变量获取
        base_url = os.environ.get("DND_API_BASE_URL")
        api_key = os.environ.get("DND_API_KEY")
        model = os.environ.get("DND_MODEL")
        if base_url and api_key and model:
            return Config(
                base_url=base_url,
                api_key=api_key,
                model=model,
                config_path=None,
            )
        raise FileNotFoundError(
            f"Config file not found: {path}. "
            f"Also tried environment variables DND_API_BASE_URL, DND_API_KEY, DND_MODEL."
        )

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data or "api" not in data:
        raise KeyError(
            f"Config file {path} must contain an 'api' section with "
            f"base_url, api_key, and model fields."
        )

    api = data["api"]
    missing = [k for k in ("base_url", "api_key", "model") if k not in api]
    if missing:
        raise KeyError(
            f"Config file {path} missing required api fields: {', '.join(missing)}"
        )

    return Config(
        base_url=api["base_url"],
        api_key=api["api_key"],
        model=api["model"],
        config_path=str(path),
    )
