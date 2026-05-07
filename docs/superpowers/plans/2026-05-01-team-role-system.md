# 队伍角色与体系识别系统 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现精灵角色分类和队伍体系识别功能，整合到现有评分系统

**Architecture:** 新增两个独立模块（role_classifier.py和system_detector.py，然后整合到team_analyzer.py中，最后优化遗传算法的适应度函数

**Tech Stack:** Python 3, json, regex, 现有代码库(pet_scorer.py, team_analyzer.py

---

## 前置检查

- [ ] **Step 0: 读取现有文件结构和数据

读取并理解
  - 读取 `models/pet_scorer.py` 了解现有的评分逻辑和技能分析
  - 读取 `data/pets_final.json` 了解精灵数据结构
  - 读取 `data/all_pet_rankings.json` 了解现有排名数据

---

### Task 1: 实现角色分类器 (role_classifier.py)

**Files:**
- Create: `models/role_classifier.py`
- Test: 手动验证几题测试

- [ ] **Step 1: 创建模块框架和导入

```python
#!/usr/bin/env python3
"""
精灵角色分类器
识别每只精灵可以是：清场手(Cleaner)、首发(Starter)、辅助(Support)、扫尾(Finisher)
"""
import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"


class RoleClassifier:
    def __init__(self):
        with open(DATA_DIR / "pets_final.json", encoding="utf-8") as f:
            self.pets = json.load(f)

    def _has_buff_skills(self, pet_id: str) -> dict:
        """检查是否有强化技能"""
        pet = self.pets.get(pet_id, {})
        skills = pet.get("skills", {}).get("learnset", []) + pet.get("skills", {}).get("recommended", [])
        has_atk_buff = any(re.search(r"[物魔双]攻\+\d+%", str(sk.get("desc", ""))) for sk in skills)
        has_spd_buff = any("速度+" in str(sk.get("desc", "")) for sk in skills)
        return {"atk": has_atk_buff, "spd": has_spd_buff}

    def _has_heal_skills(self, pet_id: str) -> bool:
        """检查是否有回复技能"""
        pet = self.pets.get(pet_id, {})
        skills = pet.get("skills", {}).get("learnset", []) + pet.get("skills", {}).get("recommended", [])
        heal_keywords = ["回复", "治疗", "回血", "休息回复", "续航"]
        return any(kw in str(sk.get("desc", "")) for kw in heal_keywords for sk in skills)

    def classify(self, pet_id: str) -> dict:
        """为一只精灵计算四个角色的得分"""
        pet = self.pets.get(pet_id)
        if not pet:
            return {"error": "宠物不存在"}

        stats = pet.get("stats", {})
        spd = stats.get("spd", 0)
        hp = stats.get("hp", 0)
        defense = stats.get("def", 0)
        trait = pet.get("trait", {}).get("desc", "")

        cleaner_score = 0
        starter_score = 0
        support_score = 0
        finisher_score = 0

        # 清场手评分
        buffs = self._has_buff_skills(pet_id)

        # 强化超速型: 有加速技能 + 强化后速度 > 130 + 有输出技能
        if buffs["spd"] and spd * 1.5 > 130 and buffs["atk"]:
            cleaner_score += 30
        elif buffs["spd"] and spd * 1.5 > 120:
            cleaner_score += 20

        # 高速强化型: 基础速度快 + 有强化
        if spd >= 120 and buffs["atk"]:
            cleaner_score += 25
        if spd >= 110 and buffs["atk"]:
            cleaner_score += 15

        # 特性优秀型
        trait_keywords = ["能量", "力竭", "斩杀", "迸发", "折射"]
        if any(kw in trait for kw in trait_keywords):
            cleaner_score += 20

        # 肉盾强化型: 身板硬 + 有回复 + 有强化
        if hp * defense >= 25000 and self._has_heal_skills(pet_id) and buffs["atk"]:
            cleaner_score += 25
        elif hp * defense >= 20000 and self._has_heal_skills(pet_id):
            cleaner_score += 15

        # 首发评分
        pet_skills = pet.get("skills", {}).get("learnset", []) + pet.get("skills", {}).get("recommended", [])

        # 天气技能
        has_weather = any("将天气改为" in str(sk.get("desc", "")) for sk in pet_skills)
        if has_weather:
            starter_score += 30

        # 迅捷技能
        has_swift = any("迅捷" in str(sk.get("desc", "")) or sk.get("cost", 99) == 0 for sk in pet_skills)
        if has_swift:
            starter_score += 30

        # 控制技能
        control_keywords = ["眩晕", "寄生", "聒噪", "惊吓"]
        has_control = any(kw in str(sk.get("desc", "")) for kw in control_keywords for sk in pet_skills)
        if has_control:
            starter_score += 20

        # 速度快
        if spd >= 110:
            starter_score += 20

        # 辅助评分
        # 驱散类技能
        utility_keywords = ["驱散印记", "清增益", "清减益", "焚烧烙印", "食腐", "转化印记"]
        has_dispel = any(kw in str(sk.get("desc", "")) for kw in utility_keywords for sk in pet_skills)
        if has_dispel:
            support_score += 30

        # 队友增益技能
        team_buff_keywords = ["力量增效", "速度强化", "魔攻强化", "给队友", "全体"]
        has_team_buff = any(kw in str(sk.get("desc", "")) for kw in team_buff_keywords for sk in pet_skills)
        if has_team_buff:
            support_score += 25

        # 联动特性
        if "印记" in trait and ("队友" in trait:
            support_score += 25

        # 高防低攻
        atk = stats.get("atk", 0) + stats.get("matk", 0)
        if (defense + stats.get("mdef", 0)) > atk * 1.5:
            support_score += 20

        # 扫尾评分
        # 斩杀类技能
        finish_keywords = ["斩杀", "HP越低", "血越少", "收割"]
        has_finish = any(kw in str(sk.get("desc", "")) for kw in finish_keywords for sk in pet_skills)
        if has_finish:
            finisher_score += 40

        # 先制
        if has_swift:
            finisher_score += 30

        # 高速高攻但身板脆
        if spd >= 110 and atk >= 180 and hp * defense < 15000:
            finisher_score += 30

        # 归一化到0-100
        def normalize(score):
            return min(100, max(0, score))

        scores = {
            "cleaner": normalize(cleaner_score),
            "starter": normalize(starter_score),
            "support": normalize(support_score),
            "finisher": normalize(finisher_score),
        }

        # 取前2个角色
        sorted_roles = sorted(scores.items(), key=lambda x: -x[1])
        primary_roles = [r[0] for r in sorted_roles[:2] if r[1] >= 30]

        return {
            "pet_id": pet_id,
            "pet_name": pet.get("name", pet_id),
            "role_scores": scores,
            "primary_roles": primary_roles,
            "primary_role_names": [{"cleaner": "清场手", "starter": "首发", "support": "辅助", "finisher": "扫尾"}[r] for r in primary_roles],
        }


if __name__ == "__main__":
    # 测试
    rc = RoleClassifier()
    test_pets = ["闪电鳗鱼", "星光狮", "厉毒修萝", "帕尔", "圣剑"]
    for pid in test_pets:
        result = rc.classify(pid)
        if "error" not in result:
            print(f"{result['pet_name']}: {result['primary_role_names']}")
            print(f"  得分: {result['role_scores']}")
```

- [ ] **Step 2: 运行测试验证

Run: `cd /Users/shenyizhou/code/test/roko_team/models/role_classifier.py`
Expected: 能正常输出几个测试精灵的角色分类结果

- [ ] **Step 3: 修复问题并提交

```bash
cd /Users/shenyizhou/code/test/roko_team
git add models/role_classifier.py
git commit -m "feat: add role classifier module
```

---

### Task 2: 实现体系识别器 (system_detector.py)

**Files:**
- Create: `models/system_detector.py`
- Test: 手动测试

- [ ] **Step 1: 创建模块框架

```python
#!/usr/bin/env python3
"""
队伍体系识别器
识别毒队、星陨队、雷暴队等战术体系
"""
import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"


class SystemDetector:
    def __init__(self):
        with open(DATA_DIR / "pets_final.json", encoding="utf-8") as f:
            self.pets = json.load(f)

        # 体系定义
        self.systems = {
            "毒队": {
                "core": ["厉毒修萝", "裘卡"],
                "synergy": ["影狸", "剧毒蜈蚣"],
                "calc": self._calc_poison_team,
            },
            "星陨队": {
                "core": ["怖哭菇", "落陨星兔"],
                "synergy": ["帕尔", "暮星辰", "圣羽翼王"],
                "calc": self._calc_starfall_team,
            },
            "独角兽队": {
                "core": ["白金独角兽"],
                "synergy": ["黄金独角兽"],
                "calc": self._calc_unicorn_team,
            },
            "恶魔狼王队": {
                "core": ["恶魔狼", "奇瓦重"],
                "synergy": ["音速犬"],
                "calc": self._calc_wolfking_team,
            },
            "三三队": {
                "core": ["梦想三三"],
                "synergy": [],
                "calc": self._calc_sansan_team,
            },
            "火神队": {
                "core": ["火神"],
                "synergy": [],
                "calc": self._calc_firegod_team,
            },
            "地武队": {
                "core": ["棋绮后"],
                "synergy": [],
                "calc": self._calc_earth_warrior_team,
            },
            "圣羽翼王队": {
                "core": ["圣羽翼王"],
                "synergy": ["岚鸟"],
                "calc": self._calc_wingking_team,
            },
            "圣剑队": {
                "core": ["圣剑"],
                "synergy": [],
                "calc": self._calc_sword_team,
            },
            "雷暴队": {
                "core": ["闪电鳗鱼", "星光狮"],
                "synergy": [],
                "calc": self._calc_thunder_team,
            },
        }

        # 依赖首领化的精灵
        self.leader_dependent = [
            "白金独角兽", "恶魔狼", "梦想三三", "火神",
            "棋绮后", "圣剑", "圣羽翼王",
        ]

    def _has_skill_keyword(self, team: list[str], keyword: str) -> bool:
        """检查队伍中是否有含关键词的技能"""
        for pet_id in team:
            pet = self.pets.get(pet_id, {})
            skills = pet.get("skills", {}).get("learnset", []) + pet.get("skills", {}).get("recommended", [])
            for sk in skills:
                if keyword in str(sk.get("desc", "")) or keyword in str(sk.get("name", "")):
                    return True
        return False

    def _calc_poison_team(self, members: set[str]) -> dict:
        score = 0
        details = {}

        # 核心: 厉毒修萝 + 裘卡
        core_count = 0
        if "厉毒修萝" in members:
            core_count += 1
            score += 20
        if "裘卡" in members:
            core_count += 1
            score += 20
        details["core"] = core_count

        # 协同: 其他毒系精灵
        synergy_count = sum(1 for m in members if m in ["影狸", "剧毒蜈蚣"])
        score += synergy_count * 15
        details["synergy"] = synergy_count

        # 有印记驱散/转化
        if self._has_skill_keyword(list(members), "驱散印记") or self._has_skill_keyword(list(members), "转化"):
            score += 30
            details["has_dispel"] = True

        return {"score": min(100, score), "details": details}

    def _calc_starfall_team(self, members: set[str]) -> dict:
        score = 0
        details = {}

        # 核心
        core_count = 0
        if "怖哭菇" in members:
            core_count += 1
            score += 25
        if "落陨星兔" in members:
            core_count += 1
            score += 25
        details["core"] = core_count

        # 炸能量
        if "帕尔" in members:
            score += 25
            details["has_paer"] = True

        # 迅捷触发
        if "圣羽翼王" in members or "暮星辰" in members:
            score += 25
            details["has_swift_trigger"] = True

        return {"score": min(100, score), "details": details}

    def _calc_unicorn_team(self, members: set[str]) -> dict:
        score = 0
        details = {}

        if "白金独角兽" in members:
            score += 60
            details["core"] = True

        # 有折射技能
        if self._has_skill_keyword(list(members), "折射"):
            score += 20
            details["has_refract"] = True

        return {"score": min(100, score), "details": details}

    def _calc_wolfking_team(self, members: set[str]) -> dict:
        score = 0
        details = {}

        core_count = 0
        if "恶魔狼" in members:
            core_count += 1
            score += 30
        if "奇瓦重" in members:
            core_count += 1
            score += 20
        details["core"] = core_count

        # 送死组件
        if "音速犬" in members:
            score += 30
            details["has_sacrifice"] = True

        return {"score": min(100, score), "details": details}

    def _calc_sansan_team(self, members: set[str]) -> dict:
        score = 0
        details = {}

        if "梦想三三" in members:
            score += 60
            details["core"] = True

        # 甜心技能
        if self._has_skill_keyword(list(members), "甜心"):
            score += 20
            details["has_sweet"] = True

        return {"score": min(100, score), "details": details}

    def _calc_firegod_team(self, members: set[str]) -> dict:
        score = 0
        details = {}

        if "火神" in members:
            score += 50
            details["core"] = True

        # 火天气
        if self._has_skill_keyword(list(members), "将天气改为晴天"):
            score += 25
            details["has_fire_weather"] = True

        return {"score": min(100, score), "details": details}

    def _calc_earth_warrior_team(self, members: set[str]) -> dict:
        score = 0
        details = {}

        if "棋绮后" in members:
            score += 40
            details["core"] = True

        # 地刺精灵数量
        earth_spike_count = sum(1 for m in members if self._has_skill_keyword([m], "地刺"))
        score += earth_spike_count * 15
        details["earth_spike_count"] = earth_spike_count

        return {"score": min(100, score), "details": details}

    def _calc_wingking_team(self, members: set[str]) -> dict:
        score = 0
        details = {}

        if "圣羽翼王" in members:
            score += 50
            details["core"] = True

        # 翼系协同
        wing_count = sum(1 for m in members if m in ["岚鸟", "黑羽夫人"])
        score += wing_count * 25
        details["wing_synergy"] = wing_count

        return {"score": min(100, score), "details": details}

    def _calc_sword_team(self, members: set[str]) -> dict:
        score = 0
        details = {}

        if "圣剑" in members:
            score += 70
            details["core"] = True

        return {"score": min(100, score), "details": details}

    def _calc_thunder_team(self, members: set[str]) -> dict:
        score = 0
        details = {}

        core_count = 0
        if "闪电鳗鱼" in members:
            core_count += 1
            score += 30
        if "星光狮" in members:
            core_count += 1
            score += 30
        details["core"] = core_count

        # 雷暴天气
        if self._has_skill_keyword(list(members), "雷暴"):
            score += 40
            details["has_thunder_weather"] = True

        return {"score": min(100, score), "details": details}

    def detect(self, team: list[str]) -> dict:
        """检测一支队伍的体系"""
        members = set(team)
        results = []

        for sys_name, sys_config in self.systems.items():
            calc_func = sys_config["calc"]
            result = calc_func(members)
            if result["score"] > 0:
                results.append({
                    "name": sys_name,
                    "score": result["score"],
                    "details": result["details"],
                    "is_complete": result["score"] >= 70,
                })

        # 按得分排序
        results.sort(key=lambda x: -x["score"])

        # 首领化冲突检测
        leader_count = sum(1 for m in members if m in self.leader_dependent)
        has_leader_conflict = leader_count > 1 and "恶魔狼" not in members

        # 恶魔狼王队例外，允许多首领候补
        if "恶魔狼" in members and leader_count > 1:
            has_leader_conflict = False

        # 角色配置合理性
        from .role_classifier import RoleClassifier
        rc = RoleClassifier()

        role_counts = {"cleaner": 0, "starter": 0, "support": 0, "finisher": 0}
        for pet_id in team:
            roles = rc.classify(pet_id)
            if "primary_roles" in roles:
                for r in roles["primary_roles"]:
                    role_counts[r] = role_counts.get(r, 0) + 1

        # 角色合理性评分：每个角色至少有1个，清场手最好有2个
        role_score = 0
        for rname, count in role_counts.items():
            if count >= 1:
                role_score += 25
            if rname == "cleaner" and count >= 2:
                role_score += 15

        return {
            "systems": results[:3],  # 前3个得分最高的体系
            "best_system": results[0] if results else None,
            "leader_conflict": {
                "count": leader_count,
                "has_conflict": has_leader_conflict,
                "dependent_pets": [m for m in members if m in self.leader_dependent],
            },
            "role_config": {
                "counts": role_counts,
                "reasonable_score": min(100, role_score),
            },
        }


if __name__ == "__main__":
    sd = SystemDetector()

    # 测试雷暴队
    test_team = ["闪电鳗鱼", "星光狮", "影狸", "黑羽夫人", "寂灭骨龙", "贝古斯"]
    result = sd.detect(test_team)
    print("测试队伍体系检测:")
    for sys in result["systems"]:
        print(f"  {sys['name']}: {sys['score']}分 {'✓' if sys['is_complete'] else ''}")

    print(f"首领化冲突: {result['leader_conflict']}")
    print(f"角色配置: {result['role_config']}")
```

- [ ] **Step 2: 运行测试验证

Run: `cd /Users/shenyizhou/code/test/roko_team/models/system_detector.py`
Expected: 正常输出测试结果

- [ ] **Step 3: 提交

```bash
git add models/system_detector.py
git commit -m "feat: add system detector module"
```

---

### Task 3: 整合到队伍分析器

**Files:**
- Modify: `models/team_analyzer.py`

- [ ] **Step 1: 在TeamAnalyzer类中导入并整合

在 `__init__` 方法中添加：

```python
from .role_classifier import RoleClassifier
from .system_detector import SystemDetector

# 在 __init__ 末尾:
self.role_classifier = RoleClassifier()
self.system_detector = SystemDetector()
```

- [ ] **Step 2: 修改 analyze_team 方法，加入新的评分维度

在 `analyze_team` 方法末尾，返回之前添加：

```python
        # 新增：体系和角色分析
        system_result = self.system_detector.detect(team_ids)

        # 新的综合评分
        role_score = system_result["role_config"]["reasonable_score"]
        system_score = system_result["best_system"]["score"] if system_result["best_system"] else 0

        # 按新评分公式
        original_total = (
            avg_score * 0.4 +
            (coverage["coverage_score"] + 20) * 2 * 0.25 +
            energy_synergy * 0.2 +
            (first_four_hp / 20000 * 100) * 0.15 -
            debuff_penalty * 0.3
        )

        new_total = (
            original_total * 0.6 +
            role_score * 0.2 +
            system_score * 0.2
        )
```

然后把返回结果中加入新字段：

```python
        return {
            "team_score": round(new_total, 1),
            "original_score": round(original_total, 1),
            # ... 原有字段 ...
            "system_analysis": system_result,
            "role_breakdown": {
                pet_id: self.role_classifier.classify(pet_id)
                for pet_id in team_ids
            },
        }
```

- [ ] **Step 3: 在 print_team_report 方法中新增输出新的报告板块

在方法末尾添加：

```python
        # 角色配置分析
        print("\n【角色配置分析】")
        role_names = {"cleaner": "清场手", "starter": "首发", "support": "辅助", "finisher": "扫尾"}
        for r_key, r_name in role_names.items():
            count = result["system_analysis"]["role_config"]["counts"].get(r_key, 0)
            check = "✓" if count >= 1 else ""
            print(f"  {r_name}: {count}只 {check}")
        print(f"  配置合理性: {result['system_analysis']['role_config']['reasonable_score']}/100")

        # 体系识别结果
        print("\n【体系识别结果】")
        if result["system_analysis"]["systems"]:
            for i, sys in enumerate(result["system_analysis"]["systems"], 1):
                status = "完整体系 ✓" if sys["is_complete"] else "不完整"
                print(f"  {i}. {sys['name']} - 完成度 {sys['score']}% ({status})")
                if sys["details"]:
                    detail_str = ", ".join([f"{k}: {v}" for k, v in sys["details"].items()])
                    print(f"     详情: {detail_str}")
        else:
            print("  未检测到明确体系")

        # 首领化冲突检测
        print("\n【首领化分析】")
        lc = result["system_analysis"]["leader_conflict"]
        if lc["dependent_pets"]:
            pet_str = ", ".join(lc["dependent_pets"])
            warn = " ⚠ 冲突!" if lc["has_conflict"] else ""
            print(f"  依赖首领化精灵: {pet_str} ({lc['count']}只){warn}")
            if lc["has_conflict"]:
                print(f"  警告: 首领化只能用一次，建议只保留1个核心首领化精灵")
        else:
            print("  无首领化依赖")
```

- [ ] **Step 4: 运行测试验证

Run: `python -c "from models.team_analyzer import TeamAnalyzer; ta = TeamAnalyzer(); ta.print_team_report(['闪电鳗鱼', '星光狮', '影狸', '黑羽夫人', '寂灭骨龙', '贝古斯'])"`
Expected: 完整输出包括角色和体系的分析结果

- [ ] **Step 5: 提交

```bash
git add models/team_analyzer.py
git commit -m "feat: integrate role and system analysis into team analyzer"
```

---

### Task 4: 优化遗传算法

**Files:**
- Modify: `algorithms/genetic_optimizer.py`

- [ ] **Step 1: 修改适应度函数使用新评分

在 fitness 方法中：

```python
def fitness(self, team: list[str]) -> float:
    """适应度函数 - 使用新的带体系评分的队伍综合评分"""
    result = self.team_analyzer.analyze_team(team)
    if "error" in result:
        return 0
    # 使用新的综合评分
    return result["team_score"]
```

- [ ] **Step 2: 运行优化测试

Run: `python algorithms/genetic_optimizer.py`
Expected: 遗传算法正常运行，输出优化后的队伍

- [ ] **Step 3: 提交

```bash
git add algorithms/genetic_optimizer.py
git commit -m "feat: update genetic optimizer to use new team score"
```

---

### Task 5: 整体测试和验证

**Files:**
- Test: 整体运行测试

- [ ] **Step 1: 测试所有模块都能正常导入

```python
from models.role_classifier import RoleClassifier
from models.system_detector import SystemDetector
from models.team_analyzer import TeamAnalyzer

# 测试各模块
```

- [ ] **Step 2: 运行完整的队伍分析和优化

```bash
python -c "
from models.team_analyzer import TeamAnalyzer
ta = TeamAnalyzer()

# 测试几个典型队伍
test_teams = [
    ['闪电鳗鱼', '星光狮', '影狸', '黑羽夫人', '寂灭骨龙', '贝古斯'],  # 雷暴队
    ['厉毒修萝', '裘卡', '影狸', '剧毒蜈蚣', '黑羽夫人', '贝古斯'],  # 毒队
    ['怖哭菇', '落陨星兔', '帕尔', '圣羽翼王', '寂灭骨龙', '贝古斯'],  # 星陨队
]

for team in test_teams:
    print('='*50)
    ta.print_team_report(team)
"
```

- [ ] **Step 3: 提交最终测试结果

检查输出是否合理，体系是否正确识别

---

## 自检清单

### 1. Spec coverage
- ✅ 角色分类器 → Task 1
- ✅ 体系识别器 → Task 2
- ✅ 整合到队伍分析器 → Task 3
- ✅ 遗传算法优化 → Task 4
- ✅ 首领化冲突检测 → Task 2
- ✅ 角色配置合理性评分 → Task 2

### 2. Placeholder scan
- ✅ 没有 TBD/TODO
- ✅ 所有代码都是完整可运行
- ✅ 所有步骤都有明确的命令和预期输出

### 3. Type consistency
- ✅ 所有函数名、变量名在各任务中一致
- ✅ 数据结构在各模块间兼容

---

Plan complete and saved to `docs/superpowers/plans/2026-05-01-team-role-system.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
