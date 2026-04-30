#!/usr/bin/env python3
"""
解析洛克王国世界Excel数据，转化为结构化JSON
"""
import pandas as pd
import json
import re
from pathlib import Path

EXCEL_PATH = Path(__file__).parent.parent / "洛克王国世界种族值.xlsx"
DATA_DIR = Path(__file__).parent.parent / "data"


def parse_pets():
    """解析宠物数据"""
    df = pd.read_excel(EXCEL_PATH, sheet_name="种族值")

    pets = {}
    for _, row in df.iterrows():
        pet_id = str(row["宠物编号"]).strip()

        # 解析属性（可能是"龙/幽"这种格式）
        type_str = str(row["属性"]) if pd.notna(row["属性"]) else ""
        types = [t.strip() for t in type_str.split("/") if t.strip()]

        # 特性文本
        feature = str(row["特性"]) if pd.notna(row["特性"]) else ""

        pet = {
            "id": pet_id,
            "name": str(row["宠物名称"]).strip(),
            "types": types,
            "stats": {
                "hp": int(row["生命"]) if pd.notna(row["生命"]) else 0,
                "atk": int(row["物攻"]) if pd.notna(row["物攻"]) else 0,
                "def": int(row["物防"]) if pd.notna(row["物防"]) else 0,
                "sp_atk": int(row["魔攻"]) if pd.notna(row["魔攻"]) else 0,
                "sp_def": int(row["魔防"]) if pd.notna(row["魔防"]) else 0,
                "spd": int(row["速度"]) if pd.notna(row["速度"]) else 0,
                "total": int(row["种族总和"]) if pd.notna(row["种族总和"]) else 0,
            },
            "feature": feature,
            "is_final": str(row["最终形态"]).strip() == "是",
            "rank": float(row["强度排序"]) if pd.notna(row["强度排序"]) else None,
            "wiki_url": str(row["内页地址"]) if pd.notna(row["内页地址"]) else "",
        }

        # 优先选择最终形态的宠物（如果有重名）
        if pet["name"] not in pets or pet["is_final"]:
            pets[pet["name"]] = pet

    # 按ID为key重新组织
    pets_by_id = {p["id"]: p for p in pets.values()}

    print(f"解析完成: {len(pets_by_id)} 只宠物")
    return pets_by_id


def parse_type_chart():
    """解析属性克制表"""
    df = pd.read_excel(EXCEL_PATH, sheet_name="属性")

    # 提取属性名称（第一列）
    types = [str(t).strip() for t in df["Unnamed: 0"].tolist()]

    # 构建克制矩阵: type_chart[attack_type][defense_type] = multiplier
    type_chart = {}
    for i, atk_type in enumerate(types):
        type_chart[atk_type] = {}
        for def_type in types:
            multiplier = float(df.iloc[i][def_type])
            type_chart[atk_type][def_type] = multiplier

    # 计算每种属性的攻防评分
    type_scores = {}
    for t in types:
        atk_score = float(df.iloc[types.index(t)]["攻"])
        def_score = float(df.iloc[types.index(t)]["防"])
        total_score = float(df.iloc[types.index(t)]["总"])
        type_scores[t] = {
            "attack": atk_score,
            "defense": def_score,
            "total": total_score
        }

    print(f"解析完成: {len(types)} 种属性")
    return {
        "types": types,
        "chart": type_chart,
        "scores": type_scores
    }


def parse_speed_lines():
    """解析速度线数据"""
    df = pd.read_excel(EXCEL_PATH, sheet_name="速度线")

    speed_lines = []
    for _, row in df.iterrows():
        line = {
            "race_value": str(row["种族值"]).strip(),
            "speed_value": int(row["速度值"]) if pd.notna(row["速度值"]) else 0,
            "pets": str(row["精灵"]).strip() if pd.notna(row["精灵"]) else ""
        }
        speed_lines.append(line)

    # 按速度从高到低排序
    speed_lines.sort(key=lambda x: -x["speed_value"])

    print(f"解析完成: {len(speed_lines)} 档速度线")
    return speed_lines


def main():
    """主函数：解析所有数据并保存为JSON"""
    DATA_DIR.mkdir(exist_ok=True)

    print("=" * 50)
    print("开始解析洛克王国世界数据...")
    print("=" * 50)

    # 解析宠物数据
    print("\n[1/3] 解析宠物数据...")
    pets = parse_pets()
    with open(DATA_DIR / "pets.json", "w", encoding="utf-8") as f:
        json.dump(pets, f, ensure_ascii=False, indent=2)

    # 解析属性克制表
    print("\n[2/3] 解析属性克制表...")
    type_data = parse_type_chart()
    with open(DATA_DIR / "type_chart.json", "w", encoding="utf-8") as f:
        json.dump(type_data, f, ensure_ascii=False, indent=2)

    # 解析速度线
    print("\n[3/3] 解析速度线...")
    speed_lines = parse_speed_lines()
    with open(DATA_DIR / "speed_lines.json", "w", encoding="utf-8") as f:
        json.dump(speed_lines, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 50)
    print("所有数据解析完成！")
    print(f"输出目录: {DATA_DIR.absolute()}")
    print("=" * 50)


if __name__ == "__main__":
    main()
