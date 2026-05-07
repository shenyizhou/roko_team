#!/usr/bin/env python3
"""
洛克王国世界队伍量化分析系统 - 主入口
"""
import json
from pathlib import Path
from models.pet_scorer import PetScorer
from models.team_analyzer import TeamAnalyzer
from algorithms.genetic_optimizer import GeneticTeamOptimizer


def print_pet_rankings(limit: int = 30):
    """打印宠物排行榜"""
    print("\n" + "=" * 80)
    print(f"宠物综合评分排行榜（Top {limit}）")
    print("=" * 80)

    scorer = PetScorer()
    rankings = scorer.get_all_rankings()

    print(f"{'排名':<4}{'名称':<12}{'属性':<14}{'总分':>8}{'种族':>8}{'属性':>8}{'特性':>8}{'技能':>8}{'速度':>8}")
    print("-" * 80)

    for i, pet in enumerate(rankings[:limit], 1):
        s = pet["scores"]
        attrs = "/".join(pet["attrs"])
        spd = pet["stats"]["spd"]
        print(f"{i:<4}{pet['name']:<12}{attrs:<14}{s['total']:>8.1f}{s['stats']:>8.1f}{s['type']:>8.1f}{s['feature']:>8.1f}{s['skill']:>8.1f}{spd:>8}")

    return rankings


def analyze_custom_team(pet_names: list[str]):
    """分析自定义队伍"""
    from models import get_family_map
    family_map = get_family_map()
    analyzer = TeamAnalyzer()

    # 通过名称找ID
    name_to_id = {pet["name"]: pet_id for pet_id, pet in analyzer.pets.items()}

    team_ids = []
    seen_families = set()
    for name in pet_names:
        if name in name_to_id:
            pid = name_to_id[name]
            fam = family_map.get(pid, pid)
            if fam in seen_families:
                # 同家族冲突：跳过或警告
                fam_members = [n for n, f in family_map.items() if f == fam and n in name_to_id]
                print(f"  警告: '{name}' 与队伍中已有精灵属于同一家族({fam})，跳过。家族成员: {fam_members}")
                continue
            seen_families.add(fam)
            team_ids.append(pid)
        else:
            # 模糊匹配
            matches = [n for n in name_to_id.keys() if name in n]
            if matches:
                pid = name_to_id[matches[0]]
                fam = family_map.get(pid, pid)
                if fam in seen_families:
                    print(f"  警告: '{matches[0]}' 与队伍中已有精灵属于同一家族，跳过")
                    continue
                seen_families.add(fam)
                team_ids.append(pid)
                print(f"  模糊匹配: {name} -> {matches[0]}")
            else:
                print(f"  警告: 找不到宠物 '{name}'")

    if len(team_ids) == 6:
        analyzer.print_team_report(team_ids)
    else:
        print(f"  错误: 需要6只宠物，当前只找到{len(team_ids)}只")


def recommend_best_team(population_size: int = 150, generations: int = 80):
    """推荐最优队伍"""
    print("\n" + "=" * 80)
    print("遗传算法队伍优化")
    print("=" * 80)

    optimizer = GeneticTeamOptimizer(population_size=population_size, generations=generations)
    best_team, best_score, history = optimizer.optimize()

    print("\n" + "=" * 80)
    print("【推荐队伍 - 综合分析】")
    print("=" * 80)
    result = optimizer.team_analyzer.print_team_report(best_team)

    return best_team, result


def main():
    print("=" * 80)
    print("洛克王国世界队伍量化分析系统")
    print("=" * 80)

    # 1. 宠物排行榜
    rankings = print_pet_rankings(limit=30)

    # 2. 用最高评分的6只组队（基准测试），考虑家族约束
    print("\n" + "=" * 80)
    print("【基准测试 - 评分最高的6只组队（同一家族只取一只）】")
    print("=" * 80)
    from models import get_family_map
    family_map = get_family_map()
    analyzer = TeamAnalyzer()

    top6_ids = []
    seen_families = set()
    for p in rankings:
        # 合并形态取第一个
        pid = p.get("merged_forms", [p.get("id")])[0] if "merged_forms" in p else p.get("id")
        if pid:
            fam = family_map.get(pid, pid)
            if fam not in seen_families and len(top6_ids) < 6:
                seen_families.add(fam)
                top6_ids.append(pid)
    analyzer.print_team_report(top6_ids)

    # 3. 遗传算法优化队伍
    best_team, result = recommend_best_team(population_size=150, generations=80)

    # 4. 再推荐2个备选队伍
    print("\n" + "=" * 80)
    print("【备选队伍推荐】")
    print("=" * 80)

    for i in range(2):
        print(f"\n--- 备选队伍 {i+1} ---")
        optimizer2 = GeneticTeamOptimizer(population_size=100, generations=50)
        team2, score2, _ = optimizer2.optimize()
        optimizer2.team_analyzer.print_team_report(team2)


if __name__ == "__main__":
    main()
