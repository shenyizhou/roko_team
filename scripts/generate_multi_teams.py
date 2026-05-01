#!/usr/bin/env python3
"""
多体系最优队伍生成器
围绕每个战术体系 + 全局最优，生成多支推荐队伍
"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.team_analyzer import TeamAnalyzer
from models.build_analyzer import BuildAnalyzer
from algorithms.genetic_optimizer import GeneticTeamOptimizer

DATA_DIR = Path(__file__).parent.parent.parent / "data"


def main():
    print("=" * 80)
    print("  洛克王国世界 - 多体系最优队伍生成器")
    print("=" * 80)

    ta = TeamAnalyzer()
    ba = BuildAnalyzer()

    all_teams = {}

    # 1. 全局遗传算法最优队伍
    print("\n【1】全局遗传算法搜索最优队伍...")
    optimizer = GeneticTeamOptimizer(population_size=80, generations=30)
    best_team_ids, best_score, history = optimizer.optimize()
    best_team_names = [ta.pets[p]["name"] for p in best_team_ids]

    build = ba.full_analysis(best_team_ids)
    all_teams["全局最优"] = {
        "members": best_team_names,
        "total_score": best_score,
        "build_score": build["build_quality_score"],
        "style": build["build_style"]["primary_style"],
        "hot_coverage": build["hot_pet_coverage"]["summary"],
        "red_threats": [t["name"] for t in build["hot_pet_coverage"].get("red_threats", [])],
    }
    print(f"  全局最优: {', '.join(best_team_names)}")
    print(f"  综合评分: {best_score:.1f} | 构筑: {build['build_quality_score']} | {build['hot_pet_coverage']['summary']}")

    # 2. 按体系生成
    print("\n【2】按战术体系生成最优队伍...")
    per_system = ba.generate_per_system_teams(top_k=2)
    for sys_name, teams in per_system.items():
        if not teams:
            continue
        best = teams[0]
        all_teams[sys_name] = {
            "members": best["members"],
            "total_score": best.get("score", 0),
            "build_score": best.get("score", 0),
            "style": best["analysis"]["build_style"]["primary_style"],
            "hot_coverage": best["analysis"]["hot_pet_coverage"]["summary"],
            "red_threats": [t["name"] for t in best["analysis"]["hot_pet_coverage"].get("red_threats", [])],
        }
        print(f"  {sys_name}: {', '.join(best['members'][:4])}...")
        print(f"    构筑: {best.get('score', 0)} | {best['analysis']['hot_pet_coverage']['summary']}")

    # 3. 保存所有队伍
    output = {
        "generated_at": "2026-05-01",
        "teams": all_teams,
        "rankings": [
            {
                "rank": i + 1,
                "name": name,
                "members": t["members"],
                "build_score": t["build_score"],
                "hot_coverage": t["hot_coverage"],
            }
            for i, (name, t) in enumerate(
                sorted(all_teams.items(), key=lambda x: -x[1]["build_score"])
            )
        ],
    }

    out_path = DATA_DIR / "optimal_teams_multi.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # 总结
    print(f"\n{'='*80}")
    print("生成完成！队伍排名（按构筑质量）:")
    print(f"{'='*80}")
    for i, item in enumerate(output["rankings"]):
        members = ", ".join(item["members"])
        print(f"  {item['rank']}. [{item['name']}] 构筑:{item['build_score']}")
        print(f"     成员: {members}")
        print(f"     PVP: {item['hot_coverage']}")

    print(f"\n结果已保存到: {out_path}")


if __name__ == "__main__":
    main()
