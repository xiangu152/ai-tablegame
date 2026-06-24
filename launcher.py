#!/usr/bin/env python3
"""
游戏启动器 — 选择剧本，启动 DM 实例。

用法:
    python3 launcher.py                     # 交互式选择
    python3 launcher.py --list              # 列出可用剧本
    python3 launcher.py <剧本编号>           # 直接启动

剧本来自 dnd_data/game_book/ 下的 PDF 文件。
"""

import sys
from pathlib import Path

GAME_BOOK_DIR = Path("dnd_data/game_book")
RULEBOOK_DIR = Path("dnd_data/rule_book/markdown")


def list_adventures() -> list[dict]:
    """扫描可用剧本"""
    if not GAME_BOOK_DIR.exists():
        return []

    adventures = []
    for pdf in sorted(GAME_BOOK_DIR.glob("*.pdf")):
        # 从文件名提取剧本名
        name = pdf.stem
        # 去掉前缀 "龙与地下城 5eDnD_" 和 后缀版本号
        name = name.replace("龙与地下城 5eDnD_", "")
        adventures.append({
            "index": len(adventures) + 1,
            "name": name,
            "file": pdf.name,
            "path": str(pdf),
            "size_mb": pdf.stat().st_size / (1024 * 1024),
        })
    return adventures


def launch(game_name: str, pdf_path: str):
    """启动 DM 实例"""
    import subprocess

    print(f"""
╔══════════════════════════════════════════════╗
║          🐉 DND AI 游戏启动                   ║
╠══════════════════════════════════════════════╣
║  剧本: {game_name:<35s} ║
║  PDF:  {pdf_path[:35]:<35s} ║
╚══════════════════════════════════════════════╝

DM 实例启动后会自动:
  1. 阅读团本和规则书
  2. 创建角色卡
  3. 输出玩家启动指令
  4. 开始游戏

在 Cloade Code 中让 DM 实例读取 agent_roles/DM.md 并执行备团协议。
    """)

    # 写入本次游戏配置
    config = {
        "game_name": game_name,
        "pdf_path": pdf_path,
        "rulebook_path": str(RULEBOOK_DIR),
    }
    Path(".game_config.json").write_text(
        __import__("json").dumps(config, ensure_ascii=False, indent=2)
    )

    # 给 Claude Code 实例的启动指令
    print(f"""
在 Claude Code 中执行:

    python3 run_dm.py {game_name} "{pdf_path}"

DM 将自主完成全部备团。
""")


def main():
    adventures = list_adventures()

    if not adventures:
        print("❌ 没有找到剧本 PDF。")
        print(f"   请将 DND 团本 PDF 放入 {GAME_BOOK_DIR.resolve()}")
        sys.exit(1)

    if "--list" in sys.argv:
        print("可用剧本:\n")
        for adv in adventures:
            print(f"  [{adv['index']}] {adv['name']} ({adv['size_mb']:.0f} MB)")
        return

    # 选择剧本
    choice = None
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        choice = int(sys.argv[1])

    if choice and 1 <= choice <= len(adventures):
        adv = adventures[choice - 1]
    elif len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        # 按名称搜索
        query = sys.argv[1]
        matches = [a for a in adventures if query.lower() in a["name"].lower()]
        if matches:
            adv = matches[0]
        else:
            print(f"❌ 未找到匹配 '{query}' 的剧本")
            list_adventures()
            sys.exit(1)
    else:
        print("选择剧本:\n")
        for adv in adventures:
            print(f"  [{adv['index']}] {adv['name']} ({adv['size_mb']:.0f} MB)")
        print()
        try:
            choice = int(input("输入编号: ").strip())
            adv = adventures[choice - 1]
        except (ValueError, IndexError, KeyboardInterrupt):
            print("取消")
            sys.exit(1)

    # 生成游戏名
    game_name = adv["name"].replace(" ", "_").replace("：", "_")[:50]
    launch(game_name, adv["path"])


if __name__ == "__main__":
    main()
