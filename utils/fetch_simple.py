#!/usr/bin/env python3
"""
简化版数据抓取脚本
"""
import requests
import json
import time
from pathlib import Path

BASE_URL = "https://www.rocoworldwiki.com"
DATA_DIR = Path(__file__).parent.parent / "data"

headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}


def main():
    print("=" * 60)
    print("开始抓取数据...")
    print("=" * 60)

    # 1. 加载宠物索引
    print("\n1. 加载宠物索引...")
    with open(DATA_DIR / "spirit_filter_index.json") as f:
        spirit_index = json.load(f)

    # 筛选final和region形态
    target_pets = [
        p for p in spirit_index["items"]
        if p.get("typeClass") in ["final", "region"] and p.get("detailFile")
    ]
    print(f"   目标宠物：{len(target_pets)} 只（final+region）")

    # 2. 批量抓取
    print("\n2. 抓取宠物详情...")
    spirits_data = {}
    start = time.time()

    for i, pet in enumerate(target_pets, 1):
        try:
            url = f"{BASE_URL}/data/spirits/{pet['detailFile']}"
            r = requests.get(url, headers=headers, timeout=10)
            r.raise_for_status()
            spirits_data[pet["noText"]] = r.json()
        except Exception as e:
            print(f"   ✗ {pet['name']} ({pet['noText']}): {e}")

        if i % 20 == 0:
            elapsed = time.time() - start
            print(f"   进度: {i}/{len(target_pets)} ({i/len(target_pets)*100:.1f}%) - 用时: {elapsed:.1f}s")
            time.sleep(1)  # 避免请求过快

    print(f"\n   ✓ 成功抓取: {len(spirits_data)} 只")

    # 3. 保存
    with open(DATA_DIR / "spirits_detail.json", "w", encoding="utf-8") as f:
        json.dump(spirits_data, f, ensure_ascii=False, indent=2)
    print(f"   ✓ 已保存到 spirits_detail.json")

    # 4. 构建最终宠物数据库
    print("\n3. 构建最终宠物数据库...")
    pet_base_info = {p["noText"]: p for p in spirit_index["items"]}

    final_pets = {}
    for pet_id, detail in spirits_data.items():
        base_info = pet_base_info.get(pet_id, {})
        final_pets[pet_id] = {
            "id": pet_id,
            "name": detail.get("name", ""),
            "type_class": base_info.get("typeClass", ""),
            "attrs": base_info.get("attrs", []),
            "stats": {
                "total": base_info.get("race", 0),
                "hp": base_info.get("hp", 0),
                "atk": base_info.get("atk", 0),
                "matk": base_info.get("matk", 0),
                "def": base_info.get("def", 0),
                "mdef": base_info.get("mdef", 0),
                "spd": base_info.get("spd", 0),
            },
            "trait": {
                "name": base_info.get("traitName", ""),
                "desc": base_info.get("traitDesc", ""),
            },
            "skills": {
                "learnset": detail.get("skills", {}).get("learnset", []),
                "recommended": detail.get("skills", {}).get("recommended", []),
                "other": detail.get("skills", {}).get("other", []),
            },
            "skill_count": base_info.get("skillCount", 0),
        }

    with open(DATA_DIR / "pets_final.json", "w", encoding="utf-8") as f:
        json.dump(final_pets, f, ensure_ascii=False, indent=2)

    print(f"   ✓ 最终宠物数: {len(final_pets)} 只")
    print(f"   ✓ 已保存到 pets_final.json")

    # 按种族值排序输出前10
    sorted_pets = sorted(final_pets.values(), key=lambda x: -x["stats"]["total"])
    print("\n   种族值前10:")
    for p in sorted_pets[:10]:
        skills = p['skills']['learnset']
        avg_cost = sum(s.get('cost', 0) for s in skills) / max(len(skills), 1) if skills else 0
        print(f"     {p['name']:10} ({p['id']:7}) - 种族值: {p['stats']['total']} - 技能数: {len(skills)} - 平均能耗: {avg_cost:.1f}")

    print("\n" + "=" * 60)
    print(f"✓ 完成！总用时: {time.time() - start:.1f}s")
    print("=" * 60)


if __name__ == "__main__":
    main()
