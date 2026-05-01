#!/usr/bin/env python3
"""
队伍构筑深度分析系统 v2
基于实战构筑理论：轴识别、联防互补、构筑风格分类、起点手段分析、PVP热门应对压力测试

v2 新增:
- PVP热门精灵应对覆盖率分析
- 按体系自动生成多支最优队伍
"""
import json
import re
from pathlib import Path
from typing import Dict, List, Tuple, Set
from itertools import combinations

DATA_DIR = Path(__file__).parent.parent / "data"


class BuildAnalyzer:
    def __init__(self):
        with open(DATA_DIR / "pets_final.json", encoding="utf-8") as f:
            self.pets = json.load(f)

        # 加载PVP热门精灵
        hot_path = DATA_DIR / "pvp_hot_pets.json"
        if hot_path.exists():
            with open(hot_path, encoding="utf-8") as f:
                self.hot_pets = json.load(f)
        else:
            self.hot_pets = {}

        # 属性克制表
        self.weakness_map = {
            "萌": ["机械", "恶"],
            "机械": ["火", "电", "武"],
            "火": ["水", "地"],
            "水": ["电", "草"],
            "草": ["火", "冰", "翼", "虫", "毒"],
            "电": ["地"],
            "地": ["水", "草", "冰"],
            "冰": ["火", "武"],
            "武": ["萌", "翼"],
            "翼": ["电", "冰"],
            "幽": ["恶魔", "幽"],
            "恶": ["虫", "光"],
            "龙": ["冰", "龙", "幻"],
            "幻": ["恶魔", "虫"],
            "光": ["武", "恶魔"],
            "虫": ["火", "翼", "冰"],
            "毒": ["地"],
            "一般": [],
        }

        # ========== 战术轴定义 ==========
        self.tactical_axis = {
            "星陨斩杀轴": {
                "core": ["怖哭菇", "落陨星兔", "帕尔"],
                "desc": "怖哭菇上星陨印记 → 落陨星兔引爆 → 帕尔炸能量斩杀",
            },
            "狼王收割轴": {
                "core": ["卡瓦重", "音速犬", "恶魔狼"],
                "desc": "卡瓦重冰冻控速 → 音速犬热身回火轮转 → 恶魔狼王收割",
            },
            "雷暴联动轴": {
                "core": ["闪电鳗鱼", "星光狮"],
                "desc": "闪电鳗鱼+星光狮双雷暴，迸发连锁压制",
            },
            "迅捷水刃轴": {
                "core": ["圣羽翼王", "岚鸟", "翠顶夫人", "黑羽夫人"],
                "desc": "圣羽翼王迅捷触发翼系队友水刃技能",
            },
            "地刺触发轴": {
                "core": ["食尘短绒", "巨噬针鼹"],
                "desc": "食尘短绒触发巨噬针鼹特性，双地刺手联动",
            },
            "魔能轮转轴": {
                "core": ["黑猫巫师", "小皮球"],
                "desc": "魔能爆黑猫配合小皮球轮转，持续压制",
            },
        }

        # ========== 联防体系精灵 ==========
        self.defense_anchors = {
            "寂灭骨龙": {"attrs": ["龙", "幽"], "desc": "龙+幽联防核心，高耐久，三回合复活"},
            "鳗尾兽": {"attrs": ["地", "草"], "desc": "地+草联防，中转电系"},
            "兽花蕾": {"attrs": ["光", "草"], "desc": "萌系血脉联防核心"},
            "嗜波螺": {"attrs": ["地", "水"], "desc": "地+水联防，中转火系"},
            "化蝶": {"attrs": ["虫", "萌"], "desc": "三命容错，联防稳定"},
            "雪影冰灵": {"attrs": ["冰", "萌"], "desc": "冰+萌联防，控速弹反"},
            "贝古斯": {"attrs": ["机械", "火"], "desc": "机械+火联防，高物防"},
        }

        # ========== 起点操作手段分类 ==========
        self.startup_keywords = {
            "炸能量": ["精神扰乱", "操控", "勾魂", "恶作剧", "圆号鱼", "聒噪", "激怒"],
            "控速": ["缓冻", "速冻", "电离爆破", "冰捆缚", "瞬间零度", "冰天雪地", "冰冻"],
            "转换对位": ["吓退", "摇篮曲", "芳香引诱", "流沙", "遁地", "石锁",
                      "闪击折返", "羽刃", "风隐", "恶意逃离", "泡沫幻影", "加大功率"],
            "削弱火力": ["退化", "刺盾", "反弹", "力量吞噬", "隐藏条款", "甜心续航"],
            "天气印记": ["雷暴", "晴天", "雨天", "雪天", "沙暴", "星陨", "中毒印记", "蓄电印记"],
            "强化接棒": ["击鼓传花", "赤子之心", "力量增效", "速度强化"],
            "折返回避": ["吓退", "倾泻", "高温回火", "折返", "羽刃", "风隐", "恶意逃离",
                      "泡沫幻影", "加大功率", "流沙", "遁地", "石锁"],
        }

    def _has_pet(self, members: Set[str], keyword: str) -> bool:
        for pid in members:
            pet = self.pets.get(pid, {})
            if keyword in pet.get("name", ""):
                return True
        return False

    def _has_skill_keyword(self, pet_id: str, keywords: List[str]) -> bool:
        pet = self.pets.get(pet_id, {})
        skills = pet.get("skills", {}).get("learnset", []) + pet.get("skills", {}).get("recommended", [])
        for sk in skills:
            text = str(sk.get("name", "")) + " " + str(sk.get("desc", ""))
            for kw in keywords:
                if kw in text:
                    return True
        return False

    # ========== 轴检测 ==========
    def detect_axes(self, team: List[str]) -> Dict:
        members = set(team)
        results = {}
        for axis_name, axis_info in self.tactical_axis.items():
            found = []
            for core_name in axis_info["core"]:
                if self._has_pet(members, core_name):
                    found.append(core_name)
            completeness = int(len(found) / len(axis_info["core"]) * 100) if axis_info["core"] else 0
            results[axis_name] = {
                "description": axis_info["desc"],
                "complete": completeness >= 70,
                "completeness": completeness,
                "members_found": found,
            }
        return results

    # ========== 联防互补分析 ==========
    def analyze_synergy_coverage(self, team: List[str], cleaner_name: str = None) -> Dict:
        all_attrs = set()
        for pid in team:
            pet = self.pets.get(pid, {})
            for attr in pet.get("attrs", []):
                all_attrs.add(attr)

        anchors_found = []
        for anchor_name, anchor_info in self.defense_anchors.items():
            if self._has_pet(set(team), anchor_name):
                anchors_found.append({
                    "name": anchor_name,
                    "attrs": anchor_info["attrs"],
                    "desc": anchor_info["desc"],
                })

        # 清场手弱点分析
        cleaner_analysis = None
        if cleaner_name:
            cleaner = None
            for pid in team:
                pet = self.pets.get(pid, {})
                if cleaner_name in pet.get("name", ""):
                    cleaner = pet
                    break
            if cleaner:
                cleaner_attrs = cleaner.get("attrs", [])
                cleaner_weakness = set()
                for attr in cleaner_attrs:
                    cleaner_weakness.update(self.weakness_map.get(attr, []))
                weakness_covered = {}
                for weak_attr in cleaner_weakness:
                    covered = False
                    for pid in team:
                        pet = self.pets.get(pid, {})
                        pet_attrs = pet.get("attrs", [])
                        for counter_attr in self.weakness_map.get(weak_attr, []):
                            if counter_attr in pet_attrs:
                                covered = True
                                break
                        if covered:
                            break
                    weakness_covered[weak_attr] = covered
                uncovered = [k for k, v in weakness_covered.items() if not v]
                cleaner_analysis = {
                    "cleaner_name": cleaner.get("name", ""),
                    "cleaner_attrs": cleaner_attrs,
                    "cleaner_weakness": list(cleaner_weakness),
                    "uncovered_weakness": uncovered,
                    "coverage_pct": int((1 - len(uncovered) / max(1, len(cleaner_weakness))) * 100),
                }

        return {
            "team_attrs": list(all_attrs),
            "defense_anchors": anchors_found,
            "cleaner_synergy": cleaner_analysis,
        }

    # ========== 对面性能评分 ==========
    def score_1v1_performance(self, pet_id: str) -> Dict:
        pet = self.pets.get(pet_id, {})
        if not pet:
            return {"error": "not found"}
        stats = pet.get("stats", {})
        name = pet.get("name", "")
        score = 0
        details = []

        spd = stats.get("spd", 0)
        if spd >= 130:
            score += 30; details.append("极速线≥130")
        elif spd >= 120:
            score += 25; details.append("高速线≥120")
        elif spd >= 110:
            score += 20; details.append("中速线≥110")
        else:
            score += 10; details.append("慢速")

        hp = stats.get("hp", 0)
        defense = stats.get("def", 0) + stats.get("mdef", 0)
        bulk = hp * defense
        if bulk >= 30000:
            score += 25; details.append("极硬身板")
        elif bulk >= 20000:
            score += 20; details.append("硬身板")
        else:
            score += 10; details.append("身板一般")

        atk = max(stats.get("atk", 0), stats.get("matk", 0))
        if atk >= 130:
            score += 25; details.append("顶级火力≥130")
        elif atk >= 120:
            score += 20; details.append("强力火力")
        else:
            score += 10; details.append("火力一般")

        has_escape = self._has_skill_keyword(pet_id, self.startup_keywords["折返回避"])
        has_debuff = self._has_skill_keyword(pet_id, ["毒孢子", "引燃", "冰冻", "催眠", "麻痹", "中毒"])
        has_counter = self._has_skill_keyword(pet_id, ["应对状态", "应对攻击", "先制应对"])
        if has_escape: score += 10; details.append("折返回避")
        if has_debuff: score += 5; details.append("异常状态")
        if has_counter: score += 5; details.append("应对状态")

        return {"name": name, "total_score": score, "details": details,
                "speed": spd, "bulk": bulk, "attack": atk,
                "has_escape": has_escape, "has_debuff": has_debuff, "has_counter": has_counter}

    # ========== 起点手段分析 ==========
    def analyze_startup_methods(self, team: List[str]) -> Dict:
        results = {}
        for method_type, keywords in self.startup_keywords.items():
            if method_type == "折返回避":
                continue
            count = 0
            pets_with = []
            for pid in team:
                if self._has_skill_keyword(pid, keywords):
                    count += 1
                    pet = self.pets.get(pid, {})
                    pets_with.append(pet.get("name", pid))
            results[method_type] = {"count": count, "pets": pets_with}
        total_methods = sum(r["count"] for r in results.values())
        return {"by_type": results, "total_methods": total_methods,
                "diversity_score": min(100, total_methods * 18)}

    # ========== 轮转折返能力 ==========
    def analyze_roll_capability(self, team: List[str]) -> Dict:
        escape_count = 0
        escape_pets = []
        for pid in team:
            if self._has_skill_keyword(pid, self.startup_keywords["折返回避"]):
                escape_count += 1
                pet = self.pets.get(pid, {})
                escape_pets.append(pet.get("name", pid))
        roll_anchors = []
        for pid in team:
            pet = self.pets.get(pid, {})
            name = pet.get("name", "")
            if any(kw in name for kw in ["小皮球", "绒光优优", "优优"]):
                roll_anchors.append(name)
        return {"escape_skill_count": escape_count, "escape_pets": escape_pets,
                "roll_anchors": roll_anchors,
                "roll_capability_score": min(100, escape_count * 15 + len(roll_anchors) * 25)}

    # ========== PVP热门精灵应对分析（核心新功能）==========
    def analyze_hot_pet_coverage(self, team: List[str]) -> Dict:
        """分析队伍对PVP热门精灵的应对能力"""
        if not self.hot_pets:
            return {"error": "pvp_hot_pets.json not found, run extraction first"}

        # 收集队伍所有属性
        team_attrs = set()
        for pid in team:
            pet = self.pets.get(pid, {})
            team_attrs.update(pet.get("attrs", []))

        # 对每只热门精灵计算威胁度
        threat_results = []
        total_can_counter = 0
        for rank_key, hot_pet in sorted(self.hot_pets.items(), key=lambda x: int(x[0])):
            hot_name = hot_pet["name"]
            hot_attrs = hot_pet["attrs"]
            hot_stats = hot_pet["stats"]

            # 计算队伍中能counter它的精灵数量
            counter_count = 0
            counter_pets = []
            for pid in team:
                pet = self.pets.get(pid, {})
                my_attrs = pet.get("attrs", [])
                # 属性克制
                can_counter = False
                for hot_attr in hot_attrs:
                    for counter_attr in self.weakness_map.get(hot_attr, []):
                        if counter_attr in my_attrs:
                            can_counter = True
                            break
                    if can_counter:
                        break
                if can_counter:
                    counter_count += 1
                    counter_pets.append(pet.get("name", pid))

            # 威胁度：无法应对=高风险
            if counter_count == 0:
                risk = "red"
                risk_label = "🔴 无应对"
            elif counter_count == 1:
                risk = "yellow"
                risk_label = "🟡 勉强应对"
            else:
                risk = "green"
                risk_label = "🟢 可应对"

            threat_results.append({
                "rank": int(rank_key),
                "name": hot_name,
                "attrs": hot_attrs,
                "speed": hot_stats.get("spd", 0),
                "counter_count": counter_count,
                "counter_pets": counter_pets,
                "risk": risk,
                "risk_label": risk_label,
            })

        red_count = sum(1 for t in threat_results if t["risk"] == "red")
        yellow_count = sum(1 for t in threat_results if t["risk"] == "yellow")
        green_count = sum(1 for t in threat_results if t["risk"] == "green")
        total = len(threat_results)

        coverage_score = int((green_count / total) * 60 + (yellow_count / total) * 30) if total > 0 else 0

        return {
            "total_hot_pets": total,
            "red_threats": [t for t in threat_results if t["risk"] == "red"],
            "yellow_threats": [t for t in threat_results if t["risk"] == "yellow"],
            "all_threats": threat_results,
            "coverage_score": coverage_score,
            "summary": f"🟢{green_count} 🟡{yellow_count} 🔴{red_count} | 应对覆盖率 {coverage_score}/100",
        }

    # ========== 构筑风格分类 ==========
    def classify_build_style(self, team: List[str]) -> Dict:
        aggressive_score = 0
        startup_score = 0
        roll_score = 0

        total_1v1 = 0
        attack_attr_count = {}
        for pid in team:
            perf = self.score_1v1_performance(pid)
            total_1v1 += perf.get("total_score", 0)
            pet = self.pets.get(pid, {})
            for attr in pet.get("attrs", []):
                attack_attr_count[attr] = attack_attr_count.get(attr, 0) + 1

        avg_1v1 = total_1v1 / len(team) if team else 0
        if avg_1v1 >= 70: aggressive_score += 40
        elif avg_1v1 >= 60: aggressive_score += 25
        redundant = sum(1 for v in attack_attr_count.values() if v >= 3)
        aggressive_score += redundant * 15

        roll = self.analyze_roll_capability(team)
        if roll["escape_skill_count"] >= 3: aggressive_score += 20; roll_score += 30
        elif roll["escape_skill_count"] >= 2: aggressive_score += 10; roll_score += 15
        if len(roll["roll_anchors"]) >= 2: roll_score += 40
        elif len(roll["roll_anchors"]) >= 1: roll_score += 20

        startup = self.analyze_startup_methods(team)
        startup_score += startup["diversity_score"]
        axes = self.detect_axes(team)
        complete_axes = sum(1 for ax in axes.values() if ax["complete"])
        if complete_axes >= 1: startup_score += 20
        if complete_axes >= 2: startup_score += 30

        synergy = self.analyze_synergy_coverage(team)
        anchor_count = len(synergy["defense_anchors"])
        if anchor_count >= 3: roll_score += 30
        elif anchor_count >= 2: roll_score += 15

        total = aggressive_score + startup_score + roll_score
        if total > 0:
            aggressive_pct = int(aggressive_score / total * 100)
            startup_pct = int(startup_score / total * 100)
            roll_pct = 100 - aggressive_pct - startup_pct
        else:
            aggressive_pct = startup_pct = roll_pct = 33

        styles = [("对面构筑", aggressive_pct), ("起点展开", startup_pct), ("循环构筑", roll_pct)]
        styles.sort(key=lambda x: -x[1])
        primary = styles[0][0]
        if styles[0][1] - styles[1][1] < 10:
            primary = f"{styles[0][0]}+{styles[1][0]}混合"

        return {"primary_style": primary,
                "style_scores": {"对面构筑": aggressive_score, "起点展开": startup_score, "循环构筑": roll_score},
                "style_percentages": {"对面构筑": aggressive_pct, "起点展开": startup_pct, "循环构筑": roll_pct}}

    # ========== 完整分析入口 ==========
    def full_analysis(self, team: List[str], cleaner_name: str = None) -> Dict:
        team_ids = []
        for name in team:
            found = False
            for pid, pet in self.pets.items():
                if name in pet.get("name", ""):
                    team_ids.append(pid); found = True; break
            if not found:
                team_ids.append(name)

        axes = self.detect_axes(team_ids)
        synergy = self.analyze_synergy_coverage(team_ids, cleaner_name)
        perf_results = {}
        for pid in team_ids:
            perf = self.score_1v1_performance(pid)
            if "name" in perf:
                perf_results[perf["name"]] = perf
        startup = self.analyze_startup_methods(team_ids)
        roll = self.analyze_roll_capability(team_ids)
        style = self.classify_build_style(team_ids)
        hot_coverage = self.analyze_hot_pet_coverage(team_ids)

        # 综合评分
        axis_score = sum(ax["completeness"] for ax in axes.values() if ax["complete"])
        synergy_score = len(synergy["defense_anchors"]) * 15
        perf_avg = sum(p.get("total_score", 0) for p in perf_results.values()) / max(1, len(perf_results))
        startup_score = startup["diversity_score"]
        roll_score = roll["roll_capability_score"]
        hot_score = hot_coverage.get("coverage_score", 0)
        red_penalty = min(len(hot_coverage.get("red_threats", [])) * 5, 40)

        total = int(
            axis_score * 0.15 +
            synergy_score * 0.1 +
            perf_avg * 0.15 +
            startup_score * 0.15 +
            roll_score * 0.1 +
            hot_score * 0.35) - red_penalty  # 热门应对权重最高
        total = max(0, min(100, total))

        return {
            "tactical_axes": axes,
            "synergy_coverage": synergy,
            "1v1_performance": perf_results,
            "startup_methods": startup,
            "roll_capability": roll,
            "build_style": style,
            "hot_pet_coverage": hot_coverage,
            "build_quality_score": total,
        }

    # ========== 按体系生成最优队伍（核心新功能）==========
    def generate_per_system_teams(self, top_k: int = 5) -> Dict[str, List[Dict]]:
        """
        围绕每个已定义体系，贪心搜索最优队伍
        返回: {体系名: [队伍1, 队伍2, ...]}
        """
        # 先在 pets_final 中建立名称→ID映射
        name_to_pid = {}
        for pid, p in self.pets.items():
            name_to_pid[p["name"]] = pid

        # 找出所有精灵的实际名称列表
        all_pet_names = list(name_to_pid.keys())

        results = {}
        for axis_name, axis_info in self.tactical_axis.items():
            core_names = axis_info["core"]
            # 检查核心精灵是否存在
            available_core = [n for n in core_names if any(n in pn for pn in all_pet_names)]
            if len(available_core) < 1:
                continue

            # 固定核心精灵
            core_pids = []
            for cn in available_core:
                for pn in all_pet_names:
                    if cn in pn:
                        core_pids.append(name_to_pid[pn])
                        break

            # 需要填充多少空位
            slots = 6 - len(core_pids)
            if slots <= 0:
                continue

            # 候选精灵池：排除核心，按1v1性能和联防价值排序
            candidates = [
                pid for pid in self.pets.keys() if pid not in core_pids
            ]

            # 贪心搜索：逐个加入评分最高的精灵
            teams = self._greedy_team_search(core_pids, candidates, slots, axis_info, top_k)
            if teams:
                results[axis_name] = teams

        return results

    def _greedy_team_search(self, core: List[str], candidates: List[str], slots: int,
                            axis_info: Dict, top_k: int) -> List[Dict]:
        """贪心搜索最优队伍"""
        import random
        random.seed(42)

        best_teams = []
        best_scores = []

        # 多轮随机采样
        for _ in range(min(200, len(candidates) * 10)):
            # 随机选slots个候选填充
            if len(candidates) < slots:
                fill = candidates[:]
            else:
                fill = random.sample(candidates, slots)
            team = core + fill

            if len(team) != 6:
                continue

            # 评分
            result = self.full_analysis(team)
            score = result["build_quality_score"]

            # 检查热门精灵覆盖率
            red_count = len(result.get("hot_pet_coverage", {}).get("red_threats", []))
            if red_count >= 10:  # 太多热门精灵无法应对，扣分
                score -= red_count * 2

            # 保持top_k最优
            team_names = [self.pets.get(t, {}).get("name", t) for t in team]
            entry = {"members": team_names, "score": score, "analysis": result}

            if len(best_teams) < top_k:
                best_teams.append(entry)
                best_scores.append(score)
                best_teams.sort(key=lambda x: -x["score"])
                best_scores.sort(reverse=True)
            elif score > best_scores[-1]:
                best_teams[-1] = entry
                best_scores[-1] = score
                best_teams.sort(key=lambda x: -x["score"])
                best_scores.sort(reverse=True)

        return best_teams


if __name__ == "__main__":
    analyzer = BuildAnalyzer()

    # 测试雷暴队
    test_team = ["闪电鳗鱼", "星光狮", "影狸", "黑羽夫人", "寂灭骨龙", "贝古斯"]
    print("=" * 70)
    print("队伍构筑深度分析 v2 - 雷暴队")
    print("=" * 70)
    result = analyzer.full_analysis(test_team)

    print(f"\n构筑风格: {result['build_style']['primary_style']}")

    print("\n【战术轴检测】")
    for an, ai in result["tactical_axes"].items():
        if ai["completeness"] > 0:
            print(f"  {an}: {ai['completeness']}% {'✓' if ai['complete'] else ''} -> {ai['members_found']}")

    print(f"\n【PVP热门精灵应对】{result['hot_pet_coverage'].get('summary', '')}")
    for t in result["hot_pet_coverage"].get("red_threats", [])[:5]:
        print(f"  🔴 {t['name']}(速度{t['speed']}) - 无应对手段!")
    for t in result["hot_pet_coverage"].get("yellow_threats", [])[:3]:
        print(f"  🟡 {t['name']}(速度{t['speed']}) - 仅{t['counter_pets']}可应对")

    print(f"\n构筑综合评分: {result['build_quality_score']}/100")

    # 按体系生成最优队伍
    print("\n" + "=" * 70)
    print("按体系生成最优队伍")
    print("=" * 70)
    per_system = analyzer.generate_per_system_teams(top_k=2)
    for sys_name, teams in per_system.items():
        print(f"\n【{sys_name}】")
        for i, t in enumerate(teams[:2], 1):
            hot_summary = t["analysis"]["hot_pet_coverage"].get("summary", "")
            print(f"  {i}. 评分:{t['score']} | {', '.join(t['members'])}")
            print(f"     PVP应对: {hot_summary}")
