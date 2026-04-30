#!/usr/bin/env python3
"""
属性联防矩阵计算
基于洛克王国世界18属性体系（光系引入，石系并入地系）
矩阵数据来自 洛克王国世界种族值.xlsx 属性tab
"""
import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"

# 游戏18属性标准名
GAME_ATTRS = ["火", "水", "草", "光", "恶魔", "幽灵", "一般", "地", "冰", "龙",
              "电", "毒", "虫", "武", "翼", "萌", "机械", "幻"]


class AttributeMatrix:
    def __init__(self):
        with open(DATA_DIR / "attribute_chart.json", encoding="utf-8") as f:
            self.attr_data = json.load(f)

        self.attr_names = [a["nameCn"] for a in self.attr_data["attributes"]]

        # 宠物数据属性名 → 游戏标准名
        self.short_to_full = {
            "幽": "幽灵",
            "恶": "恶魔",
            "普通": "一般",
            "地": "地",
        }
        for name in self.attr_names:
            self.short_to_full[name] = name

        # 构建倍率矩阵
        self.multipliers = {atk: {def_: 1.0 for def_ in self.attr_names}
                           for atk in self.attr_names}

        for atk in self.attr_data["attributes"]:
            atk_name = atk["nameCn"]
            offense = atk["battleMultiplier"]["offense"]
            for mult_str, def_keys in offense.items():
                for def_key in def_keys:
                    def_name = def_key  # key直接就是标准名
                    self.multipliers[atk_name][def_name] = float(mult_str)

        # 使用游戏内置攻防评分
        self.gamescores = {a["nameCn"]: a["game_scores"] for a in self.attr_data["attributes"]}

        # 缓存进攻/防守面统计
        self.type_offense_count = {}
        self.type_defense_data = {}

        for attr in self.attr_names:
            # 进攻：统计克制面和不利面
            off_weak = []   # 能克制的属性
            off_resist = [] # 打不动的属性
            for def_a in self.attr_names:
                m = self.multipliers[attr][def_a]
                if m >= 2: off_weak.append(def_a)
                elif m == 0: off_resist.append(def_a)
            self.type_offense_count[attr] = len(off_weak)

            # 防守：统计谁克制我、谁打我抵抗
            def_weak = []    # 被谁克制 (2x)
            def_resist = []  # 抵抗谁 (0x, 游戏统一归为抵抗)
            for atk_a in self.attr_names:
                m = self.multipliers[atk_a][attr]
                if m >= 2: def_weak.append(atk_a)
                elif m == 0: def_resist.append(atk_a)
            self.type_defense_data[attr] = {
                "weak_to": def_weak,
                "resist_from": def_resist,
                "weak_count": len(def_weak),
                "resist_count": len(def_resist),
            }

    def _normalize_attr(self, attr: str) -> str:
        return self.short_to_full.get(attr, attr)

    def get_multiplier(self, atk_attr: str, def_attr: str) -> float:
        atk = self._normalize_attr(atk_attr)
        def_ = self._normalize_attr(def_attr)
        return self.multipliers.get(atk, {}).get(def_, 1.0)

    def get_type_score(self, attrs: list[str]) -> dict:
        """
        计算宠物属性的综合评分。
        双属性时进攻取各自最大覆盖，防守聚合计算（取较优抵抗）。
        返回游戏内置攻/防评分，按比例缩放。
        """
        normalized = [self._normalize_attr(a) for a in attrs]

        # 进攻：取各属性的最大克制面
        offense = 0
        for a in normalized:
            if a in self.type_offense_count:
                offense = max(offense, self.type_offense_count[a])

        # 防守：联合覆盖（双属性取并集）
        resisted = set()
        weakened = set()

        for a in normalized:
            d = self.type_defense_data.get(a, {})
            for atk_a in d.get("resist_from", []):
                resisted.add(atk_a)
            for atk_a in d.get("weak_to", []):
                weakened.add(atk_a)

        weak_count = len(weakened)

        # 使用游戏内置评分作为基准（缩放）
        # 游戏最优属性: 机械(攻-1,防7,总6)，最差: 虫(攻-4,防0,总-4)
        # 双属性时取加权平均
        game_off = sum(self.gamescores.get(a, {}).get("offense", 0) for a in normalized) / len(normalized)
        game_def = sum(self.gamescores.get(a, {}).get("defense", 0) for a in normalized) / len(normalized)
        game_total = sum(self.gamescores.get(a, {}).get("total", 0) for a in normalized) / len(normalized)

        # 防守评分：抵抗数-弱点数（与游戏公式一致）
        defense_score = len(resisted) - weak_count

        return {
            "offense": offense,
            "resist": len(resisted),
            "weak": weak_count,
            "defense_score": defense_score,
            "game_offense": game_off,
            "game_defense": game_def,
            "game_total": game_total,
        }

    def get_team_coverage(self, team_attrs: list[list[str]]) -> dict:
        """计算队伍整体属性覆盖"""
        all_covered = set()
        all_resisted = set()
        all_weakened = set()

        for attrs in team_attrs:
            for a in attrs:
                all_covered.add(a)
                a_norm = self._normalize_attr(a)
                d = self.type_defense_data.get(a_norm, {})
                for atk_a in d.get("resist_from", []):
                    all_resisted.add(atk_a)
                for atk_a in d.get("immune_from", []):
                    all_resisted.add(atk_a)
                for atk_a in d.get("weak_to", []):
                    all_weakened.add(atk_a)

        return {
            "covered_attrs": len(all_covered),
            "resisted_attrs": len(all_resisted),
            "weak_attrs": len(all_weakened),
            "coverage_score": len(all_resisted) - len(all_weakened),
            "weak_list": sorted(all_weakened),
            "resist_list": sorted(all_resisted),
        }


if __name__ == "__main__":
    am = AttributeMatrix()
    print("洛克王国世界 18属性体系")
    print("=" * 70)
    print(f"{'属性':<6} {'克制数':>5} {'抵抗数':>5} {'弱点数':>5} {'游戏攻':>5} {'游戏防':>5} {'游戏总':>5}")
    print("-" * 50)
    for attr in am.attr_names:
        off = am.type_offense_count[attr]
        d = am.type_defense_data[attr]
        gs = am.gamescores[attr]
        print(f"{attr:<6} {off:>5} {d['resist_count']+d['immune_count']:>5} {d['weak_count']:>5} {gs['offense']:>5} {gs['defense']:>5} {gs['total']:>5}")
