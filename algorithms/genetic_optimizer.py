#!/usr/bin/env python3
"""
基于遗传算法的队伍优化器
"""
import json
import random
import time
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from models.team_analyzer import TeamAnalyzer

DATA_DIR = Path(__file__).parent.parent.parent / "data"


class GeneticTeamOptimizer:
    def __init__(self, population_size: int = 100, generations: int = 50):
        self.team_analyzer = TeamAnalyzer()
        self.all_pet_ids = list(self.team_analyzer.pets.keys())
        self.population_size = population_size
        self.generations = generations

        from models import get_family_map, _filter_index_items
        self.family_map = get_family_map()  # {名称: noText}

        # 首领化精灵集合（typeClass == "boss"）
        self.boss_names = set(
            it["name"] for it in _filter_index_items()
            if it.get("typeClass") == "boss"
        )
        self.max_bosses = 1  # 队伍中最多允许1只首领化精灵

        # 遗传算法参数
        self.mutation_rate = 0.3
        self.crossover_rate = 0.7
        self.elitism_rate = 0.1  # 保留前10%的精英个体

    def _get_family(self, pet_id: str) -> str:
        """获取精灵所属家族编号，同一家族只能上一只"""
        return self.family_map.get(pet_id, pet_id)

    def _has_family_conflict(self, team: list[str], new_pet: str) -> bool:
        """检查新精灵是否与队伍中已有精灵属于同一家族"""
        new_family = self._get_family(new_pet)
        for p in team:
            if self._get_family(p) == new_family:
                return True
        return False

    def _count_bosses(self, team: list[str]) -> int:
        """统计队伍中首领化精灵数量"""
        return sum(1 for p in team if p in self.boss_names)

    def _is_valid_addition(self, team: list[str], new_pet: str) -> bool:
        """检查加入新精灵是否违反约束：家族不重复 + 首领不超过上限"""
        if self._has_family_conflict(team, new_pet):
            return False
        if new_pet in self.boss_names and self._count_bosses(team) >= self.max_bosses:
            return False
        return True

    def create_random_team(self) -> list[str]:
        """创建随机队伍，确保同一家族只上一只 + 最多1只首领"""
        team = []
        available = list(self.all_pet_ids)
        random.shuffle(available)
        for p in available:
            if self._is_valid_addition(team, p):
                team.append(p)
            if len(team) == 6:
                break
        return team

    def fitness(self, team: list[str]) -> float:
        """适应度函数 - 队伍综合评分"""
        result = self.team_analyzer.analyze_team(team)
        if "error" in result:
            return 0
        return result["team_score"]

    def mutate(self, team: list[str]) -> list[str]:
        """变异：随机替换1-2只宠物，避开同家族且不超首领上限"""
        new_team = team.copy()
        num_mutations = random.randint(1, 2)
        for _ in range(num_mutations):
            idx = random.randint(0, 5)
            candidates = [p for p in self.all_pet_ids
                          if p not in new_team and self._is_valid_addition(
                              [t for j, t in enumerate(new_team) if j != idx], p)]
            if candidates:
                new_team[idx] = random.choice(candidates)
        return new_team

    def crossover(self, team1: list[str], team2: list[str]) -> tuple[list[str], list[str]]:
        """交叉：交换两队的部分宠物，避开同家族且不超首领上限"""
        point = random.randint(1, 5)
        child1 = team1[:point] + [p for p in team2[point:] if p not in team1[:point]]
        child2 = team2[:point] + [p for p in team1[point:] if p not in team2[:point]]

        # 补全到6只，避开约束冲突
        for child in (child1, child2):
            # 先移除同家族冲突的精灵（保留靠前的）
            seen_families = set()
            clean_child = []
            for p in child:
                fam = self._get_family(p)
                if fam not in seen_families:
                    seen_families.add(fam)
                    clean_child.append(p)
            # 再检查首领上限
            final_child = []
            boss_count = 0
            for p in clean_child:
                if p in self.boss_names:
                    if boss_count >= self.max_bosses:
                        continue
                    boss_count += 1
                final_child.append(p)
            child[:] = final_child

            while len(child) < 6:
                candidates = [p for p in self.all_pet_ids
                              if p not in child and self._is_valid_addition(child, p)]
                if not candidates:
                    candidates = [p for p in self.all_pet_ids if p not in child]
                child.append(random.choice(candidates))

        return child1, child2

    def select_parent(self, population: list[list[str]], fitness_scores: list[float]) -> list[str]:
        """轮盘赌选择"""
        total_fitness = sum(fitness_scores)
        if total_fitness == 0:
            return random.choice(population)

        pick = random.uniform(0, total_fitness)
        current = 0
        for team, score in zip(population, fitness_scores):
            current += score
            if current > pick:
                return team
        return random.choice(population)

    def optimize(self) -> tuple[list[str], float, list[dict]]:
        """运行遗传算法优化"""
        print("=" * 70)
        print(f"遗传算法队伍优化 - 种群大小: {self.population_size}, 代数: {self.generations}")
        print("=" * 70)

        # 初始化种群
        population = [self.create_random_team() for _ in range(self.population_size)]
        history = []
        best_team = None
        best_score = 0

        start_time = time.time()

        for gen in range(self.generations):
            # 计算适应度
            fitness_scores = [self.fitness(team) for team in population]

            # 记录本代最佳
            gen_best_score = max(fitness_scores)
            gen_best_idx = fitness_scores.index(gen_best_score)
            gen_best_team = population[gen_best_idx]

            if gen_best_score > best_score:
                best_score = gen_best_score
                best_team = gen_best_team.copy()

            # 记录历史
            avg_score = sum(fitness_scores) / len(fitness_scores)
            history.append({
                "generation": gen + 1,
                "best_score": round(gen_best_score, 1),
                "avg_score": round(avg_score, 1),
                "best_team_names": [self.team_analyzer.pets[p]["name"] for p in gen_best_team]
            })

            if (gen + 1) % 5 == 0 or gen == 0:
                names = ", ".join(self.team_analyzer.pets[p]["name"] for p in gen_best_team[:3])
                print(f"  第{gen+1:3d}代 - 最佳: {gen_best_score:5.1f} | 平均: {avg_score:5.1f} | 领先: {names}...")

            # 生成下一代
            next_population = []

            # 精英保留
            elite_count = int(self.population_size * self.elitism_rate)
            elite_indices = sorted(range(len(fitness_scores)), key=lambda i: -fitness_scores[i])[:elite_count]
            for idx in elite_indices:
                next_population.append(population[idx].copy())

            # 填充剩余
            while len(next_population) < self.population_size:
                if random.random() < self.crossover_rate:
                    parent1 = self.select_parent(population, fitness_scores)
                    parent2 = self.select_parent(population, fitness_scores)
                    child1, child2 = self.crossover(parent1, parent2)
                    next_population.append(child1)
                    if len(next_population) < self.population_size:
                        next_population.append(child2)
                else:
                    parent = self.select_parent(population, fitness_scores)
                    next_population.append(parent.copy())

                # 变异
                if random.random() < self.mutation_rate and len(next_population) > 0:
                    next_population[-1] = self.mutate(next_population[-1])

            population = next_population

        elapsed = time.time() - start_time
        print(f"\n  ✓ 优化完成！用时: {elapsed:.1f}s")
        print(f"  ✓ 最佳队伍评分: {best_score:.1f}")

        return best_team, best_score, history


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))

    optimizer = GeneticTeamOptimizer(population_size=100, generations=50)
    best_team, best_score, history = optimizer.optimize()

    print("\n" + "=" * 70)
    print("最佳队伍分析")
    print("=" * 70)
    optimizer.team_analyzer.print_team_report(best_team)
