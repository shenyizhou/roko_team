#!/usr/bin/env python3
"""
属性联防矩阵计算
"""
import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"


class AttributeMatrix:
    def __init__(self):
        with open(DATA_DIR / "attribute_chart.json", encoding="utf-8") as f:
            self.attr_data = json.load(f)

        self.attr_names = [a["nameCn"] for a in self.attr_data["attributes"]]
        self.attr_key_to_name = {a["key"]: a["nameCn"] for a in self.attr_data["attributes"]}

        # 宠物属性简称到标准名的映射
        self.short_to_full = {
            "幽": "幽灵",
            "恶": "恶魔",
            "地": "地面",
            "翼": "翼",  # 数据里就是"翼"
        }
        for name in self.attr_names:
            self.short_to_full[name] = name  # 标准名直接映射

        # 构建倍率矩阵（按倍率分组的格式）
        self.multipliers = {atk: {def_: 1.0 for def_ in self.attr_names} for atk in self.attr_names}

        for atk in self.attr_data["attributes"]:
            atk_name = atk["nameCn"]
            offense = atk["battleMultiplier"]["offense"]

            # 处理不同倍率
            for mult_str, def_keys in offense.items():
                mult = float(mult_str)
                for def_key in def_keys:
                    def_name = self.attr_key_to_name[def_key]
                    self.multipliers[atk_name][def_name] = mult

        # 缓存每个属性的克制面和抵抗面
        self._calc_type_scores()

    def _calc_type_scores(self):
        """计算每个属性的攻防价值"""
        self.type_offense_scores = {}  # 克制面数量
        self.type_defense_scores = {}  # 抵抗面数量 - 弱点数量

        for attr in self.attr_names:
            # 进攻价值：克制的属性数量
            offense = sum(1 for mult in self.multipliers[attr].values() if mult >= 2)
            # 防守价值：抵抗的属性数量 - 被克制的属性数量
            resist = sum(1 for a in self.attr_names if self.multipliers[a][attr] <= 0.5)
            weak = sum(1 for a in self.attr_names if self.multipliers[a][attr] >= 2)
            immune = sum(1 for a in self.attr_names if self.multipliers[a][attr] == 0)

            self.type_offense_scores[attr] = offense
            self.type_defense_scores[attr] = {
                "resist": resist,
                "weak": weak,
                "immune": immune,
                "score": resist + 2 * immune - weak
            }

    def _normalize_attr(self, attr: str) -> str:
        """转换属性简称为标准名"""
        return self.short_to_full.get(attr, attr)

    def get_multiplier(self, atk_attr: str, def_attr: str) -> float:
        """获取属性克制倍率"""
        atk = self._normalize_attr(atk_attr)
        def_ = self._normalize_attr(def_attr)
        return self.multipliers.get(atk, {}).get(def_, 1.0)

    def get_type_score(self, attrs: list[str]) -> dict:
        """计算宠物属性的综合评分"""
        total_offense = 0
        total_resist = 0
        total_weak = 0
        total_immune = 0

        normalized_attrs = [self._normalize_attr(a) for a in attrs]

        # 多属性时，进攻取最大克制
        for attr in normalized_attrs:
            if attr in self.type_offense_scores:
                total_offense = max(total_offense, self.type_offense_scores[attr])

        # 防守取联合覆盖（抵抗任意属性就算）
        resisted_attrs = set()
        weakened_attrs = set()
        immuned_attrs = set()

        for attr in normalized_attrs:
            if attr in self.type_defense_scores:
                for a in self.attr_names:
                    mult = self.multipliers[a][attr]
                    if mult == 0:
                        immuned_attrs.add(a)
                    elif mult <= 0.5:
                        resisted_attrs.add(a)
                    elif mult >= 2:
                        weakened_attrs.add(a)

        total_resist = len(resisted_attrs - immuned_attrs)
        total_immune = len(immuned_attrs)
        total_weak = len(weakened_attrs)

        return {
            "offense": total_offense,
            "resist": total_resist,
            "immune": total_immune,
            "weak": total_weak,
            "defense_score": total_resist + 2 * total_immune - total_weak
        }

    def get_team_coverage(self, team_attrs: list[list[str]]) -> dict:
        """计算队伍整体属性覆盖"""
        all_resisted = set()
        all_weakened = set()
        all_covered = set()  # 队伍宠物属性覆盖

        for attrs in team_attrs:
            all_covered.update(attrs)
            # 统计抵抗和弱点
            for attr in attrs:
                attr_norm = self._normalize_attr(attr)
                for a in self.attr_names:
                    mult = self.multipliers[a][attr_norm]
                    if mult <= 0.5:
                        all_resisted.add(a)
                    elif mult >= 2:
                        all_weakened.add(a)

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
    print("属性攻防价值排行:")
    print("\n进攻价值（克制面数量）:")
    for attr, score in sorted(am.type_offense_scores.items(), key=lambda x: -x[1]):
        print(f"  {attr:4}: {score}")

    print("\n防守价值（抵抗-弱点+2*免疫）:")
    for attr, data in sorted(am.type_defense_scores.items(), key=lambda x: -x[1]["score"]):
        print(f"  {attr:4}: 抵抗{data['resist']} - 弱点{data['weak']} + 免疫{data['immune']}*2 = {data['score']}")
