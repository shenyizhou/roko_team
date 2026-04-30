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

        # 遗传算法参数
        self.mutation_rate = 0.3
        self.crossover_rate = 0.7
        self.elitism_rate = 0.1  # 保留前10%的精英个体

    def create_random_team(self) -> list[str]:
        """创建随机队伍"""
        return random.sample(self.all_pet_ids, 6)

    def fitness(self, team: list[str]) -> float:
        """适应度函数 - 队伍综合评分"""
        result = self.team_analyzer.analyze_team(team)
        if "error" in result:
            return 0
        return result["team_score"]

    def mutate(self, team: list[str]) -> list[str]:
        """变异：随机替换1-2只宠物"""
        new_team = team.copy()
        num_mutations = random.randint(1, 2)
        for _ in range(num_mutations):
            idx = random.randint(0, 5)
            new_pet = random.choice(self.all_pet_ids)
            while new_pet in new_team:
                new_pet = random.choice(self.all_pet_ids)
            new_team[idx] = new_pet
        return new_team

    def crossover(self, team1: list[str], team2: list[str]) -> tuple[list[str], list[str]]:
        """交叉：交换两队的部分宠物"""
        # 单点交叉
        point = random.randint(1, 5)
        child1 = team1[:point] + [p for p in team2[point:] if p not in team1[:point]]
        child2 = team2[:point] + [p for p in team1[point:] if p not in team2[:point]]

        # 补全到6只
        while len(child1) < 6:
            p = random.choice(self.all_pet_ids)
            if p not in child1:
                child1.append(p)
        while len(child2) < 6:
            p = random.choice(self.all_pet_ids)
            if p not in child2:
                child2.append(p)

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
