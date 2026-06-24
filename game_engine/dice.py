"""
骰子工具 - DND 标准掷骰

支持格式: ndm+xx, ndm-xx, ndm
例如: 2d6+3, 1d20, 4d8-2, 3d6

额外: 优势/劣势 (advantage/disadvantage)
"""

import re
import random
from typing import Optional


class Dice:
    """DND 骰子"""

    DICE_PATTERN = re.compile(
        r"(\d+)?d(\d+)([+-]\d+)?$", re.IGNORECASE
    )

    @classmethod
    def roll(cls, formula: str, advantage: Optional[bool] = None) -> dict:
        """
        掷骰。

        Args:
            formula: 骰子公式 (如 "2d6+3", "1d20", "4d8-2")
            advantage: None=正常, True=优势(取高), False=劣势(取低)

        Returns:
            {
                "formula": str,
                "rolls": [int],        # 每个骰子的结果
                "modifier": int,       # 加值
                "total": int,          # 最终结果
                "advantage": str|null, # "advantage"/"disadvantage"/null
                "detail": str,         # 可读格式
            }
        """
        match = cls.DICE_PATTERN.match(formula.strip().replace(" ", ""))
        if not match:
            raise ValueError(
                f"Invalid dice formula: {formula!r}. "
                f"Expected format: ndm+xx (e.g., 2d6+3, 1d20)"
            )

        count = int(match.group(1)) if match.group(1) else 1
        sides = int(match.group(2))
        modifier = int(match.group(3)) if match.group(3) else 0

        if count < 1 or count > 100:
            raise ValueError(f"Dice count must be 1-100, got {count}")
        if sides < 2 or sides > 1000:
            raise ValueError(f"Dice sides must be 2-1000, got {sides}")

        # 掷骰
        if advantage is True and count == 1 and sides == 20:
            # 优势: 投两个 d20 取高
            rolls = [random.randint(1, sides), random.randint(1, sides)]
            base = max(rolls)
            adv_str = "advantage"
        elif advantage is False and count == 1 and sides == 20:
            # 劣势: 投两个 d20 取低
            rolls = [random.randint(1, sides), random.randint(1, sides)]
            base = min(rolls)
            adv_str = "disadvantage"
        else:
            rolls = [random.randint(1, sides) for _ in range(count)]
            base = sum(rolls)
            adv_str = None

        total = base + modifier
        mod_str = f"{modifier:+d}" if modifier else ""

        # 构建可读详情
        if advantage is True and count == 1 and sides == 20:
            detail = f"{formula} (优势: {rolls} → {base}{mod_str} = {total})"
        elif advantage is False and count == 1 and sides == 20:
            detail = f"{formula} (劣势: {rolls} → {base}{mod_str} = {total})"
        elif count == 1 and modifier == 0:
            detail = f"{formula} → [{rolls[0]}] = {total}"
        else:
            detail = f"{formula} → {rolls}{mod_str} = {total}"

        return {
            "formula": formula,
            "dice_count": count,
            "dice_sides": sides,
            "modifier": modifier,
            "rolls": rolls,
            "base": base,
            "total": total,
            "advantage": adv_str,
            "detail": detail,
        }

    @classmethod
    def ability_check(cls, modifier: int = 0, dc: Optional[int] = None) -> dict:
        """
        属性检定 (d20 + 调整值)。

        Args:
            modifier: 属性/熟练调整值
            dc: 难度等级 (可选，用于判断成功/失败)

        Returns:
            掷骰结果 + 成功/失败判断 (如果提供了 DC)
        """
        result = cls.roll(f"1d20{modifier:+d}" if modifier else "1d20")
        if dc is not None:
            result["dc"] = dc
            result["success"] = result["total"] >= dc
            result["detail"] += f" (DC {dc}: {'成功' if result['success'] else '失败'})"
        return result

    @classmethod
    def parse(cls, formula: str) -> dict:
        """仅解析公式，不掷骰。返回 {count, sides, modifier}"""
        match = cls.DICE_PATTERN.match(formula.strip().replace(" ", ""))
        if not match:
            raise ValueError(f"Invalid dice formula: {formula!r}")
        return {
            "count": int(match.group(1)) if match.group(1) else 1,
            "sides": int(match.group(2)),
            "modifier": int(match.group(3)) if match.group(3) else 0,
        }
