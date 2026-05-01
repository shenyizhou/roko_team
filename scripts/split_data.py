#!/usr/bin/env python3
"""
数据文件拆分/清理

1. 移除所有技能中的 accuracy 和 pp 字段
2. 拆分为独立文件:
   - data/pets.json         宠物基础数据 (不含技能)
   - data/skills_library.json 技能库 (去除 accuracy/pp)
   - data/pet_learnset.json  精灵可学技能映射 {pet_id: [skill_id, ...]}
   - data/pet_recommended.json 精灵推荐技能配置 {pet_id: [skill_id, ...]}
"""

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
SKIP_FIELDS = {"accuracy", "pp"}


def clean_skill(sk):
    """移除技能中的无效字段"""
    return {k: v for k, v in sk.items() if k not in SKIP_FIELDS}


def main():
    # 加载原数据
    with open(DATA_DIR / "pets_final.json") as f:
        pets_old = json.load(f)

    with open(DATA_DIR / "skills_library.json") as f:
        skills_lib = json.load(f)

    # 1. 清理 skills_library.json
    cleaned_lib = {}
    for kid, sk in skills_lib.items():
        cleaned_lib[kid] = clean_skill(sk)

    with open(DATA_DIR / "skills_library.json", "w") as f:
        json.dump(cleaned_lib, f, ensure_ascii=False, indent=2)
    print(f"技能库已清理: {len(cleaned_lib)} 个技能 (移除 {SKIP_FIELDS})")

    # 2. 拆分宠物数据
    pets_new = {}
    learnset_map = {}
    recommended_map = {}

    for pid, pet in pets_old.items():
        # 基础宠物数据(去技能块)
        pet_base = {k: v for k, v in pet.items() if k != "skills"}
        pets_new[pid] = pet_base

        skills_block = pet.get("skills", {})

        # 可学技能 → pet_learnset.json
        learnset = skills_block.get("learnset", [])
        if learnset:
            cleaned = []
            for sk in learnset:
                if isinstance(sk, dict):
                    cleaned.append(clean_skill(sk))
            learnset_map[pid] = cleaned

        # 推荐技能 → pet_recommended.json
        recommended = skills_block.get("recommended", [])
        if recommended:
            cleaned = []
            for sk in recommended:
                if isinstance(sk, dict):
                    cleaned.append(clean_skill(sk))
            recommended_map[pid] = cleaned

    # 保存各文件
    with open(DATA_DIR / "pets.json", "w") as f:
        json.dump(pets_new, f, ensure_ascii=False, indent=2)
    print(f"宠物数据: {len(pets_new)} 个 → pets.json")

    with open(DATA_DIR / "pet_learnset.json", "w") as f:
        json.dump(learnset_map, f, ensure_ascii=False, indent=2)
    print(f"可学技能: {len(learnset_map)} 个精灵 → pet_learnset.json")

    with open(DATA_DIR / "pet_recommended.json", "w") as f:
        json.dump(recommended_map, f, ensure_ascii=False, indent=2)
    print(f"推荐技能: {len(recommended_map)} 个精灵 → pet_recommended.json")


if __name__ == "__main__":
    main()
