"""
文件级检查点机制 - 每处理完一个文件保存中间结果，崩溃后可恢复。

存储位置: dm_memory/{game_name}/.checkpoints/
每个步骤保存为一个 JSON 文件，包含时间戳和哈希校验。
"""

import json
import hashlib
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Any

logger = logging.getLogger(__name__)


class Checkpoint:
    """
    文件级检查点管理器。

    Usage:
        cp = Checkpoint("my_game")
        cp.save("pdf_extraction", pdf_data)
        if cp.is_valid("pdf_extraction"):
            pdf_data = cp.load("pdf_extraction")
    """

    def __init__(self, game_name: str, base_dir: str = "dm_memory"):
        self.game_name = game_name
        self.base_dir = Path(base_dir)
        self.checkpoints_dir = self.base_dir / game_name / ".checkpoints"
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, step_name: str) -> Path:
        """获取检查点文件路径"""
        safe_name = step_name.replace("/", "_").replace(" ", "_")
        return self.checkpoints_dir / f"{safe_name}.json"

    def save(self, step_name: str, data: Any) -> str:
        """
        保存检查点。

        Args:
            step_name: 步骤名称（如 "pdf_extraction", "rulebook_玩家手册"）
            data: 要保存的数据（必须可 JSON 序列化）

        Returns:
            检查点文件路径
        """
        path = self._path(step_name)
        serialized = json.dumps(data, ensure_ascii=False, indent=2)
        checksum = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]

        checkpoint = {
            "step": step_name,
            "game_name": self.game_name,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "checksum": checksum,
            "data": data,
        }

        path.write_text(
            json.dumps(checkpoint, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Checkpoint saved: %s (checksum=%s)", step_name, checksum)
        return str(path)

    def load(self, step_name: str) -> Optional[Any]:
        """
        加载检查点。

        Args:
            step_name: 步骤名称

        Returns:
            保存的数据，如果检查点不存在或损坏则返回 None
        """
        path = self._path(step_name)
        if not path.exists():
            return None

        try:
            checkpoint = json.loads(path.read_text(encoding="utf-8"))
            return checkpoint.get("data")
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Checkpoint corrupt: %s (%s)", step_name, e)
            return None

    def is_valid(self, step_name: str) -> bool:
        """
        验证检查点是否存在且数据完整。

        Args:
            step_name: 步骤名称

        Returns:
            True 如果检查点有效
        """
        path = self._path(step_name)
        if not path.exists():
            return False

        try:
            checkpoint = json.loads(path.read_text(encoding="utf-8"))
            if "data" not in checkpoint:
                return False
            # 验证 checksum
            stored_checksum = checkpoint.get("checksum")
            if stored_checksum:
                data_json = json.dumps(
                    checkpoint["data"], ensure_ascii=False, indent=2
                )
                current = hashlib.sha256(data_json.encode("utf-8")).hexdigest()[:16]
                return current == stored_checksum
            return True
        except Exception:
            return False

    def list_steps(self) -> list[dict]:
        """
        列出所有已完成的步骤。

        Returns:
            [{"step": "step_name", "saved_at": "ISO8601", "checksum": "xxx"}, ...]
        """
        steps = []
        for path in sorted(self.checkpoints_dir.glob("*.json")):
            try:
                cp = json.loads(path.read_text(encoding="utf-8"))
                steps.append({
                    "step": cp.get("step", path.stem),
                    "saved_at": cp.get("saved_at", "unknown"),
                    "checksum": cp.get("checksum", "unknown"),
                })
            except Exception:
                steps.append({
                    "step": path.stem,
                    "saved_at": "unknown",
                    "checksum": "corrupt",
                })
        return steps

    def clear(self, step_name: Optional[str] = None):
        """
        清除检查点。

        Args:
            step_name: 要清除的步骤（None 则清除全部）
        """
        if step_name:
            path = self._path(step_name)
            if path.exists():
                path.unlink()
                logger.info("Cleared checkpoint: %s", step_name)
        else:
            for path in self.checkpoints_dir.glob("*.json"):
                path.unlink()
            logger.info("Cleared all checkpoints for game: %s", self.game_name)

    def is_step_done(self, step_name: str) -> bool:
        """检查某步骤是否已完成（有有效检查点）"""
        return self.is_valid(step_name)
