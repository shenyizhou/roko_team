#!/usr/bin/env python3
"""
抓取洛克王国世界所有数据
抓取final形态124只 + region形态132只 = 共256只
"""
import requests
import json
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_URL = "https://www.rocoworldwiki.com"
DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}


def fetch_json(url_path: str) -> dict:
    """抓取JSON数据"""
    url = f"{BASE_URL}{url_path}"
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def save_json(data: dict | list, filename: str):
    """保存JSON数据"""
    with open(DATA_DIR / filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fetch_attribute_chart():
    """抓取属性克制表"""
    print("=" * 60)
    print("1. 抓取属性克制表...")
    attr_chart = fetch_json("/data/attribute-chart.json")
    save_json(attr_chart, "attribute_chart.json")
    print(f"   ✓ 保存成功：{len(attr_chart['attributes'])} 种属性")
    return attr_chart


def fetch_spirit_index():
    """抓取宠物索引（含种族值）"""
    print("\n" + "=" * 60)
    print("2. 抓取宠物索引...")
    spirit_index = fetch_json("/data/spirit-filter-index.json")
    save_json(spirit_index, "spirit_filter_index.json")
    print(f"   ✓ 保存成功：{len(spirit_index['items'])} 只宠物")

    # 分类统计
    by_class = {}
    for pet in spirit_index["items"]:
        tc = pet.get("typeClass", "unknown")
        by_class[tc] = by_class.get(tc, 0) + 1

    print("   形态分布:")
    for tc, cnt in sorted(by_class.items(), key=lambda x: -x[1]):
        print(f"     - {tc}: {cnt} 只")

    return spirit_index


def fetch_one_spirit_detail(pet: dict):
    """抓取单只宠物详情"""
    detail_file = pet.get("detailFile")
    if not detail_file:
        return None

    try:
        detail = fetch_json(f"/data/spirits/{detail_file}")
        return pet["noText"], detail
    except Exception as e:
        print(f"   ✗ {pet['name']} ({pet['noText']}): {e}")
        return None


def fetch_spirit_details(spirit_index: dict):
    """批量抓取宠物详情"""
    print("\n" + "=" * 60)
    print("3. 批量抓取宠物详情...")

    # 筛选final和region形态
    target_pets = [
        p for p in spirit_index["items"]
        if p.get("typeClass") in ["final", "region"] and p.get("detailFile")
    ]
    print(f"   目标宠物：{len(target_pets)} 只（final+region）")

    # 并行抓取
    spirits_data = {}
    failed = []
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_one_spirit_detail, pet) for pet in target_pets]

        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            if result:
                pet_id, detail = result
                spirits_data[pet_id] = detail
            if i % 20 == 0:
                elapsed = time.time() - start_time
                print(f"   进度: {i}/{len(target_pets)} ({i/len(target_pets)*100:.1f}%) - 用时: {elapsed:.1f}s")

    print(f"\n   ✓ 抓取成功: {len(spirits_data)} 只")
    if failed:
        print(f"   ✗ 失败: {len(failed)} 只")

    # 保存
    save_json(spirits_data, "spirits_detail.json")

    return spirits_data


def build_skill_library(spirits_data: dict):
    """从所有宠物技能中构建全局技能库"""
    print("\n" + "=" * 60)
    print("4. 构建技能库...")

    all_skills = {}
    for pet_id, detail in spirits_data.items():
        if "skills" not in detail:
            continue

        for skill_type in ["learnset", "recommended", "other"]:
            if skill_type in detail["skills"]:
                for skill in detail["skills"][skill_type]:
                    skill_id = skill.get("id")
                    if skill_id and skill_id not in all_skills:
                        all_skills[skill_id] = skill

    print(f"   ✓ 技能总数: {len(all_skills)} 个")

    # 统计技能类型
    elements = {}
    categories = {}
    for skill in all_skills.values():
        elem = skill.get("element", "未知")
        cat = skill.get("category", "未知")
        elements[elem] = elements.get(elem, 0) + 1
        categories[cat] = categories.get(cat, 0) + 1

    print(f"   属性分布: {dict(sorted(elements.items(), key=lambda x: -x[1]))}")
    print(f"   类型分布: {categories}")

    # 按能耗统计
    cost_dist = {}
    for skill in all_skills.values():
        cost = skill.get("cost", 0)
        cost_dist[cost] = cost_dist.get(cost, 0) + 1
    print(f"   能耗分布: {dict(sorted(cost_dist.items()))}")

    save_json(all_skills, "skills_library.json")
    return all_skills


def build_final_pets_db(spirit_index: dict, spirits_data: dict):
    """构建最终的宠物数据库"""
    print("\n" + "=" * 60)
    print("5. 构建最终宠物数据库...")

    # 建立ID到基础信息的映射
    pet_base_info = {p["noText"]: p for p in spirit_index["items"]}

    final_pets = {}
    for pet_id, detail in spirits_data.items():
        base_info = pet_base_info.get(pet_id, {})

        pet = {
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
        final_pets[pet_id] = pet

    print(f"   ✓ 宠物总数: {len(final_pets)} 只")

    # 按种族值排序输出前10
    sorted_pets = sorted(final_pets.values(), key=lambda x: -x["stats"]["total"])
    print("\n   种族值前10:")
    for p in sorted_pets[:10]:
        skills = p['skills']['learnset']
        avg_cost = sum(s.get('cost', 0) for s in skills) / max(len(skills), 1) if skills else 0
        print(f"     {p['name']:10} ({p['id']:7}) - 种族值: {p['stats']['total']} - 技能数: {len(skills)} - 平均能耗: {avg_cost:.1f}")

    save_json(final_pets, "pets_final.json")
    return final_pets


def main():
    total_start = time.time()

    # 1. 属性克制表
    fetch_attribute_chart()

    # 2. 宠物索引
    spirit_index = fetch_spirit_index()

    # 3. 宠物详情
    spirits_data = fetch_spirit_details(spirit_index)

    # 4. 技能库
    build_skill_library(spirits_data)

    # 5. 最终宠物数据库
    build_final_pets_db(spirit_index, spirits_data)

    print("\n" + "=" * 60)
    print(f"✓ 所有数据抓取完成！总用时: {time.time() - total_start:.1f}s")
    print(f"✓ 数据文件: {DATA_DIR.absolute()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
