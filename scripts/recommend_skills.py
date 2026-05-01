#!/usr/bin/env python3
"""
精灵最优技能配置推荐系统 v2

改动(v1→v2):
1. 技能池扩展为 learnset + stone + legacy（从 spirits_detail.json 读取）
2. 识别技能负面效果并惩罚（如坟场搏击 敌能量→威力-10%）
3. 环境认知：物防 > 魔防（当前物攻主导环境）
4. 战术性防御技能加分（吓退迫换、报复烧能、无耗费能等）
5. 应对状态输出技能加分（偷袭 应对3倍、吹炎 应对翻倍等）
6. 窄体系依赖技能惩罚（龙噬印记仅适合梦想三三）
"""

import json
import re
from itertools import combinations
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"


def load_skill_scores():
    with open(DATA_DIR / "all_skill_rankings.json") as f:
        return {s["name"]: s for s in json.load(f)}


# ============================================================
# 技能分类函数
# ============================================================

def is_defense(skill):
    return "减伤" in skill.get("desc", "")


def is_offense(skill):
    return skill.get("power", 0) > 0


def is_utility(skill):
    desc = skill.get("desc", "")
    keywords = [
        "驱散", "清减益", "清增益", "焚烧烙印", "食腐", "焚毁",
        "印记", "眩晕", "寄生", "聒噪", "打断应对",
        "转化", "翻倍", "冷却", "天气",
        "将天气改为",
    ]
    return any(kw in desc for kw in keywords)


# ============================================================
# 新增：环境认知评分修正
# ============================================================

def score_defense_quality(skill):
    """
    评估防御技能在当前物攻主导环境下的实际价值
    返回 bonus 或 penalty（可为负）
    """
    score = 0
    desc = skill.get("desc", "")

    if not is_defense(skill):
        return score

    # --- 战术性防御效果（高价值）---
    # 吓退 / 迫换
    if "脱离" in desc and "应对" in desc:
        score += 6  # 应对攻击迫使敌方脱离，战术价值极高
    if "回合结束返场" in desc and "应对" in desc:
        score += 4  # 应对攻击后自己返场

    # 报复 / 烧能量
    if "失去" in desc and "能量" in desc and "应对" in desc:
        score += 5  # 应对攻击烧敌方能量

    # 嗜痛：受击加双攻
    if "双攻+" in desc and "受到伤害" in desc:
        score += 4

    # --- 减伤数值 ---
    m = re.search(r"减伤(\d+)%", desc)
    reduction = int(m.group(1)) if m else 0
    if reduction >= 70:
        score += 2
    elif reduction >= 50:
        score += 0
    else:
        score -= 2  # 减伤太低

    # --- 防御附带效果评估 ---
    # 负面：附带魔防加成（当前物攻环境，魔防价值低）
    if "魔防+" in desc:
        score -= 3
    # 正面：附带物防加成
    if "物防+" in desc:
        score += 3

    # 负面：附加效果是加魔攻（物理精灵加魔攻无用）
    if "魔攻+" in desc:
        score -= 2
    # 正面：附加效果是加物攻
    if "物攻+" in desc:
        score += 2

    # 龙血：可在蓄力状态下使用 + 应对免蓄力（独特机制）
    if "蓄力状态下使用" in desc and "无需蓄力" in desc:
        score += 3

    # 无畏之心：减伤100%但能耗永久+2（双刃剑）
    if "减伤100%" in desc:
        score += 1
    if "能耗永久+" in desc:
        score -= 2

    return score


def detect_skill_downside(skill):
    """
    检测技能的负面效果，返回扣分值（正数=扣分）
    """
    penalty = 0
    desc = skill.get("desc", "")

    # 坟场搏击类：敌方能量越高威力越低
    if re.search(r"敌方每有\d+能量.*威力-\d+%", desc):
        penalty += 10
        # 如果是高能耗大招更糟：敌方能攒能量时威力大打折扣
        if skill.get("cost", 0) >= 4:
            penalty += 3

    # 无畏之心：能耗永久+2
    if "能耗永久+" in desc:
        penalty += 4

    # 蓄力技能（需要蓄力=先手劣势）
    if "蓄力" in desc and "造成" in desc and "无需蓄力" not in desc:
        penalty += 2

    return penalty


def detect_narrow_skill(skill):
    """
    检测窄体系依赖技能，返回扣分值
    这类技能仅在特定队伍中有价值，通用推荐中应惩罚
    """
    penalty = 0
    desc = skill.get("desc", "")
    name = skill.get("name", "")
    cost = skill.get("cost", 0)

    # 龙噬印记：仅梦想三三体系能利用
    if "龙噬" in desc:
        if cost >= 3:
            penalty += 8  # 高费龙噬技能泛用性极差
        else:
            penalty += 4

    # 降灵印记：有一定泛用性（可配合驱散）但非最高优先级
    # 不惩罚，降灵有独立的战术价值

    # 星陨印记：仅星陨队
    if "星陨" in desc and "印记" in desc:
        penalty += 6

    # 借用：随机性太大，不可控
    if name == "借用":
        penalty += 5

    return penalty


def score_speed_buff_synergy(skill, pet_spd):
    """
    评估速度与buff技能的协同
    低速精灵带纯buff技能价值打折：还没来得及用就可能被打残
    """
    penalty = 0
    power = skill.get("power", 0)
    desc = skill.get("desc", "")

    # 只评估纯buff/变化技能（无威力、非防御）
    if power > 0 or is_defense(skill):
        return penalty

    # 低速阈值：速度<90 的精灵带纯buff技能风险高
    if pet_spd < 90:
        # 力量增效类：纯buff，无防御
        if re.search(r"[物魔双]攻\+", desc) and "减伤" not in desc:
            penalty -= 6
        # 其他纯buff
        elif "获得" in desc or "加" in desc or "+" in desc:
            penalty -= 3

    return penalty


def score_reliable_power(skill, pet_atk, pet_matk):
    """
    评估朴实高威力技能的可靠性加分
    高威力、无负面、非蓄力的输出技能在当前物攻环境有稳定价值
    """
    bonus = 0
    power = skill.get("power", 0)
    cost = skill.get("cost", 0)
    desc = skill.get("desc", "")
    category = skill.get("category", "")

    if power <= 0:
        return bonus

    # 是否有负面效果
    has_downside = detect_skill_downside(skill) > 0
    # 是否需要蓄力
    needs_charge = "蓄力" in desc and "无需蓄力" not in desc

    if not has_downside and not needs_charge:
        # 纯粹的稳定输出技能
        efficiency = power / max(cost, 1)

        # 威力效率评分
        if efficiency >= 40:
            bonus += 8
        elif efficiency >= 25:
            bonus += 6
        elif efficiency >= 20:
            bonus += 3

        # 大威力加分（一击打残对面的能力）
        if power >= 130:
            bonus += 6
        elif power >= 100:
            bonus += 3

        # 均衡型：不根据精灵种族值偏向，只看技能本身的稳定输出能力
        if category == "物理":
            bonus += 1  # 当前物攻环境小幅加成，所有精灵都能受益

    return bonus


def score_counter_offense(skill, pet_atk, pet_matk):
    """
    评估输出技能在应对状态下的爆发价值
    """
    bonus = 0
    desc = skill.get("desc", "")
    power = skill.get("power", 0)
    category = skill.get("category", "")

    if power <= 0:
        return bonus

    # 应对状态威力翻倍/多倍（如偷袭3倍、吹炎翻倍）
    m = re.search(r"应对状态.*威力[变改]?为?(\d+\.?\d*)倍", desc)
    if m:
        mult = float(m.group(1))
        if mult >= 3:
            bonus += 8  # 偷袭类：3倍=255威力
        elif mult >= 2:
            bonus += 5  # 吹炎类：翻倍=340威力
        else:
            bonus += 2

    # 应对状态威力+N（如电弧迸发+40）
    m2 = re.search(r"应对状态.*威力\+(\d+)", desc)
    if not m2:
        m2 = re.search(r"迸发.*威力\+(\d+)", desc)
    if m2:
        bonus += 3

    # 穿膛：敌方能量≤2时5倍伤害
    if "能量小于等于" in desc and "倍伤害" in desc:
        m3 = re.search(r"(\d+)倍伤害", desc)
        if m3:
            mult = int(m3.group(1))
            if mult >= 5:
                bonus += 7

    # 先手技能加分（先手+1有价值）
    if "先手+" in desc:
        bonus += 2

    # 迸发技能（可能被队友/天气触发）
    if "迸发" in desc:
        bonus += 2

    return bonus


# ============================================================
# 技能协同（保持原有逻辑）
# ============================================================

def mark_trait_skill_synergy(config, trait_desc, skill_scores):
    bonus = 0
    if not trait_desc:
        return bonus

    if "迸发" in trait_desc:
        for sk in config:
            sk_desc = sk.get("desc", "")
            if "迸发" in sk_desc:
                if "获得所有" in sk_desc:
                    bonus += 12
                else:
                    bonus += 5

    if re.search(r"天气|雨天|晴天|沙暴|暴风雪", trait_desc):
        for sk in config:
            sk_desc = sk.get("desc", "")
            if "将天气改为" in sk_desc:
                bonus += 8

    if "印记" in trait_desc and ("获得" in trait_desc or "赋予" in trait_desc):
        for sk in config:
            sk_desc = sk.get("desc", "")
            if "驱散印记" in sk_desc or ("印记" in sk_desc and ("消耗" in sk_desc or "触发" in sk_desc)):
                bonus += 5

    if "中毒" in trait_desc:
        for sk in config:
            sk_desc = sk.get("desc", "")
            if "中毒" in sk_desc and ("消耗" in sk_desc or "触发" in sk_desc or "提升" in sk_desc):
                bonus += 4

    return bonus


def mark_synergy_score(skill_list, skill_scores):
    synergy = 0
    descs = [s.get("desc", "") for s in skill_list]
    combined = " ".join(descs)

    has_mark_generate = any(
        kw in combined for kw in ["印记", "湿润", "光合", "风起", "龙噬", "降灵", "减速", "蓄电", "蓄势", "星陨", "棘刺"]
    ) and any("获得" in d and ("印记" in d or "层" in d) for d in descs)

    has_mark_consume = any(kw in combined for kw in ["驱散", "焚烧", "焚毁", "食腐", "心灵洞悉"])
    has_mark_trigger = any(kw in combined for kw in ["印记" + n for n in ["触发", "消耗", "引爆"]])

    if has_mark_generate and (has_mark_consume or has_mark_trigger):
        synergy += 8

    has_buff = any(
        re.search(r"[物魔双]攻\+\d+%", d) for d in descs
    ) or any("速度+" in d for d in descs)
    has_attack = any("造成" in d and "物伤" in d or "魔伤" in d for d in descs)
    if has_buff and has_attack:
        synergy += 6

    has_mhapply = any("萌化" in d and "敌方" in d for d in descs)
    has_mhtransfer = any("转移" in d and "萌化" in d for d in descs)
    if has_mhapply and has_mhtransfer:
        synergy += 6

    has_escape = any("脱离" in d for d in descs)
    has_inherit = any(("继承" in d or "传" in d) and "增益" in d for d in descs)
    if has_escape and has_inherit:
        synergy += 5

    has_gain_energy = any("回复" in d and "能量" in d for d in descs)
    has_burn_energy = any(("失去" in d or "偷取" in d) and "能量" in d for d in descs)
    if has_gain_energy and has_burn_energy:
        synergy += 4

    has_atk = any(re.search(r"[物魔双]攻\+", d) for d in descs)
    has_spd = any("速度+" in d for d in descs)
    if has_atk and has_spd:
        synergy += 5

    has_combo = any("连击" in d for d in descs)
    has_consec = any("奉献" in d for d in descs)
    if has_combo and has_consec:
        synergy += 5

    return synergy


# ============================================================
# 组合评分（核心）
# ============================================================

def score_config(config, skill_scores, trait_desc="", pet_data=None):
    """
    对4技能组合评分
    """
    base = 0
    details = {}
    # stats 兼容 spirits_detail(baseLv1) 和 pets_final(stats) 两种格式
    pstats = pet_data.get("stats") or pet_data.get("baseLv1", {}) if pet_data else {}
    pet_atk = pstats.get("atk", 0)
    pet_matk = pstats.get("matk", 0)
    pet_spd = pstats.get("spd", 0)

    for sk in config:
        name = sk.get("name", "")
        sc = skill_scores.get(name, {})
        base += sc.get("score", 0)
        details[name] = sc.get("score", 0)

    penalty = 0

    # ================================================================
    # v2 新增评分维度
    # ================================================================

    # 1. 负面效果惩罚
    downside_penalty = sum(detect_skill_downside(sk) for sk in config)
    penalty -= downside_penalty

    # 2. 窄体系惩罚
    narrow_penalty = sum(detect_narrow_skill(sk) for sk in config)
    penalty -= narrow_penalty

    # 3. 应对状态输出加分
    counter_bonus = sum(score_counter_offense(sk, pet_atk, pet_matk) for sk in config)

    # 3b. 朴实高威力加分
    reliable_bonus = sum(score_reliable_power(sk, pet_atk, pet_matk) for sk in config)

    # 3c. 低速buff惩罚
    speed_buff_penalty = sum(score_speed_buff_synergy(sk, pet_spd) for sk in config)
    penalty += speed_buff_penalty

    # ================================================================
    # 约束检查
    # ================================================================

    defense_count = sum(1 for sk in config if is_defense(sk))
    if defense_count == 0:
        penalty -= 15

    offense_count = sum(1 for sk in config if is_offense(sk))
    if offense_count == 0:
        penalty -= 12

    # 4. 防御技能质量评分（v2 新增：区分物防/魔防/战术防御）
    defense_quality_bonus = sum(score_defense_quality(sk) for sk in config if is_defense(sk))

    # 功能性奖励
    utility_count = sum(1 for sk in config if is_utility(sk))
    utility_bonus = min(utility_count * 2, 8)

    # 天气设置技能加分
    weather_bonus = 0
    for sk in config:
        if "将天气改为" in sk.get("desc", ""):
            weather_bonus += 5

    # 防御过多惩罚
    if defense_count >= 3:
        penalty -= 5

    # 迅捷冗余
    swift_count = sum(1 for sk in config if "迅捷" in sk.get("desc", ""))
    if swift_count >= 2:
        penalty -= (swift_count - 1) * 15

    # 防御冗余：第二个防御技能边际价值大幅递减
    if defense_count >= 2:
        penalty -= (defense_count - 1) * 15

    # 功能冗余
    for cat_pattern in [r"驱散.*印记"]:
        cat_count = sum(1 for sk in config if re.search(cat_pattern, sk.get("desc", "")))
        if cat_count >= 2:
            penalty -= (cat_count - 1) * 8

    # 同属性输出冗余
    atk_by_element = {}
    for sk in config:
        if sk.get("power", 0) > 0:
            el = sk.get("element", "普通")
            atk_by_element[el] = atk_by_element.get(el, 0) + 1
    for el, count in atk_by_element.items():
        if count >= 2:
            penalty -= (count - 1) * 12

    # 输出过多惩罚
    if offense_count >= 4:
        penalty -= 8

    # 技能配合加分
    synergy = mark_synergy_score(config, skill_scores)

    # 特性-技能协同加分
    trait_syn = mark_trait_skill_synergy(config, trait_desc, skill_scores)

    # 多样性奖励
    if defense_count >= 1 and offense_count >= 1 and utility_count >= 1:
        diversity_bonus = 6
    else:
        diversity_bonus = 0

    total = (
        base + utility_bonus + weather_bonus + synergy + trait_syn
        + diversity_bonus + counter_bonus + reliable_bonus + defense_quality_bonus + penalty
    )

    return round(total, 1), {
        "base": round(base, 1),
        "utility_bonus": utility_bonus,
        "weather_bonus": weather_bonus,
        "synergy": synergy,
        "trait_syn": trait_syn,
        "counter_bonus": counter_bonus,
        "reliable_bonus": reliable_bonus,
        "defense_quality": defense_quality_bonus,
        "diversity": diversity_bonus,
        "downside_penalty": -downside_penalty,
        "narrow_penalty": -narrow_penalty,
        "penalty": penalty,
        "defense": defense_count,
        "offense": offense_count,
        "utility": utility_count,
    }


# ============================================================
# 推荐主逻辑
# ============================================================

def pre_rank_skill(sk, skill_scores, pet_atk=0, pet_matk=0, pet_spd=0):
    """单个技能快速启发式评分，用于预筛选"""
    sc = skill_scores.get(sk.get("name", ""), {})
    score = sc.get("score", 0)

    # 惩罚负面效果
    score -= detect_skill_downside(sk) * 2
    # 惩罚窄体系
    score -= detect_narrow_skill(sk) * 2

    # 防御技能根据质量调整
    if is_defense(sk):
        score += score_defense_quality(sk)

    # 朴实高威力加分
    score += score_reliable_power(sk, pet_atk, pet_matk)
    # 应对爆发加分
    score += score_counter_offense(sk, pet_atk, pet_matk)
    # 低速buff惩罚
    score += score_speed_buff_synergy(sk, pet_spd)

    return score


MAX_SKILL_CANDIDATES = 18  # C(18,4) = 3060, 可控


def recommend_for_pet(pet_data, skill_scores):
    """为一个精灵推荐最优技能配置"""
    skills_data = pet_data.get("skills", {})
    learnset = skills_data.get("learnset", [])
    other = skills_data.get("other", [])

    all_skills = learnset + other
    if not all_skills:
        return None

    trait_desc = pet_data.get("trait", {}).get("desc", "")

    if len(all_skills) < 4:
        return {
            "skills": all_skills,
            "score": 0,
            "meta": {},
        }

    # 预筛选：按启发式评分排序，取前 MAX_SKILL_CANDIDATES 个
    pet_atk = pet_data.get("baseLv1", {}).get("atk", 0) or pet_data.get("stats", {}).get("atk", 0)
    pet_matk = pet_data.get("baseLv1", {}).get("matk", 0) or pet_data.get("stats", {}).get("matk", 0)
    pet_spd = pet_data.get("baseLv1", {}).get("spd", 0) or pet_data.get("stats", {}).get("spd", 0)
    if len(all_skills) > MAX_SKILL_CANDIDATES:
        ranked = sorted(all_skills, key=lambda sk: -pre_rank_skill(sk, skill_scores, pet_atk, pet_matk, pet_spd))
        candidates = ranked[:MAX_SKILL_CANDIDATES]
    else:
        candidates = all_skills

    best_score = -9999
    best_config = None
    best_meta = None

    for combo in combinations(candidates, 4):
        score, meta = score_config(list(combo), skill_scores, trait_desc, pet_data)
        if score > best_score:
            best_score = score
            best_config = list(combo)
            best_meta = meta

    return {
        "skills": best_config,
        "score": best_score,
        "meta": best_meta,
    }


def main():
    # 从 spirits_detail.json 读取完整技能数据
    with open(DATA_DIR / "spirits_detail.json") as f:
        spirits = json.load(f)

    # 补充：加载 pets.json 中不在 spirits_detail 的宠物
    with open(DATA_DIR / "pets.json") as f:
        pets_data = json.load(f)
    with open(DATA_DIR / "pet_learnset.json") as f:
        learnset_data = json.load(f)

    existing_names = {pd.get("name", "") for pd in spirits.values()}
    added_count = 0
    for name, pet_info in pets_data.items():
        if name in existing_names:
            continue
        ls = learnset_data.get(name, [])
        if not ls:
            continue
        st = pet_info.get("stats", {})
        spirits[f"__pets__{name}"] = {
            "name": name,
            "attrs": pet_info.get("attrs", []),
            "stats": st,
            "baseLv1": st,
            "trait": pet_info.get("trait", {}),
            "skills": {
                "learnset": ls,
                "other": [],
            },
        }
        added_count += 1
    if added_count:
        print(f"已从 pets.json 补充 {added_count} 个不在 spirits_detail 中的精灵")

    skill_scores = load_skill_scores()

    results = {}
    pet_count = 0
    for pet_id, pet_data in spirits.items():
        name = pet_data.get("name", "")
        if not name:
            continue
        skills_data = pet_data.get("skills", {})
        if not skills_data.get("learnset") and not skills_data.get("other"):
            continue
        pet_count += 1

        rec = recommend_for_pet(pet_data, skill_scores)
        if rec:
            results[name] = {
                "name": name,
                "skills": [sk["name"] for sk in rec["skills"]],
                "score": rec["score"],
                "meta": rec["meta"],
            }

    # 保存推荐结果（按宠物排名排序）
    # 读取宠物排名
    try:
        with open(DATA_DIR / "all_pet_rankings.json") as f:
            pet_rankings = {r["name"]: i for i, r in enumerate(json.load(f))}
    except Exception:
        pet_rankings = {}

    rec_map = {}
    for pet_id, pet_data in spirits.items():
        name = pet_data.get("name", "")
        if name not in results:
            continue
        rec_skill_names = results[name]["skills"]
        all_skills = pet_data["skills"].get("learnset", []) + pet_data["skills"].get("other", [])
        skill_list = []
        for skname in rec_skill_names:
            for sk in all_skills:
                if sk["name"] == skname:
                    skill_list.append(sk)
                    break
        rec_map[name] = skill_list

    # 按宠物排名排序（高分在前）
    sorted_names = sorted(rec_map.keys(), key=lambda n: pet_rankings.get(n, 9999))
    sorted_map = {n: rec_map[n] for n in sorted_names}

    with open(DATA_DIR / "pet_recommended.json", "w") as f:
        json.dump(sorted_map, f, ensure_ascii=False, indent=2)
    print(f"已从 spirits_detail.json 读取 {pet_count} 个精灵")
    print(f"已为 {len(results)} 个精灵推荐技能配置，保存到 pet_recommended.json（按宠物排名排序）")

    # 输出摘要
    print("\n========== 推荐摘要 ==========")
    for name in sorted(results.keys(), key=lambda x: -results[x]["score"])[:20]:
        r = results[name]
        m = r["meta"]
        print(f"{r['name']:20s} 评分={r['score']:6.1f} "
              f"(基础={m['base']} 配合={m['synergy']} 应对={m.get('counter_bonus', 0)} "
              f"可靠={m.get('reliable_bonus', 0)} "
              f"防质={m.get('defense_quality', 0)} "
              f"负面={m.get('downside_penalty', 0)} 窄={m.get('narrow_penalty', 0)} "
              f"罚={m['penalty']})")
        print(f"  技能: {', '.join(r['skills'])}")

    # 特别输出寂灭骨龙
    if "寂灭骨龙" in results:
        r = results["寂灭骨龙"]
        m = r["meta"]
        print(f"\n========== 寂灭骨龙 详情 ==========")
        print(f"  技能: {', '.join(r['skills'])}")
        print(f"  评分: {r['score']}")
        print(f"  明细: 基础={m['base']} 配合={m['synergy']} 应对={m.get('counter_bonus', 0)} "
              f"可靠={m.get('reliable_bonus', 0)} "
              f"防质={m.get('defense_quality', 0)} 负面={m.get('downside_penalty', 0)} "
              f"窄={m.get('narrow_penalty', 0)} 罚={m['penalty']}")

    # 统计
    stats = {"has_defense": 0, "has_offense": 0, "has_utility": 0, "has_synergy": 0}
    for r in results.values():
        if r["meta"]["defense"] > 0: stats["has_defense"] += 1
        if r["meta"]["offense"] > 0: stats["has_offense"] += 1
        if r["meta"]["utility"] > 0: stats["has_utility"] += 1
        if r["meta"]["synergy"] > 0: stats["has_synergy"] += 1
    total = len(results)
    print(f"\n覆盖率: 防御={stats['has_defense']}/{total} "
          f"输出={stats['has_offense']}/{total} "
          f"功能={stats['has_utility']}/{total} "
          f"配合={stats['has_synergy']}/{total}")


if __name__ == "__main__":
    main()
