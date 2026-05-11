#!/usr/bin/env python3
"""
特性量化评分 + 精灵重排 + 最优队伍组建

特性评分原则: 与技能评分同源，按效果量化
"""

import json, re, sys
from pathlib import Path
from itertools import combinations

DATA_DIR = Path(__file__).parent.parent / "data"
SCRIPT_DIR = Path(__file__).parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# 引入技能评分的威力公式，用于动态技能分计算
from score_all_skills import power_value

# ===== 属性 × 种族 综合战斗能力评分 =====
_ATTR_CHART = None
_SHORT_MAP = {}  # 属性名已统一("幽"/"恶"/"普通")，不再需要映射

def _load_chart():
    global _ATTR_CHART
    if _ATTR_CHART is None:
        with open(DATA_DIR / "attribute_chart.json") as f:
            _ATTR_CHART = json.load(f)
    return _ATTR_CHART

def _norm(a):
    return _SHORT_MAP.get(a, a)

IV = 10  # 个体值 (7~10，取10)

def base_to_actual(base_stat, nature_mod=0.0, is_hp=False):
    """
    生命: (1.7*(种族 + 3*个体) + 70) × (1 + 性格修正) + 100
    其他: (1.1*(种族 + 3*个体) + 10) × (1 + 性格修正) + 50
    计算细则：系数×种族部分先四舍五入，整体算完再四舍五入一次
    """
    if is_hp:
        step1 = round(1.7 * (base_stat + 3 * IV))  # 第一次四舍五入
        raw = (step1 + 70) * (1 + nature_mod) + 100
        return round(raw)  # 第二次四舍五入
    else:
        step1 = round(1.1 * (base_stat + 3 * IV))  # 第一次四舍五入
        raw = (step1 + 10) * (1 + nature_mod) + 50
        return round(raw)  # 第二次四舍五入

# 攻击属性热门度分级
_HOT_T1 = {"普通", "翼", "水", "地", "机械", "火"}
_HOT_T2 = {"光", "恶", "冰", "电", "武"}

def _atk_weight(atk_type):
    if atk_type in _HOT_T1: return 2.0
    if atk_type in _HOT_T2: return 1.5
    return 1.0

def calc_combat_score(attrs, stats, rec_skills=None, trait_name="", speed_percentile=None):
    """
    属性×种族值 综合战斗能力评分：攻击能力 + 防御能力 + 速度线
    攻击分基于伤害公式：技能威力 × 对应攻击力 × 本系加成
    speed_percentile: 速度超越比例 (0~1)，用于百分位加权速度分
    返回 (total, breakdown)
    """
    if not attrs:
        return 0, {"attack": 0, "defense": 0, "speed": 0}

    chart = _load_chart()
    normalized = [_norm(a) for a in attrs]

    # ---- 属性 matchup 计算 ----
    atk_vs = {}
    for e in chart["attributes"]:
        atk_n = e["nameCn"]
        atk_vs[atk_n] = {}
        for ms, targets in e["battleMultiplier"]["offense"].items():
            for t in targets:
                atk_vs[atk_n][t] = float(ms)

    # 属性联防面 (0~100)
    # 双属性实际倍率 = 两个属性的倍率相乘（不是取min）
    defense_raw = 0
    for atk_t in atk_vs:
        combined_m = 1.0
        for def_t in normalized:
            m = atk_vs[atk_t].get(def_t, 1.0)
            combined_m *= m
        w = _atk_weight(atk_t)
        if combined_m <= 0.5:
            defense_raw += 5 * w    # 抵抗
        elif combined_m <= 0.75:
            defense_raw += 2 * w    # 小抵抗（0.5x 但倍率 <0.75）
        elif combined_m >= 4.0:
            defense_raw -= 8 * w    # 四倍弱点
        elif combined_m >= 2.0:
            defense_raw -= 5 * w    # 弱点
    type_defense = max(defense_raw, 0)  # 差属性=0分，不扣分

    # 属性打击面：精灵属性 + 携带技能的属性（技能打击面）
    offense_attrs = set(normalized)
    if rec_skills:
        for sk in rec_skills:
            power = sk.get("power", 0) if isinstance(sk, dict) else 0
            if power > 0:
                offense_attrs.add(sk.get("element", "普通") if isinstance(sk, dict) else "普通")
    stab_se = set()
    stab_resist = set()
    for atk_t in offense_attrs:
        for def_t, m in atk_vs.get(atk_t, {}).items():
            if m >= 2:
                stab_se.add(def_t)
            elif m <= 0.5:
                stab_resist.add(def_t)
    # 无免疫，所有属性均可命中，抵抗仅降低效率
    type_offense = max(18 * 1.0 + len(stab_se) * 1.5 - len(stab_resist) * 1.0 + 3, 0)

    # 属性协同 (0~25)
    if len(normalized) == 2:
        t1, t2 = normalized
        weak, resist = {}, {}
        for t in [t1, t2]:
            wset, rset = set(), set()
            for atk_t in atk_vs:
                m = atk_vs[atk_t].get(t, 1.0)
                if m >= 2: wset.add(atk_t)
                elif m <= 0.5: rset.add(atk_t)
            weak[t] = wset; resist[t] = rset
        covered = set(); dup = set()
        for w in weak[t1]:
            if w in resist[t2]: covered.add(w)
            elif w in weak[t2]: dup.add(w)
        for w in weak[t2]:
            if w in resist[t1]: covered.add(w)
            elif w in weak[t1]: dup.add(w)
        type_synergy = len(covered) * 5.0 - len(dup) * 4.0
    else:
        type_synergy = 3

    # ---- 种族值维度 ----
    hp = stats.get("hp", 80)
    atk = stats.get("atk", 80)
    matk = stats.get("matk", 80)
    def_ = stats.get("def", 80)
    mdef = stats.get("mdef", 80)
    spd = stats.get("spd", 80)

    # 计算实际速度值（默认+20%性格修正，对应高速输出手）
    actual_spd = base_to_actual(spd, 0.2)

    # 1. 攻击能力: 种族攻击力 × 本系加成（不重复算技能威力，技能威力归 skill_score）
    # 检查是否有对应类型攻击技能
    actual_atk = base_to_actual(atk, 0.2)
    actual_matk = base_to_actual(matk, 0.2)
    STAT_SCALE = 0.2
    STAB = 1.25

    has_phys = False
    has_phys_stab = False
    has_spec = False
    has_spec_stab = False
    if rec_skills:
        for sk in rec_skills:
            power = sk.get("power", 0) if isinstance(sk, dict) else 0
            if power <= 0:
                continue
            category = sk.get("category", "") if isinstance(sk, dict) else ""
            element = sk.get("element", "普通") if isinstance(sk, dict) else "普通"
            if "物理" in category:
                has_phys = True
                if element in attrs:
                    has_phys_stab = True
            else:
                has_spec = True
                if element in attrs:
                    has_spec_stab = True

    phys_atk_score = round(actual_atk * STAT_SCALE * (STAB if has_phys_stab else 1.0), 1) if has_phys else 0
    spec_atk_score = round(actual_matk * STAT_SCALE * (STAB if has_spec_stab else 1.0), 1) if has_spec else 0
    # 取物攻和特攻中更高的，代表精灵的最佳攻击能力
    atk_stat = max(phys_atk_score, spec_atk_score)
    # 攻击分 = 种族攻击 + 技能打击面
    type_atk = type_offense * 0.6
    attack_score = round(atk_stat + type_atk, 1)

    # 2. 防御能力: 基于伤害公式，拆分为物防/特防分（纯种族值）
    # 伤害 ∝ 攻击÷防御，能承受的总伤害 ∝ HP×防御
    # 物攻环境主导 (65%物理，35%特殊)
    scale = 180
    phys_bulk = hp * def_ / scale
    spec_bulk = hp * mdef / scale
    phys_def_score = round(phys_bulk * 0.65, 1)
    spec_def_score = round(spec_bulk * 0.35, 1)
    defense_score = round(phys_def_score + spec_def_score, 1)  # 纯种族防御分

    # 3a. 属性得分：联防面 × 物防面折扣 + 协同（脆皮带不动好属性）
    type_def = type_defense  # 不压缩，让好属性充分体现
    type_syn = max(type_synergy, 0)  # 协同不惩罚（弱协同=0分）
    # 物防面折扣：phys_bulk = HP × 防御，低于10000的精灵联防分打折
    phys_bulk = hp * def_
    bulk_factor = min(1.0, pow(max(phys_bulk / 10000, 0), 0.5))
    type_def_effective = round(type_def * bulk_factor, 1)
    attr_score = round(type_def_effective + type_syn, 1)

    # 3b. 速度分：百分位加权 — 速度价值取决于超越多少精灵
    # 低于50%分位 = 0分；50%~100%分位线性映射到 0~120
    MAX_SPEED = 120
    if speed_percentile is not None and speed_percentile > 0.5:
        excess = speed_percentile - 0.5  # 0 ~ 0.5
        speed_score = round(excess * 2 * MAX_SPEED, 1)
        speed_line = spd
    elif speed_percentile is not None:
        speed_score = 0
        speed_line = 0
    else:
        # 回退：无百分位数据时使用旧公式
        if spd >= 100:
            speed_score = round(pow(max(spd - 98, 0), 1.5) * 0.32, 1)
            speed_line = spd
        else:
            speed_score = 0
            speed_line = 0

    # 无权重：直接相加，一视同仁
    total = round(attack_score + defense_score + speed_score + attr_score, 1)
    return total, {
        "attack": attack_score,
        "phys_atk": round(phys_atk_score, 1),
        "spec_atk": round(spec_atk_score, 1),
        "defense": defense_score,
        "phys_def": phys_def_score,
        "spec_def": spec_def_score,
        "speed": speed_score,
        "speed_line": speed_line,
        "actual_spd": actual_spd,
        "attr_score": attr_score,
        "type_atk": round(type_atk, 1),
        "type_def": round(type_def, 1),
        "type_def_effective": type_def_effective,
        "type_syn": round(type_syn, 1),
        "bulk_factor": round(bulk_factor, 2),
    }


def _find_int(pattern, text, default=0):
    m = re.search(pattern, text)
    return int(m.group(1)) if m else default


def _cond_mult(desc):
    """返回条件折扣系数：硬条件(天气/环境/队友)折扣最大，软条件(自身行动)中等"""
    if not desc:
        return 1.0
    # 硬条件: 需要天气/环境/队友支持/击败触发
    if re.search(r"天气|雨天|晴天|沙暴|暴风雪|环境|水系环境|己方队伍|队友|队伍中|主动击败", desc):
        return 0.35
    # 中等条件: 需要自身行动触发（应对/击败/入场/离场/每携带等）
    if re.search(r"应对成功|主动击败|入场|离场|每携带\d|每拥有|每回合|受到|若", desc):
        return 0.55
    # 轻微条件: 力竭(几乎必然发生)、更换等
    if re.search(r"力竭|更换", desc):
        return 0.85
    return 1.0


# ===== 特性手工评分表 =====
# 直接按特性名查分，分数已综合考量效果强度、触发条件、队伍依赖等因素
TRAIT_SCORES = {
    # ── S档：顶级特性 (>150) ──
    "化茧": 250,       # 2次免死+萌化 — 游戏最强生存特性
    "不朽": 200,       # 力竭3回合后复活 — 可预知第二条命

    # ── A+档：极强特性 (150-160) ──
    "飓风": -10,       # 被击败额外损失魔力（迅捷价值已融入技能得分）
    "付给恶魔的赎价": 160,  # 击杀多扣敌方魔力，进攻端极强

    # ── A档：强力特性 (100-140) ──
    "冰钻": 130,       # 敌方每1能耗→威力+10%，被动增伤极强
    "悬一线": 120,     # 1次免死+眩晕敌方1回合
    "先知": 100,       # 速度+50 + 攻击+50%

    # ── B档：优秀特性 (60-100) ──
    "哨兵": 90,        # 速度+50 + 行动后脱离
    "夺目": 85,        # 额外3个随机技能，非光系技能威力+25%
    "大捞一笔": 80,    # 回合结束时偷取所有敌方精灵2点能量
    "爆燃": 80,        # 使用火系技能后双攻永久+30%
    "守望星": 80,      # 星陨半消耗满伤害
    "电流刺激": 80,    # 攻击技能迸发威力+40
    "圣火骑士": 75,    # 应对成功后下次攻击威力翻倍
    "煤渣草": 70,      # 灼烧衰减→增长
    "预警": 70,        # 速度+50
    "悼亡": 70,        # 双方每1只力竭精灵→双攻+30%(终局极强)
    "御驾亲征": -5,    # 大幅提升种族值，力竭扣4魔力(分数已经加到种族值里，因为一般都是最后一个出场负面较为可控)
    "吟游之弦": 10,    # 印记共存
    "游弋": 65,        # 蓄力时可使用任意技能+双防+100%
    "坠星": 65,        # 敌每层星陨印记→技能威力+15%
    "棋王契约": -5,    # 棋类体系核心
    "全神贯注": 60,    # 入场首回合物攻+100%，每次衰减20%
    "三鼓作气": 60,    # 使用能耗3技能→攻防永久+20%
    "营养液泡": 60,    # 增益额外+2层
    "珊瑚骨": 60,      # 敌方离场→全技能能耗-3(不可驱散)

    # ── C档：实用特性 (30-60) ──
    "贪心算法": 55,    # 传动1 + 6层灼烧
    "得寸进尺": 55,    # 雨天双攻+100%
    "保守派": 55,      # 低总能耗时双防+80%
    "破空": 55,        # 先手时威力+75%
    "蚀刻": 55,        # 中毒→印记转化
    "养分内循环": 55,  # 回合结束获得6能量
    "超级电池": 55,    # 每入场1次→双攻永久+30%
    "浪潮": 55,        # 使用水系技能后全技能能耗-2
    "伙伴的力量": 55,  # 不同系别技能→攻防+5%，光系+20%
    "\u201c国王\u201d的威严": 55,  # 种族值大幅增加+低耗技能威力+50%
    "快锤": 50,        # 低耗技能获得迅捷
    "生物电": 50,      # 电系技能迸发能耗-2
    "洄游": 10,        # 蓄力→全技能能耗-1
    "自由飘": 50,      # 萌化→连击数
    "吸积盘": 50,      # 回合结束敌方2层星陨
    "绒粉星光": 50,    # 非本系血脉→威力+100%
    "天通地明": 50,    # 污染血脉→威力+100%
    "月光审判": 50,    # 首领血脉→威力+100%
    "盲从": 45,        # 可多个随机技能，非幻系能耗-2
    "灵魂灼伤": 45,    # 冰→灼烧, 火→冻结
    "嫁祸": 45,        # 失血→连击
    "侵蚀": 45,        # 中毒→连击
    "暴食": 45,        # 龙系技能迅捷
    "翼轴": 45,        # 1号位迅捷+传动
    "整点报时": 45,    # 初始位置传动能耗-5
    "吸灵": 45,        # 继承阵亡队友最高属性
    "冰封": 45,        # 敌方全技能能耗+1
    "抓到你了": 50,    # 入场敌2层冻结+冻结时敌全能耗+1
    "下黑手": 40,      # 敌方离场→5层中毒
    "缩壳": 40,        # 防御技能能耗-2
    "思维之盾": 40,    # 应对后能耗-5
    "虫群突袭": 45,    # 每只虫系队友→入场攻防速+15%
    "深层氧循环": 40,  # 使用草系技能后回复15%生命
    "水翼飞升": 40,    # 队友水系→入场全能耗-1，0能耗威力+30%
    "快充": 40,        # 离场回10能量
    "衡量": 40,        # 复制敌方增益+持续复制
    "斗技": 40,        # 应对后全技能威力+20
    "做噩梦": 40,      # 敌方离场→失去3能量
    "绝对秩序": 40,    # 非系别伤害-50%
    "复方汤剂": 40,    # 中毒触发次数+1
    "向心力": 40,      # 1/2号位传动+威力30
    "勇敢": 35,        # 高耗技能威力+40%
    "贪婪": 35,        # 继承增益减益(需队伍配合)
    "乘风连击": 35,    # 翼系技能后连击+1
    "威慑": 35,        # 打断后冷却+2
    "壮胆": 35,        # 有虫系队友→双攻+50%
    "毒蘑菇": 35,      # 回合结束偷1能量
    "起飞加速": 35,    # 首次技能迅捷
    "盲拧": 35,        # 4号位能耗-4
    "美拉德反应": 35,  # 离场后队友双攻+20%免疫灼烧
    "高浓生物碱": 35,  # 使用技能→敌方2层中毒
    "囤积": 35,        # 每能量双防+10%
    "月牙雪糕": 35,    # 冻结视为星陨印记
    "张弛有度": 35,    # 周末攻/平时防+40%
    "恶魔的晚宴": 35,  # 击败后双攻+50%(条件苛刻)
    "恶魔红钻": 35,    # 击败后队伍5次奉献
    "地脉馈赠": 35,    # 入队回10能+队友地系→回3能
    "石天平": 30,      # 能耗差惩罚敌方
    "扩散侵蚀": 30,    # 水系后敌方中毒(印记2倍)
    "碰瓷": 30,        # 恶系后敌方失2能量
    "多人宿舍": 30,    # 能量超上限
    "挺起胸脯": 30,    # 低耗技能威力+50%
    "顺风": 30,        # 先手威力+50%
    "消波块": 30,      # 水系→地系能耗-1
    "溶解腐蚀": 30,    # 毒系→水系中毒2层
    "溶解扩散": 30,    # 毒系→水系中毒1层
    "特殊清洁场景": 80, # 回合结束偷1层印记
    "花精灵": 30,      # 回合结束队伍1次奉献
    "野性感官": 30,    # 应对后先手+1
    "生长": 30,        # 回合结束回12%生命
    "渴求": 30,        # 入场50%吸血
    "格斗小五": 30,    # (擒拿) 攻击应对回血25%
    "星云旅者": 30,    # (穹顶之下) 攻击时印记→星陨
    "格兰球": 30,      # (生长) 回合结束回12%生命
    "卡瓦重": 30,      # (诈死) 力竭少损失1魔力
    "擒拿": 30,        # 攻击应对回血25%
    "穹顶之下": 30,    # 攻击时印记→星陨
    "诈死": 30,        # 力竭少损失1魔力

    # ── D档：较弱特性 (10-30) ──
    "防过载保护": 25,  # 每次行动后脱离
    "警惕": 25,        # 能量为0时脱离
    "流浪鼠": 25,      # (奔波命) 使用防御后脱离
    "奔波命": 25,
    "木桶戏法": 25,    # 离场后队友木桶登场
    "星地善良": 25,    # 队友0能量自动替换
    "渗透": 25,        # 队友武/地系→入场攻防+5%
    "身经百练": 25,    # 应对→入场水/武威力+20%
    "冻土": 25,        # 冰系→地系威力+10%
    "机械变式": 25,    # 技能位置变化→能耗-1
    "散热": 25,        # 初始0能量，火系回3能
    "打雪仗": 25,      # 初始0能量，冰系回3能
    "慢热型": 25,      # 初始0能量，应对回5能
    "超负荷": 25,      # 攻击迸发→敌全技能能耗+1
    "超聚能": 25,      # 蓄力转化
    "连续负荷": 5,    # 迸发延长1回合
    "马步": 25,        # 状态应对回10能
    "暗流": 25,        # 与下只精灵换血量百分比
    "蒸汽膨胀": 25,    # 队友火系→入场全技能威力+10
    "坚韧铠甲": 25,    # 受击→队伍1次奉献
    "栗子壳": 25,      # 被攻击→敌方棘刺印记
    "刺肤": 25,        # 被攻击→反伤
    "四轴机床": 25,    # 新4号位技能能耗-3
    "咔咔冲刺": 25,    # 先手行动后连击+1
    "急性子": 25,      # 连击→2层灼烧
    "受身": 25,        # 敌方换宠→全属性+100%
    "格斗小八": 25,    # (受身)
    "变形活画": 20,    # 敌方增益→威力+10%
    "仁心": 20,        # 灼烧伤害→回血
    "耐活王": 20,      # (仁心类) 中毒伤害→回血
    "夜枭": 20,        # (搜刮) 敌方聚能→入场魔攻+20%
    "搜刮": 20,
    "陨落": 20,        # 双方回合结束触发-1
    "古卷匣魔像": 20,  # (构装契约者) 条件双防+50%
    "构装契约者": 20,
    "深蓝鲸": 20,      # (倾轧) 能耗变化效果翻倍
    "倾轧": 20,
    "泛音列": 20,      # 状态技能→敌方聒噪
    "石头大餐": 20,    # 能量不足→耗血代能量
    "晶体蜗": 20,      # (完全偏振) 抵抗携带技能系别
    "完全偏振": 20,
    "噼啪！": 20,      # (噼啪鸟) 入场首次行动+1次数
    "逐魂鸟": 15,      # 低耗攻击技免自伤
    "窃光蚊": 15,      # (血型吸引) 敌方系别→威力+
    "血型吸引": 15,
    "兽花蕾": 15,      # (稀兽花宝) 血脉决定入场效果
    "稀兽花宝": 15,
    "拨浪鼓": 15,      # 队友状态→入场毒/萌威力+10
    "春花兔": 15,      # (系统发育) 回能/回血分给队友
    "系统发育": 15,
    "伊贝粉粉": 15,    # (腐植循环) 回能同时回血5%
    "腐植循环": 15,
    "定向精炼": 15,    # 队友防御→入场技能威力+10%
    "契约的形状": 15,  # 咕噜球品质→全属性提升
    "间歇式训练": 15,  # 武系后物攻+20%速度-10
    "毒牙": 15,        # 中毒时附加魔攻魔防-40%
    "加个雪球": 15,    # 冻结时附加2层冻结
    "凡鹰": 15,        # (先锋) 普通系→翼系
    "先锋": 15,
    "无差别过滤": 10,  # 所有精灵连击=2 (可能负面)
    "共鸣": 10,        # 虫鸣威力+20
    "不移": 10,        # 无额外效果攻击技能威力+30%
    "啾啾冲刺": 10,    # 先手→连击+1

    # ── 首领基础形态特性 (弱于首领版本) ──
    "专注力": 55,      # 入场首回合物攻+100% (无衰减版)
    "捉到你了": 45,    # 入场敌2层冻结+冻结时敌全能耗+2
    "悲悯": 40,        # 己方每1只力竭→双攻+30%
    "助燃": 35,        # 使用火系技能后双攻+20%
    "蓄电池": 35,      # 每入场1次→双攻永久+20%
    "小偷小摸": 35,    # 入场时偷取所有敌方精灵2能量
    "最好的伙伴": 30,  # 克制伤害后→攻防速+20%+回2能
    "嫉妒": 30,        # 蓄力时可使用任意技能
    "养分重吸收": 30,  # 回合结束回复3能量
    "观星": 30,        # 敌每层星陨→地系技能威力+15%
    "虫群鼓舞": 30,    # 每只虫系队友→入场攻防速+10%
    "生物碱": 25,      # 使用草系技能时敌获2层中毒
    "水翼推进": 25,    # 队友水系→入场全能耗-1
    "浸润": 25,        # 使用水系技能后全能耗-1
    "目空": 25,        # 非光系技能威力+25%
    "鼓气": 25,        # 使用能耗3技能→攻防+20%
    "氧循环": 25,      # 使用草系后回复10%生命
    "地脉": 20,        # 初始0能量，队友地系→回3能量
    "捉迷藏": 20,      # 冻结时敌全能耗+1
    "偏振": 15,        # 受携带技能系别伤害-40%

    # ── E档：微弱特性 (0-10) ──
    "生机": 10,        #
    "噼啪鸟": 10,      #
    "咔咔鸟": 10,
    "烈火守护": 10,    # (蒸汽膨胀) 队友火系→入场全技能威力+10
    "斑枭": 10,
    "幽冥眼": 10,      # (惊吓) 0能量精灵无法伤己
    "惊吓": 10,
    "寒音蛇": 10,
    "起源钟": 10,
    "溯源钟": 0,       # 已在上面有整点报时=45
    "啾啾鸟": 10,
    "圆号鱼": 10,
    "健猫教练": 10,
    "机甲小子": 10,
    "月亮砣": 10,
    "火羽": 10,
    "壳栗丝鼠": 10,
    "风滚暮虫": 10,
    "格斗小六": 10,
    "红绒十字": 10,
    "邪眼巨魔": 10,
    "陨星虫": 10,
    "罗隐": 10,
    "利灯鱼": 10,      # (对流) 能耗增减反转
    "对流": 10,

    # ── 棋类变身 (个体不强，体系价值在棋王契约) ──
    "腾挪": 0,         # 攻击应对后变身棋绮后
    "保卫": 0,         # 防御应对后变身棋绮后
    "好象坏象": 0,     # 状态应对后变身棋绮后

    # ── 圣剑系列 (技能位限制，暂不扣分) ──
    "正位宝剑": 0,     # 仅1号位技能
    "宝剑王牌": 0,     # 仅1/3号位技能

    # ── 负面/代价特性 ──
    "铃兰晚钟": -10,   # 入场失去一半生命
    "虚假宝箱": -10,   # 力竭时敌方攻防+20%
    "留学生": -5,      # 全技能能耗+2，但可学全部攻击技
    "守护者": 30,      # 队友萌化→入场全技能能耗-1
    "振奋虫心": 35,    # 主动击败→队伍5次奉献(与恶魔红钻同)
    "无忧无虑": 30,    # 萌化层数不受限制
    "毒腺": 40,        # 低能耗技能→敌方4层中毒
}

def score_trait(trait_name, desc="", team_skill_stats=None, pet_name=""):
    """
    特性评分：优先查手工评分表，未收录的返回0
    team_skill_stats: 队伍技能统计
    返回 (score, breakdown)

    动态特性（按队友技能数计分）：
    - 棋绮后/棋契陛下(渗透): 地系/武系技能数
    - 瞌睡王(慢热型): 应对技能数
    - 海豹船长(身经百练): 应对技能数
    - 烈火守护(蒸汽膨胀): 火系技能数
    - 火焰猿(散热): 火系技能数
    - 雪巨人(打雪仗): 冰系技能数
    - 布克棱岩等(地脉): 地系技能数
    - 迷嶂布莱克(地脉馈赠): 地系技能数
    - 神谕鲨(水翼飞升): 水系技能数
    - 风铃鲨等(水翼推进): 水系技能数
    - 波多西(定向精炼): 防御技能数
    - 寒音蛇(拨浪鼓): 状态技能数
    """
    static_base = TRAIT_SCORES.get(trait_name, 0)  # 精灵排序时的默认分
    in_team = team_skill_stats is not None

    if not in_team:
        return static_base, {"base": static_base}

    # ── 以下为组队上下文（动态特性基础分归0，仅计动态部分）──

    # 棋绮后/棋契陛下动态加分: 每有1个地系/武系技能 +10分
    if "棋绮后" in pet_name or "棋王后" in pet_name or "棋契陛下" in pet_name or trait_name in ("渗透", "腾挪", "保卫", "御驾亲征"):
        dynamic_bonus = (team_skill_stats.get("earth", 0) + team_skill_stats.get("martial", 0)) * 10
        return dynamic_bonus, {"base": 0, "dynamic_bonus": dynamic_bonus}

    # 瞌睡王动态加分: 每有1个应对技能 +10分
    if "瞌睡王" in pet_name or trait_name == "慢热型":
        dynamic_bonus = team_skill_stats.get("counter", 0) * 10
        return dynamic_bonus, {"base": 0, "dynamic_bonus": dynamic_bonus}

    # 海豹船长动态加分: 每有1个应对技能 +10分
    if "海豹船长" in pet_name or trait_name == "身经百练":
        dynamic_bonus = team_skill_stats.get("counter", 0) * 10
        return dynamic_bonus, {"base": 0, "dynamic_bonus": dynamic_bonus}

    # 烈火守护动态加分: 每有1个火系技能 +10分
    if "烈火守护" in pet_name or trait_name == "蒸汽膨胀":
        dynamic_bonus = team_skill_stats.get("fire_skills", 0) * 10
        return dynamic_bonus, {"base": 0, "dynamic_bonus": dynamic_bonus}

    # 火焰猿动态加分: 每有1个火系技能 +10分
    if "火焰猿" in pet_name or trait_name == "散热":
        dynamic_bonus = team_skill_stats.get("fire_skills", 0) * 10
        return dynamic_bonus, {"base": 0, "dynamic_bonus": dynamic_bonus}

    # 雪巨人动态加分: 每有1个冰系技能 +10分
    if "雪巨人" in pet_name or trait_name == "打雪仗":
        dynamic_bonus = team_skill_stats.get("ice_skills", 0) * 10
        return dynamic_bonus, {"base": 0, "dynamic_bonus": dynamic_bonus}

    # 布克棱岩/布是石/布是岩动态加分: 每有1个地系技能 +10分
    if trait_name == "地脉":
        dynamic_bonus = team_skill_stats.get("earth", 0) * 10
        return dynamic_bonus, {"base": 0, "dynamic_bonus": dynamic_bonus}

    # 迷嶂布莱克动态加分: 每有1个地系技能 +10分
    if trait_name == "地脉馈赠":
        dynamic_bonus = team_skill_stats.get("earth", 0) * 10
        return dynamic_bonus, {"base": 0, "dynamic_bonus": dynamic_bonus}

    # 神谕鲨动态加分: 每有1个水系技能 +10分
    if trait_name == "水翼飞升":
        dynamic_bonus = team_skill_stats.get("water_skills", 0) * 10
        return dynamic_bonus, {"base": 0, "dynamic_bonus": dynamic_bonus}

    # 风铃鲨/蓝蝶鲨/彩蝶鲨动态加分: 每有1个水系技能 +10分
    if trait_name == "水翼推进":
        dynamic_bonus = team_skill_stats.get("water_skills", 0) * 10
        return dynamic_bonus, {"base": 0, "dynamic_bonus": dynamic_bonus}

    # 波多西动态加分: 每有1个防御技能 +10分
    if trait_name == "定向精炼":
        dynamic_bonus = team_skill_stats.get("defense_skills", 0) * 10
        return dynamic_bonus, {"base": 0, "dynamic_bonus": dynamic_bonus}

    # 寒音蛇动态加分: 每有1个状态技能 +10分
    if trait_name == "拨浪鼓":
        dynamic_bonus = team_skill_stats.get("status_skills", 0) * 10
        return dynamic_bonus, {"base": 0, "dynamic_bonus": dynamic_bonus}

    # 非动态特性：返回静态分
    return static_base, {"base": static_base}


# ===== 动态技能威力计算 =====
# 闪击/鸣沙陷阱的威力取决于精灵自身数值与参考对手的差值
REF_OPPONENT_SPD = 250   # 参考对手速度
REF_OPPONENT_DEF = 210   # 参考对手物防
STAB = 1.5               # 本系加成

# 速度/物防差值 → 基础威力档位表（闪击/鸣沙陷阱共用）
_POWER_TIERS = [
    (0,    75),
    (15,  125),
    (30,  162),
    (45,  175),
    (60,  187),
    (75,  200),
    (90,  212),
    (105, 225),
    (120, 237),
    (135, 243),
    (999, 250),
]

def _tier_power(diff):
    """差值→档位威力"""
    for threshold, power in _POWER_TIERS:
        if diff < threshold:
            return power
    return 250

def flash_strike_power(pet_spd, has_stab=False):
    """
    闪击动态威力：速度比对手越高威力越高
    pet_spd: 速度种族值
    has_stab: 精灵是否拥有翼属性
    """
    actual_spd = base_to_actual(pet_spd, 0.2)
    diff = actual_spd - REF_OPPONENT_SPD
    base_power = _tier_power(diff)
    return base_power * (STAB if has_stab else 1.0)

def sand_trap_power(pet_def, has_stab=False):
    """
    鸣沙陷阱动态威力：物防比对手越高威力越高
    pet_def: 物防种族值
    has_stab: 精灵是否拥有地属性
    """
    actual_def = base_to_actual(pet_def, 0.2)
    diff = actual_def - REF_OPPONENT_DEF
    base_power = _tier_power(diff)
    return base_power * (STAB if has_stab else 1.0)

def dynamic_skill_score(skill_name, pet_attrs, pet_stats):
    """获取动态技能分：闪击/鸣沙陷阱按精灵数值计算，其他技能返回None"""
    if skill_name == "闪击":
        spd = pet_stats.get("spd", 80)
        has_stab = "翼" in pet_attrs
        p = flash_strike_power(spd, has_stab)
        return round(power_value(p, 4), 1)
    if skill_name == "鸣沙陷阱":
        def_ = pet_stats.get("def", 80)
        has_stab = "地" in pet_attrs
        p = sand_trap_power(def_, has_stab)
        return round(power_value(p, 4), 1)
    return None


def swift_strike_score(other_skills, skill_scores, has_hurricane=False):
    """
    疾风连袭动态评分。
    other_skills: 除疾风连袭外的其他技能列表 (list of dict)
    skill_scores: 技能分数字典 {name: {score, power, ...}}
    has_hurricane: 飓风特性使全技能获得迅捷

    机制: 释放所有使用过的迅捷技能(各最多1次)
    能耗 = floor(迅捷技能总能耗/2 + 已使用次数), 首用次数=0
    """
    swift_skills = []
    for sk in other_skills:
        desc = sk.get("desc", "")
        if has_hurricane or "迅捷" in desc:
            swift_skills.append(sk)

    if not swift_skills:
        return 0, 0  # 无迅捷技能可释放

    total_cost = sum(sk.get("cost", 0) for sk in swift_skills)
    dyn_cost = total_cost // 2  # floor(总能耗/2)

    # 释放的价值 = 迅捷技能总分 × 0.5 (每个仅释放1次，边际递减)
    total_swift_score = sum(
        skill_scores.get(sk.get("name", ""), 0)
        for sk in swift_skills
    )
    # 也计入威力贡献（buff技能power=0但有分数值）
    total_power = sum(sk.get("power", 0) for sk in swift_skills)

    # 综合评分: 释放技能的分数价值 + 威力效率分
    # 疾风连袭是行动压缩技能（一回合释放所有迅捷技能），价值高于普通攻击
    value = total_swift_score * 0.5
    if total_power > 0 and dyn_cost > 0:
        value += power_value(total_power, dyn_cost) * 0.3

    return round(value, 1), dyn_cost


def _swift_bonus_for_skill(sk):
    """计算技能获得迅捷后的加分（复刻 score_all_skills.py 的迅捷后置计算）"""
    power = sk.get("power", 0)
    cost = sk.get("cost", 0)
    desc = sk.get("desc", "")
    is_dynamic = power == 0 and ("造成物伤" in desc or "造成魔伤" in desc)
    if power > 0 or is_dynamic:
        pv = power_value(power, cost)
        swift = 16 + pv * 0.4
        swift = max(16, min(28, round(swift, 1)))
    elif "减伤" in desc or ("减少" in desc and "伤害" in desc):
        def_pct = _find_int(r"减伤(\d+)%", desc) or _find_int(r"减少(\d+)%", desc) or 50
        dv = def_pct / 2.5
        if def_pct >= 100:
            dv += 25
        if cost <= 1: coeff = 1.0
        elif cost <= 2: coeff = 0.9
        elif cost <= 3: coeff = 0.7
        else: coeff = 0.5
        dv = dv * coeff
        swift = 18 + dv * 0.35
        swift = max(18, min(26, round(swift, 1)))
    else:
        swift = 12
        if "清增益" in desc: swift += 4
        if "清减益" in desc: swift += 3
        if any(kw in desc for kw in ['眩晕', '寄生', '萌化', '焚毁']): swift += 4
        if "连击数" in desc: swift += 2
        swift = max(12, min(18, swift))
    return swift


def score_pet(pet_name, pet_data, learnset_skills, rec_skills, skill_scores, speed_percentile=None):
    """
    精灵综合评分 = 推荐技能总分 + 特性分 + 战斗能力分
    """
    # 技能分 (推荐配置) — 闪击/鸣沙陷阱按精灵数值动态计算
    skill_total = 0
    swift_bonuses = {}  # {skill_name: swift_bonus} 用于输出展示
    attrs = pet_data.get("attrs", [])
    stats = pet_data.get("stats", {})
    trait = pet_data.get("trait", {})
    trait_name = trait.get("name", "")
    has_hurricane = trait_name == "飓风"

    per_skill_base = {}  # 记录每个技能的base分（用于输出）
    if rec_skills:
        # 为动态威力技能构建修正后的技能分数字典（闪击等按精灵数值计算）
        dynamic_skill_scores = dict(skill_scores)
        for sk in rec_skills:
            sk_name = sk["name"] if isinstance(sk, dict) else sk
            dyn = dynamic_skill_score(sk_name, attrs, stats)
            if dyn is not None:
                dynamic_skill_scores[sk_name] = dyn

        for sk in rec_skills:
            sk_name = sk["name"] if isinstance(sk, dict) else sk
            sk_desc = sk.get("desc", "") if isinstance(sk, dict) else ""
            # 疾风连袭在飓风下重新计算（更多技能获得迅捷，使用动态技能分）
            if sk_name == "疾风连袭" and has_hurricane:
                other_skills = [s for s in rec_skills if (s["name"] if isinstance(s, dict) else s) != "疾风连袭"]
                jf_score, _ = swift_strike_score(other_skills, dynamic_skill_scores, has_hurricane=True)
                base_score = jf_score
            else:
                base_score = dynamic_skill_scores.get(sk_name, skill_scores.get(sk_name, 0))
            per_skill_base[sk_name] = base_score
            # 飓风特性：全技能获得迅捷，为没有迅捷的技能加分
            swift_extra = 0
            if has_hurricane and "迅捷" not in sk_desc:
                swift_extra = _swift_bonus_for_skill(sk) if isinstance(sk, dict) else 0
            skill_total += base_score + swift_extra
            if swift_extra:
                swift_bonuses[sk_name] = swift_extra

    # 特性分
    trait_score, trait_pts = score_trait(trait_name, trait.get("desc", ""))

    # 属性×种族 综合战斗能力评分: 攻击能力 + 防御能力 + 速度线
    stats = dict(stats)  # copy to avoid modifying original

    # 特性对种族的直接影响：先于 combat_score 计算生效
    trait_desc = trait.get("desc", "")
    if "失去自己一半" in trait_desc or "失去一半" in trait_desc:
        stats['hp'] = stats['hp'] // 2

    attr_bonus, attr_detail = calc_combat_score(attrs, stats, rec_skills, trait.get("name", ""), speed_percentile)

    # 特性分直接使用手工评分表的值，已综合所有效果
    trait_effective = trait_score
    total = round(skill_total + trait_effective + attr_bonus, 1)

    return total, {
        "skill_score": round(skill_total, 1),
        "trait_score": trait_score,
        "trait_pts": trait_pts,
        "combat_score": attr_bonus,
        "combat_detail": attr_detail,
        "trait_name": trait.get("name", ""),
        "trait_desc": trait.get("desc", ""),
        "attrs": attrs,
        "stats": stats,
        "swift_bonuses": swift_bonuses,  # 飓风特性为各技能加的迅捷分
        "has_hurricane": has_hurricane,
        "per_skill_base": per_skill_base,
    }


def _get_fallback_key(name, data_dict):
    """地区形态回退：若name不在data_dict中，查找同基底形态的条目"""
    if name in data_dict:
        return name
    base = name.split("（")[0] if "（" in name else name
    for k in data_dict:
        if k.split("（")[0] == base:
            return k
    return None


def main():
    # Load data — 使用 spirit_filter_index.json 作为唯一数据源
    from models import get_all_pets_with_skills, DATA_DIR as M_DATA_DIR
    pets = get_all_pets_with_skills()
    with open(DATA_DIR / "all_skill_rankings.json") as f:
        rankings = json.load(f)

    skill_scores = {s['name']: s['score'] for s in rankings}

    # Load learnset and recommended skill data
    with open(DATA_DIR / "pet_learnset.json") as f:
        learnsets = json.load(f)
    with open(DATA_DIR / "pet_recommended.json") as f:
        recommended = json.load(f)

    # 计算全精灵速度百分位
    all_speeds = []
    for name, pet in pets.items():
        spd = pet.get("stats", {}).get("spd", 80)
        all_speeds.append(spd)
    all_speeds.sort()
    n_speeds = len(all_speeds)
    def _speed_percentile(spd):
        """返回速度超越比例 (0~1)"""
        faster_than = sum(1 for s in all_speeds if spd > s)
        return faster_than / n_speeds

    # 御驾亲征精灵（棋契陛下）：收尾精灵不应携带强化/变化技能，只保留攻击技能
    for name in list(recommended.keys()):
        if "棋契陛下" in name:
            rec_skills = recommended[name]
            recommended[name] = [s for s in rec_skills
                                 if s.get("power", 0) > 0 and s.get("category") != "变化"]
            if len(recommended[name]) < 4:
                # 攻击技能不足4个，从learnset中补攻击技能
                ls = learnsets.get(name, [])
                existing = {s["name"] for s in recommended[name]}
                attacks = [s for s in ls if s.get("power", 0) > 0
                          and s.get("name") not in existing]
                recommended[name].extend(attacks[:4 - len(recommended[name])])

    # 首领化 lineage：从 spirit_filter_index 自动识别
    # 同一 NO 编号下有 boss + 非boss 形态 = 一条首领化进化线
    BOSS_LINE = set()
    BOSS_NAMES = set()  # 仅首领形态（typeClass=boss），用于 [首领] 标注
    try:
        with open(DATA_DIR / "spirit_filter_index.json") as f:
            filter_idx = json.load(f)
        from collections import defaultdict
        no_groups = defaultdict(lambda: {"boss": [], "base": []})
        for it in filter_idx.get("items", []):
            no = it.get("noText", "")
            name = it.get("name", "")
            if it.get("typeClass") == "boss":
                no_groups[no]["boss"].append(name)
                BOSS_NAMES.add(name)
            else:
                no_groups[no]["base"].append(name)
        # 有 boss 形态的 NO 即为首领化进化线
        for no, group in no_groups.items():
            if group["boss"]:
                BOSS_LINE.update(group["boss"])
                BOSS_LINE.update(group["base"])
    except Exception:
        pass

    # 中间进化形态：只保留最终进化态和首领化形态
    removed_pets = set()
    try:
        with open(DATA_DIR / "_boss_info.json") as f:
            boss_info = json.load(f)
        # 移除所有标记 remove 的进化中间态（保护首领形态不被误删）
        removed_pets = {n for n, bi in boss_info.items()
                        if bi.get('remove') and n not in BOSS_NAMES}
    except Exception:
        pass

    def _is_removed(name):
        """检查精灵是否应被移除（支持短名匹配长名）"""
        if name in removed_pets:
            return True
        base = name.split("（")[0] if "（" in name else name
        return base in removed_pets

    # Score all pets (including boss base forms, without race boosting)
    pet_scores = {}
    for name, pet in pets.items():
        if _is_removed(name):
            continue
        pet_skills = pet.get("skills", {})
        pet_learnset = pet_skills.get("learnset", [])
        pet_recommended = pet_skills.get("recommended", [])

        # 地区形态回退：若无推荐技能/学习面，继承同基底形态
        if not pet_learnset:
            fallback = _get_fallback_key(name, learnsets)
            if fallback:
                pet_learnset = learnsets.get(fallback, [])
        if not pet_recommended:
            fallback = _get_fallback_key(name, recommended)
            if fallback:
                pet_recommended = recommended.get(fallback, [])

        if not pet_learnset and not pet_recommended:
            continue
        spd = pet.get("stats", {}).get("spd", 80)
        pct = _speed_percentile(spd)
        score, meta = score_pet(
            name, pet, pet_learnset or learnsets.get(name, []),
            pet_recommended or recommended.get(name, []), skill_scores, pct
        )
        rec_skills_full = pet_recommended or recommended.get(name, [])
        rec_skills = [sk["name"] for sk in rec_skills_full]
        # 每个技能的最终得分（含迅捷加分），使用score_pet计算的per_skill_base
        per_skill_base = meta.get("per_skill_base", {})
        skill_final_scores = {}
        for sk in rec_skills_full:
            sk_name = sk["name"]
            base = per_skill_base.get(sk_name, dynamic_skill_score(sk_name, meta["attrs"], meta["stats"]) or skill_scores.get(sk_name, 0))
            extra = meta.get("swift_bonuses", {}).get(sk_name, 0)
            skill_final_scores[sk_name] = round(base + extra, 1)
        pet_scores[name] = {
            "id": name,
            "name": name,
            "score": score,
            "attrs": meta["attrs"],
            "stats": meta["stats"],
            "trait_name": meta["trait_name"],
            "trait_desc": meta["trait_desc"],
            "trait_score": meta["trait_score"],
            "trait_pts": meta["trait_pts"],
            "skill_score": meta["skill_score"],
            "combat_score": meta["combat_score"],
            "combat_detail": meta["combat_detail"],
            "recommended_skills": rec_skills,
            "skill_final_scores": skill_final_scores,
            "has_hurricane": meta.get("has_hurricane", False),
            "swift_bonuses": meta.get("swift_bonuses", {}),
        }

    # Sort all by score
    ranked_all = sorted(pet_scores.values(), key=lambda x: -x["score"])

    # 去重：棋家族的黑子和白子完全一样，只保留一个（白子优先）
    # 格式: "棋契陛下（棋骑士-白子）" → base = "棋契陛下（棋骑士）"
    # 只保留棋绮后形态，隐藏棋齐垒/棋骑士/棋祈督
    QB_HIDDEN = {"棋齐垒", "棋骑士", "棋祈督"}
    seen = set()
    unique_ranked = []
    for p in ranked_all:
        name = p["name"]
        base_name = name.replace("-白子", "").replace("-黑子", "")
        if base_name in seen:
            continue
        # 隐藏非棋绮后的棋契陛下形态
        if "棋契陛下" in name and any(h in name for h in QB_HIDDEN):
            continue
        seen.add(base_name)
        unique_ranked.append(p)

    # Full ranking: unified, no boss/regular split
    ranked = unique_ranked

    # Save rankings
    with open(DATA_DIR / "all_pet_rankings.json", "w") as f:
        json.dump(ranked, f, ensure_ascii=False, indent=2)

    def _format_combat(cd, combat_score, actual_spd, attrs=None):
        stat_parts = []
        pa = cd.get('phys_atk', cd.get('attack', 0))
        sa = cd.get('spec_atk', 0)
        if pa > 0 and sa > 0:
            stat_parts.append(f"物攻{pa:.0f}/特攻{sa:.0f}")
        elif pa > 0:
            stat_parts.append(f"物攻{pa:.0f}")
        elif sa > 0:
            stat_parts.append(f"特攻{sa:.0f}")
        pd = cd.get('phys_def', 0)
        sd = cd.get('spec_def', 0)
        stat_parts.append(f"物防{pd:.0f}/特防{sd:.0f}")
        stat_parts.append(f"速{actual_spd}={cd.get('speed',0):.0f}")
        attr_s = cd.get('attr_score', 0)
        race_s = round(combat_score - attr_s, 1)
        attr_label = f"{'+'.join(attrs)}" if attrs else ""
        return f"种族值={race_s:.0f} ({' '.join(stat_parts)}) 属性={attr_s:.0f}[{attr_label}]"

    def _print_ranking(pets_list, title, start_idx=1, top_n=None):
        print("\n" + "=" * 100)
        print(title)
        print("=" * 100)
        items = pets_list[:top_n] if top_n else pets_list
        for i, p in enumerate(items, start_idx):
            cd = p.get('combat_detail', {})
            actual_spd = cd.get('actual_spd', 0)
            combat_str = _format_combat(cd, p['combat_score'], actual_spd, p.get('attrs'))
            sf = p.get('skill_final_scores', {})
            if sf:
                sk_parts = [f"{sn}={sf[sn]:.0f}" if sn in sf else sn for sn in p.get("recommended_skills", [])[:4]]
                sk_names = " · ".join(sk_parts)
            else:
                sk_names = " · ".join(p.get("recommended_skills", [])[:4])
            display_name = p['name'] + ("[首领]" if p['name'] in BOSS_NAMES else "")
            trait_suffix = ""
            if p.get('has_hurricane'):
                sb = p.get('swift_bonuses', {})
                sw = sum(sb.values())
                trait_suffix = f" [迅捷+{sw:.0f}]"
            print(f"{i:>3}. {display_name:<16} {p['score']:>6.1f}  "
                  f"技能={p['skill_score']:.0f} [{sk_names}] {combat_str}  "
                  f"【{p['trait_name']}={p['trait_score']:.0f}{trait_suffix}】{p['trait_desc'][:50]}")

    # Print unified ranking (top 50)
    _print_ranking(ranked, "精灵综合排名 (技能 + 战斗能力[攻防速])", top_n=50)

    # === 体系队伍推荐 ===
    print("\n" + "=" * 100)
    print("体系队伍推荐 — 围绕每个体系组建最优6人队")
    print("=" * 100)

    score_map = {p["name"]: p["score"] for p in ranked}

    def has_defense_skill(name):
        rec = recommended.get(name, [])
        return any("减伤" in sk.get("desc", "") for sk in rec)

    def has_utility_skill(name):
        rec = recommended.get(name, [])
        return any(
            "驱散" in sk.get("desc", "") or "印记" in sk.get("desc", "")
            or "眩晕" in sk.get("desc", "")
            for sk in rec
        )

    def get_attrs(name):
        return pets.get(name, {}).get("attrs", [])

    def get_trait(name):
        return pets.get(name, {}).get("trait", {})

    def get_trait_text(name):
        t = get_trait(name)
        return (t.get("name", "") or "") + " " + (t.get("desc", "") or "")

    def has_skill_match(name, keywords):
        """检查精灵的技能是否匹配关键词"""
        ls = learnsets.get(name, [])
        for sk in ls:
            text = (sk.get("name", "") or "") + " " + (sk.get("desc", "") or "")
            for kw in keywords:
                if kw in text:
                    return True
        return False

    def has_trait_match(name, keywords):
        """检查精灵的特性是否匹配关键词"""
        text = get_trait_text(name)
        for kw in keywords:
            if kw in text:
                return True
        return False

    # ─── 体系定义 ───
    SYSTEMS = [
        # ═══ 机制体系 ═══
        {
            "id": "poison", "name": "毒体系", "type": "mechanic",
            "skill_keywords": ["中毒", "毒雾", "毒囊", "疫病吐息", "剧毒"],
            "trait_keywords": ["中毒", "毒腺", "下黑手", "毒牙", "侵蚀",
                             "复方汤剂", "高浓生物碱", "溶解", "扩散侵蚀"],
            "engine_skills": ["毒雾", "疫病吐息", "感染病", "剧毒"],
            "bonus": 60, "desc": "中毒印记联动体系",
        },
        {
            "id": "starfall", "name": "星陨体系", "type": "mechanic",
            "skill_keywords": ["星陨", "心灵洞悉"],
            "trait_keywords": ["星陨", "吸积盘", "守望星", "坠星", "观星",
                             "陨落", "月牙雪糕", "星云旅者", "穹顶之下"],
            "engine_skills": ["心灵洞悉", "二律背反", "错乱"],
            "bonus": 70, "desc": "星陨印记联动体系",
        },
        {
            "id": "burn", "name": "灼烧体系", "type": "mechanic",
            "skill_keywords": ["灼烧", "焚烧", "火焰护盾"],
            "trait_keywords": ["灼烧", "焚烧", "煤渣草", "爆燃", "助燃",
                             "贪心算法", "灵魂灼伤", "急性子", "仁心",
                             "蒸汽膨胀", "散热"],
            "engine_skills": ["焚烧烙印", "焚毁", "火焰护盾", "天火"],
            "bonus": 55, "desc": "灼烧印记联动体系",
        },
        {
            "id": "freeze", "name": "冻结体系", "type": "mechanic",
            "skill_keywords": ["冻结", "速冻", "冰墙", "暴风雪"],
            "trait_keywords": ["冻结", "冰封", "冰钻", "打雪仗", "加个雪球",
                             "捉迷藏", "抓到你了", "捉到你了", "月牙雪糕"],
            "engine_skills": ["暴风雪", "速冻", "滚雪球", "冰点"],
            "bonus": 55, "desc": "冻结印记联动体系",
        },
        {
            "id": "moe", "name": "萌化体系", "type": "mechanic",
            "skill_keywords": ["萌化", "甜心续航"],
            "trait_keywords": ["萌化", "自由飘", "无忧无虑", "守护者", "化茧"],
            "engine_skills": ["甜心续航", "捧杀"],
            "bonus": 65, "desc": "萌化印记联动体系",
        },
        {
            "id": "devotion", "name": "奉献体系", "type": "mechanic",
            "skill_keywords": ["奉献"],
            "trait_keywords": ["奉献", "恶魔红钻", "花精灵", "振奋虫心",
                             "坚韧铠甲", "虫群突袭", "虫群鼓舞", "壮胆"],
            "engine_skills": ["假寐", "虫茧", "虫结阵"],
            "bonus": 50, "desc": "奉献联动体系（虫系核心）",
        },
        {
            "id": "energy_drain", "name": "吸能体系", "type": "mechanic",
            "skill_keywords": ["敌方失去", "偷取", "勾魂", "恶作剧", "惊吓盒子",
                             "报复", "能量不足"],
            "trait_keywords": ["大捞一笔", "小偷小摸", "碰瓷", "做噩梦",
                             "珊瑚骨", "毒蘑菇", "特殊清洁场景", "冰封",
                             "敌方.*能量", "失去.*能量"],
            "engine_skills": ["勾魂", "恶作剧", "报复", "惊吓盒子"],
            "bonus": 55, "desc": "削减敌方能量体系",
        },
        {
            "id": "charge", "name": "蓄能体系", "type": "mechanic",
            "skill_keywords": ["蓄势", "蓄能", "蓄电", "增程"],
            "trait_keywords": ["蓄势", "蓄能", "蓄电", "增程", "对流",
                             "超聚能", "超负荷", "连续负荷"],
            "engine_skills": ["蓄势待发", "增程电池", "蓄能轰击"],
            "bonus": 55, "desc": "蓄能印记联动体系",
        },
        {
            "id": "counter", "name": "应对体系", "type": "mechanic",
            "skill_keywords": ["应对状态", "应对攻击", "应对防御"],
            "trait_keywords": ["应对", "慢热型", "野性感官", "圣火骑士",
                             "思维之盾", "身经百练", "斗技", "擒拿", "马步"],
            "engine_skills": ["叠势", "气势一击"],
            "bonus": 65, "desc": "应对技能/特性联动体系",
        },
        # ═══ 天气体系 ═══
        {
            "id": "rain", "name": "雨天体系", "type": "weather",
            "weather_skill": "落雨",
            "skill_keywords": ["落雨", "雨天", "湿润", "水翼"],
            "trait_keywords": ["得寸进尺", "浪潮", "水翼飞升", "水翼推进",
                             "浸润", "雨天", "落雨"],
            "engine_skills": ["落雨"],
            "bonus": 70, "desc": "雨天天气体系",
        },
        {
            "id": "thunder", "name": "雷暴体系", "type": "weather",
            "weather_skill": "雷暴",
            "skill_keywords": ["雷暴", "雷", "电"],
            "trait_keywords": ["雷暴", "电弧", "闪电", "电", "生物电",
                             "电流刺激", "电容器"],
            "engine_skills": ["雷暴"],
            "bonus": 65, "desc": "雷暴天气体系",
        },
        {
            "id": "sand", "name": "沙暴体系", "type": "weather",
            "weather_skill": "沙涌",
            "skill_keywords": ["沙涌", "沙暴", "沙尘", "流沙"],
            "trait_keywords": ["沙暴", "沙尘", "消波块", "鸣沙"],
            "engine_skills": ["沙涌"],
            "bonus": 60, "desc": "沙暴天气体系",
        },
        {
            "id": "snow", "name": "雪天体系", "type": "weather",
            "weather_skill": "冬至",
            "skill_keywords": ["暴风雪", "冬至", "雪", "冰天雪地"],
            "trait_keywords": ["暴风雪", "冬至", "雪", "打雪仗", "冰封"],
            "engine_skills": ["冬至", "暴风雪"],
            "bonus": 60, "desc": "雪天/暴风雪天气体系",
        },
        # ═══ 核心精灵体系 ═══
        {
            "id": "status", "name": "状态队", "type": "center",
            "center_pets": ["寒音蛇"],
            "support_keywords": ["灼烧", "中毒", "冻结", "萌化", "棘刺",
                               "聒噪", "减速", "状态技能"],
            "engine_skills": [],
            "bonus": 65, "desc": "寒音蛇状态联动队",
        },
        {
            "id": "moe_team", "name": "萌队", "type": "center",
            "center_pets": ["卡洛儿"],
            "support_keywords": ["萌化", "甜心", "萌"],
            "engine_skills": [],
            "bonus": 65, "desc": "卡洛儿萌化联动队",
        },
        {
            "id": "fire_team", "name": "火队", "type": "center",
            "center_pets": ["烈火守护"],
            "support_keywords": ["火", "灼烧", "爆燃", "焚烧", "蒸汽"],
            "engine_skills": [],
            "bonus": 60, "desc": "烈火守护火系联动队",
        },
        {
            "id": "wing_king", "name": "翼王队", "type": "center",
            "center_pets": ["圣羽翼王"],
            "support_keywords": ["翼", "水刃", "闪击", "黑羽", "翠顶", "岚鸟"],
            "engine_skills": ["水刃", "闪击"],
            "bonus": 80, "desc": "飓风翼王 — 绑定水刃翼系队友(岚鸟/翠顶/黑羽)",
        },
    ]

    top_pool = [p["name"] for p in ranked[:80]]
    # 不能在队伍间通用的精灵：首领
    NOFILL = BOSS_NAMES
    noboss_pool = [n for n in top_pool if n not in NOFILL]

    # ─── 为每个首领精灵自动生成队伍定义 ───
    # 按家族(noText)分组，取每族最高分形态
    def _make_boss_teams():
        """为所有首领化精灵自动生成中心型体系定义"""
        boss_groups = {}
        from models import get_family_map
        family_map = get_family_map()
        for name in BOSS_NAMES:
            if name not in score_map:
                continue
            no = family_map.get(name, name)
            if no not in boss_groups or score_map[name] > score_map[boss_groups[no]]:
                boss_groups[no] = name

        boss_defs = []
        for no, boss_name in sorted(boss_groups.items(),
                                     key=lambda x: -score_map.get(x[1], 0)):
            attrs = get_attrs(boss_name)
            trait_text = get_trait_text(boss_name)
            support_kw = list(attrs)
            for kw in ["奉献", "萌化", "中毒", "灼烧", "冻结", "星陨", "应对",
                       "连击", "蓄力", "印记", "雨天", "沙暴", "暴风雪"]:
                if kw in trait_text:
                    support_kw.append(kw)
            # 棋家族特殊处理：匹配地/武
            if "棋" in boss_name or "御驾亲征" in trait_text:
                support_kw.extend(["地", "武"])
            boss_defs.append({
                "id": f"boss_{no}",
                "name": f"{boss_name}队",
                "type": "center",
                "center_pets": [boss_name],
                "support_keywords": list(set(support_kw)),
                "engine_skills": [],
                "bonus": 60,
                "desc": f"围绕{boss_name}的首领化队伍",
            })
        return boss_defs

    BOSS_SYSTEMS = _make_boss_teams()
    ALL_SYSTEMS = SYSTEMS + BOSS_SYSTEMS

    def score_system_fit(name, sys_def):
        """评估精灵与体系的匹配度 (0-100)"""
        score = 0
        tp = sys_def["type"]

        if tp == "center":
            if name in sys_def["center_pets"]:
                return 100
            # 检查属性、技能、特性、名称是否匹配支援关键词
            attrs = get_attrs(name)
            sup_kw = sys_def.get("support_keywords", [])
            for a in attrs:
                if a in sup_kw:
                    score += 25
            for kw in sup_kw:
                if kw in name:
                    score += 15
            if has_skill_match(name, sup_kw):
                score += 25
            if has_trait_match(name, sup_kw):
                score += 25
            return min(score, 85)

        if tp == "weather":
            if has_skill_match(name, [sys_def["weather_skill"]]):
                score += 60
            if has_skill_match(name, sys_def.get("skill_keywords", [])):
                score += 20
            if has_trait_match(name, sys_def.get("trait_keywords", [])):
                score += 30
            return min(score, 100)

        # mechanic
        if has_skill_match(name, sys_def.get("skill_keywords", [])):
            score += 35
        if has_trait_match(name, sys_def.get("trait_keywords", [])):
            score += 50
        # 发动机技能额外加分
        if has_skill_match(name, sys_def.get("engine_skills", [])):
            score += 25
        return min(score, 100)

    # 精灵黑名单：未出的精灵不参与组队
    PET_BLACKLIST = {'格斗小六', '格斗小五'}

    def discover_system_pets(sys_def):
        """发现属于某体系的所有精灵，返回 [(name, fit_score), ...] 按匹配度排序
        注意：首领精灵不通过关键词自动发现，避免被错误归类到其他体系
        """
        result = []
        for name in score_map:
            if name not in learnsets:
                continue
            if name in BOSS_NAMES:
                continue  # 首领精灵不入体系池，只在center类型显式指定
            if name in PET_BLACKLIST:
                continue  # 黑名单精灵不参与体系池
            fit = score_system_fit(name, sys_def)
            if fit > 0:
                result.append((name, fit))
        result.sort(key=lambda x: (-x[1], -score_map.get(x[0], 0)))
        return result

    # 同一家族(NO)只能携带一只的精灵族（进化线共享NO的需要合并）
    FAMILY_BLACKLIST_NOS = {
        "NO.024",  # 蹦蹦种子 → 蹦蹦草 → 蹦蹦花 → 蹦蹦果 (草毒家族，各种形态)
        "NO.017",  # 海盔虫 → 刺盔虫 → 千棘盔 (水毒家族)
        "NO.033",  # 海针 → 千棘海针 (水毒家族)
        "NO.001",  # 迪莫 → 圣光迪莫
        "NO.002",  # 喵喵 → 魔力猫 → 叶冕魔力猫
        "NO.004",  # 水蓝蓝 → 水灵 → 圣水守护
        "NO.003",  # 火花 → 火神 → 烈火战神
        "NO.005",  # 鸭吉吉家族
        "NO.011",  # 晶石蜗家族
    }

    from models import get_family_map as _get_fm
    _family_map = _get_fm()

    def _get_family_no(name):
        """返回精灵的家族编号（相同NO = 同一进化线/家族，只能上一只）"""
        if name in _family_map:
            return _family_map[name]
        return name  # fallback: use name itself as unique key

    def _family_ok(members):
        """检查没有同家族的精灵"""
        seen_nos = set()
        for m in members:
            no = _get_family_no(m)
            if no in FAMILY_BLACKLIST_NOS:
                if no in seen_nos:
                    return False
                seen_nos.add(no)
        return True

    def _attr_ok(members, max_same=4):
        """同属性限制：纯体系队放宽，但不过度集中（避免6只同一属性）"""
        ac = {}
        for m in members:
            for a in get_attrs(m):
                if ac.get(a, 0) >= max_same:
                    return False
                ac[a] = ac.get(a, 0) + 1
        return True

    def calc_team_skill_stats(members, exclude=None):
        """计算队伍技能统计，exclude为需要排除的精灵名（动态特性计算自身不参与）"""
        earth_count = 0
        martial_count = 0
        counter_count = 0
        fire_skill_count = 0    # 火系技能数
        ice_skill_count = 0     # 冰系技能数
        water_skill_count = 0   # 水系技能数
        defense_skill_count = 0 # 防御技能数 (category=变化)
        status_skill_count = 0  # 状态技能数 (category=变化)
        for m in members:
            if m == exclude:
                continue
            rec = recommended.get(m, [])
            for sk in rec:
                sk_element = sk.get("element", "") if isinstance(sk, dict) else ""
                sk_category = sk.get("category", "") if isinstance(sk, dict) else ""
                sk_desc = sk.get("desc", "") if isinstance(sk, dict) else ""
                sk_name = sk.get("name", "") if isinstance(sk, dict) else ""
                if sk_element == "地":
                    earth_count += 1
                elif sk_element == "武":
                    martial_count += 1
                if "应对" in sk_name or "应对" in sk_desc:
                    counter_count += 1
                if sk_element == "火":
                    fire_skill_count += 1
                if sk_element == "冰":
                    ice_skill_count += 1
                if sk_element == "水":
                    water_skill_count += 1
                if sk_category == "变化":
                    defense_skill_count += 1
                    status_skill_count += 1
        return {
            "earth": earth_count,
            "martial": martial_count,
            "counter": counter_count,
            "fire_skills": fire_skill_count,
            "ice_skills": ice_skill_count,
            "water_skills": water_skill_count,
            "defense_skills": defense_skill_count,
            "status_skills": status_skill_count,
        }

    def eval_team(members, synergy_bonus=0, sys_def=None):
        """评估队伍质量：包含动态特性加分和体系匹配度奖励"""
        if len(members) != 6:
            return -9999, {}
        if not _attr_ok(members, 6):  # 同属性限制
            return -9999, {}
        if not _family_ok(members):  # 同家族只能上一只
            return -9999, {}

        base_score = sum(score_map.get(m, 0) for m in members)
        # 体系匹配度奖励：体系是灵魂！高匹配度精灵大幅加分
        if sys_def:
            for m in members:
                fit = score_system_fit(m, sys_def)
                if fit >= 85:
                    base_score += 350  # 核心体系精灵超大奖励
                elif fit >= 50:
                    base_score += 180  # 高匹配精灵大奖励
        def_count = sum(1 for m in members if has_defense_skill(m))
        utl_count = sum(1 for m in members if has_utility_skill(m))
        all_attrs = set()
        spd_list = []
        for m in members:
            all_attrs.update(get_attrs(m))
            spd_list.append(pets.get(m, {}).get("stats", {}).get("spd", 0))

        # 动态特性加分: 根据队伍技能情况重新计算（排除自身）
        dynamic_trait_bonus = 0
        trait_scores = {}  # 每只精灵的特性分（组队上下文）
        for m in members:
            trait = pets.get(m, {}).get("trait", {})
            team_skill_stats = calc_team_skill_stats(members, exclude=m)
            t_score, t_breakdown = score_trait(
                trait.get("name", ""), trait.get("desc", ""),
                team_skill_stats, m
            )
            dyn_bonus = t_breakdown.get("dynamic_bonus", 0)
            dynamic_trait_bonus += dyn_bonus
            trait_scores[m] = t_score

        penalty = 0
        if def_count < 3:
            penalty -= (3 - def_count) * 15

        # 特性配合检查
        for m in members:
            trait_text = get_trait_text(m)
            pet_attrs = get_attrs(m)
            if "奉献" in trait_text:
                same = sum(1 for o in members if o != m
                          and set(pet_attrs) & set(get_attrs(o)))
                if same == 0:
                    penalty -= 35
            if "主动击败" in trait_text:
                penalty -= 12

        # 体系发动机检查
        engine_bonus = 0
        if sys_def and sys_def.get("engine_skills"):
            has_engine = any(
                has_skill_match(m, sys_def["engine_skills"])
                for m in members
            )
            if not has_engine:
                penalty -= 20

        # 天气体系：检查是否有天气手
        if sys_def and sys_def["type"] == "weather":
            has_weather = any(
                has_skill_match(m, [sys_def["weather_skill"]])
                for m in members
            )
            if not has_weather:
                penalty -= 40

        # ── 体系纯净加分 ──
        purity_bonus = 0
        if sys_def:
            # 体系纯净度是体系的灵魂！纯体系队必须碾压混合队
            core_fit_count = sum(1 for m in members
                                 if score_system_fit(m, sys_def) >= 85)
            high_fit_count = sum(1 for m in members
                                 if score_system_fit(m, sys_def) >= 50)
            if core_fit_count >= 6:  # 6只核心体系精灵
                purity_bonus += 600
            elif core_fit_count >= 5:
                purity_bonus += 500
            elif high_fit_count >= 6:  # 6只高匹配体系精灵
                purity_bonus += 400
            elif high_fit_count >= 5:
                purity_bonus += 300
            elif high_fit_count >= 4:
                purity_bonus += 150
            elif high_fit_count >= 3:
                purity_bonus += 80

        # ── 进攻冗余 + 控制战术 + 联防面加分 ──
        redundancy_bonus = 0
        control_bonus = 0
        coverage_bonus = 0

        # --- 战术一：定向施压（同属性输出集中度） ---
        element_counts = {}
        for m in members:
            rec = recommended.get(m, [])
            for sk in rec:
                elem = sk.get("element", "") if isinstance(sk, dict) else ""
                if elem and sk.get("power", 0) >= 70:
                    element_counts[elem] = element_counts.get(elem, 0) + 1

        if element_counts:
            max_element_count = max(element_counts.values())
            # 冗余战术：2-3个核心属性的集中才有战术价值
            # 4+同属性属于过度集中（容错率低），奖励递减
            if max_element_count >= 4:
                redundancy_bonus += 30  # 过度集中不奖励太多
            elif max_element_count >= 3:
                redundancy_bonus += 50  # 最佳集中度
            elif max_element_count >= 2:
                redundancy_bonus += 20
            # 奖励多属性进攻轴（双属性集中更有战术灵活性）
            multi_element = sum(1 for v in element_counts.values() if v >= 2)
            if multi_element >= 3:
                redundancy_bonus += 40  # 多轴进攻
            elif multi_element >= 2:
                redundancy_bonus += 20

        # --- 战术二：控制战术（让counter失能） ---
        # 统计各类控制技能的数量和强度
        freeze_count = 0    # 冻结：无法下场清除
        moe_count = 0       # 萌化：无法下场清除
        energy_drain_count = 0  # 吸能/阻止回能
        stun_count = 0      # 眩晕/打断

        for m in members:
            rec = recommended.get(m, [])
            for sk in rec:
                desc = sk.get("desc", "") if isinstance(sk, dict) else ""
                sk_name = sk.get("name", "") if isinstance(sk, dict) else ""

                if "冻结" in desc or "冰冻" in desc or "暴风雪" in sk_name or "滚雪球" in sk_name:
                    freeze_count += 1
                if "萌化" in desc or "甜心" in sk_name:
                    moe_count += 1
                if "失去" in desc and "能量" in desc or "勾魂" in sk_name or "惊吓盒子" in sk_name:
                    energy_drain_count += 1
                if "眩晕" in desc or "打断" in desc or "地刺" in sk_name:
                    stun_count += 1

        # 控制技能加分：多类型控制组合效果更好
        total_control = freeze_count + moe_count + energy_drain_count + stun_count
        control_types = sum(1 for x in [freeze_count, moe_count, energy_drain_count, stun_count] if x > 0)
        if control_types >= 3:  # 3种以上控制类型 = 立体控制
            control_bonus += 60
        elif control_types >= 2:  # 2种控制类型
            control_bonus += 30
        if total_control >= 6:
            control_bonus += 40
        elif total_control >= 4:
            control_bonus += 20

        # --- 战术三：炮台战术（多炮台协同） ---
        cannon_bonus = 0
        # 炮台检测：高速(>100) + 高技能输出(>130) 或 有强化技能 + 高输出(>110)
        cannons = []
        for m in members:
            spd = pets.get(m, {}).get("stats", {}).get("spd", 0)
            sk_list = recommended.get(m, [])
            skill_total = sum(skill_scores.get(sk.get("name", ""), 0) for sk in sk_list)
            has_buff = any(
                "物攻+" in sk.get("desc", "") or "魔攻+" in sk.get("desc", "")
                for sk in sk_list
            )
            # 高速清场手类型：速度快 + 输出高
            is_sweeper = spd >= 100 and skill_total >= 130
            # 强化炮台类型：有强化技能 + 输出高
            is_setup = has_buff and skill_total >= 130
            is_cannon = is_sweeper or is_setup
            if is_cannon:
                cannons.append(m)

        # 多炮台加分：2只炮台互相创造登场窗口
        if len(cannons) >= 2:
            # 检查炮台之间是否互为冗余（覆盖对方的counter）
            cannon_attrs = [get_attrs(c) for c in cannons]
            shared_counter = False
            for i, a1 in enumerate(cannon_attrs):
                for j, a2 in enumerate(cannon_attrs):
                    if i >= j: continue
                    # 不同属性组合 = 能覆盖不同的counter
                    if set(a1) != set(a2):
                        shared_counter = True
                        break
            if shared_counter:
                cannon_bonus += 60  # 战术级加分
            else:
                cannon_bonus += 30
        elif len(cannons) >= 1:
            cannon_bonus += 15  # 单炮台基础分

        # --- 战术四：捕获战术（迅捷手场下压力） ---
        capture_bonus = 0
        # 检测迅捷手数量：有迅捷技能的高威胁精灵
        swift_count = 0
        for m in members:
            rec = recommended.get(m, [])
            has_swift = any(
                "迅捷" in sk.get("desc", "") if isinstance(sk, dict) else False
                for sk in rec
            )
            spd = pets.get(m, {}).get("stats", {}).get("spd", 0)
            if has_swift and spd >= 100:
                swift_count += 1

        # 场下压力：迅捷手越多，捕获能力越强
        if swift_count >= 2:
            capture_bonus += 70
        elif swift_count >= 1:
            capture_bonus += 30

        # --- 战术五：场地战术（印记/天气改变对位关系） ---
        field_bonus = 0
        # 检测天气释放者
        weather_setters = {"落雨", "雷暴", "沙涌", "冬至"}
        weather_setter_count = 0
        mark_setters = 0
        for m in members:
            rec = recommended.get(m, [])
            for sk in rec:
                sk_name = sk.get("name", "") if isinstance(sk, dict) else ""
                if sk_name in weather_setters:
                    weather_setter_count += 1
                    break
            if any("印记" in sk.get("desc", "") or "星陨" in sk.get("desc", "") or
                   "灼烧" in sk.get("desc", "") for sk in rec if isinstance(sk, dict)):
                mark_setters += 1

        if weather_setter_count >= 1:
            field_bonus += 40
        if mark_setters >= 3:
            field_bonus += 50

        # --- 容错率分析：猜拳失败后的兜底能力 ---
        high_spd = sum(1 for s in spd_list if s > 120)
        tolerance_bonus = 0
        # 防御兜底：防御技能数量决定猜拳失败后的存活能力
        if def_count >= 4:
            tolerance_bonus += 40
        elif def_count >= 3:
            tolerance_bonus += 20

        # 起点压力检测：高输出高速精灵作为起点时，对方必须换人
        pressure_count = 0
        for i, m in enumerate(members):
            if score_map.get(m, 0) >= 370 and spd_list[i] >= 100:
                pressure_count += 1
        if pressure_count >= 2:
            tolerance_bonus += 30  # 多点压力 = 多点猜拳机会

        # 猜拳失败代价评估：高速精灵多 → 即使猜错也能先手换人 → 损失小
        if high_spd >= 3:
            tolerance_bonus += 25

        # 联防面：属性覆盖广度（但不再是越高越好）
        if len(all_attrs) >= 8:
            coverage_bonus += 15
        elif len(all_attrs) >= 6:
            coverage_bonus += 10

        # 中心型体系：额外防御冗余加分
        if sys_def and sys_def["type"] == "center":
            if def_count >= 2:
                redundancy_bonus += 15
            if high_spd >= 2:
                redundancy_bonus += 10

        total = (base_score + dynamic_trait_bonus + synergy_bonus
                + engine_bonus + purity_bonus + redundancy_bonus + control_bonus
                + cannon_bonus + capture_bonus + field_bonus + tolerance_bonus
                + coverage_bonus + penalty)
        return total, {
            "base": base_score, "trait_bonus": dynamic_trait_bonus,
            "synergy": synergy_bonus, "penalty": penalty,
            "purity": purity_bonus, "redundancy": redundancy_bonus,
            "control": control_bonus, "cannon": cannon_bonus,
            "capture": capture_bonus, "field": field_bonus,
            "tolerance": tolerance_bonus,
            "coverage": coverage_bonus,
            "trait_scores": trait_scores,
            "defense": def_count, "utility": utl_count,
            "attr_coverage": len(all_attrs),
        }

    def greedy_fill_team(core, bonus, sys_def=None, candidate_pool=None,
                        system_pool=None, force_pure_system=False):
        """贪心补位：从核心开始，逐个填充到6人
        force_pure_system: 强制6只都必须是体系精灵，不使用通用池补位
        """
        if candidate_pool is None:
            candidate_pool = noboss_pool
        # 纯体系队放宽属性限制
        max_attr = 6
        current = list(core)

        # 填充策略：优先用体系池填充，体系匹配度给大权重加成
        if system_pool:
            for _ in range(6 - len(current)):
                best_fill_score = -9999
                best_fill = None
                for cand in system_pool:
                    if cand in current:
                        continue
                    trial = current + [cand]
                    if not _attr_ok(trial, max_attr):
                        continue
                    if not _family_ok(trial):  # 同家族只能上一只
                        continue
                    if len(trial) < 6:
                        # 体系精灵优先：基础分 + 体系匹配分(大权重) + 体系匹配度平方奖励
                        fit = score_system_fit(cand, sys_def) if sys_def else 0
                        # fit=85的核心体系精灵比fit=35的高匹配度精灵优先
                        fill_score = score_map.get(cand, 0) + fit * 2.0 + (fit * fit) * 0.02
                    else:
                        fill_score, _ = eval_team(trial, bonus, sys_def)
                    if fill_score > best_fill_score:
                        best_fill_score = fill_score
                        best_fill = cand
                if best_fill:
                    current.append(best_fill)
                else:
                    break

        # 如果不强制纯体系，且体系池不够填充到6只，再用通用池补位
        if not force_pure_system:
            while len(current) < 6:
                best_fill_score = -9999
                best_fill = None
                for cand in candidate_pool:
                    if cand in current:
                        continue
                    trial = current + [cand]
                    if not _attr_ok(trial, max_attr):
                        continue
                    if not _family_ok(trial):
                        continue
                    if len(trial) < 6:
                        fill_score = score_map.get(cand, 0)
                    else:
                        fill_score, _ = eval_team(trial, bonus, sys_def)
                    if fill_score > best_fill_score:
                        best_fill_score = fill_score
                        best_fill = cand
                if best_fill:
                    current.append(best_fill)
                else:
                    break

        if len(current) == 6:
            total, info = eval_team(current, bonus, sys_def)
            return current, total, info
        return None, -9999, {}

    # ─── 为每个体系组建队伍 ───
    all_system_teams = []
    system_pet_cache = {}

    for i, sys_def in enumerate(ALL_SYSTEMS):
        sys_name = sys_def["name"]
        bonus = sys_def["bonus"]

        # 发现体系精灵
        sys_pets = discover_system_pets(sys_def)
        sys_pet_names = [p[0] for p in sys_pets]
        system_pet_cache[sys_name] = sys_pets

        # 构建候选池：体系精灵优先 + 补位高分精灵（排除首领）
        sys_pool = sys_pet_names[:20]  # 前20个体系精灵
        fill_pool = [n for n in noboss_pool if n not in set(sys_pool)]
        full_pool = sys_pool + fill_pool

        best_team = None
        best_total = -9999
        best_info = None

        # 策略：优先尝试6只全体系精灵，再逐步扩大候选池
        if sys_def["type"] == "center":
            # 中心型：强制包含核心精灵，强制6只都是体系精灵
            for cp in sys_def["center_pets"]:
                if cp not in score_map:
                    continue
                # 策略1: 核心 + 体系池，纯体系队
                for core_size in [5, 4, 3, 2]:
                    core = [cp] + [n for n in sys_pool[:core_size] if n != cp]
                    team, total, info = greedy_fill_team(
                        core, bonus, sys_def, full_pool, sys_pool, True
                    )
                    if team and total > best_total:
                        best_team, best_total, best_info = team, total, info
        elif sys_def["type"] == "weather":
            # 天气体系：先搜天气手，强制6只都是体系精灵
            weather_skill = sys_def["weather_skill"]
            weather_setters = [n for n in full_pool if has_skill_match(n, [weather_skill])]
            for ws in weather_setters[:3]:
                # 策略1: 天气手 + 体系池，纯体系队
                for core_size in [5, 4, 3, 2]:
                    core = [ws] + [n for n in sys_pool[:core_size] if n != ws]
                    team, total, info = greedy_fill_team(
                        core, bonus, sys_def, full_pool, sys_pool, True
                    )
                    if team and total > best_total:
                        best_team, best_total, best_info = team, total, info
        else:
            # 机制体系：从体系池取核心，强制6只都是体系精灵
            for core_size in [5, 4, 3]:
                core = sys_pool[:core_size]
                if not core:
                    continue
                team, total, info = greedy_fill_team(
                    core, bonus, sys_def, full_pool, sys_pool, True
                )
                if team and total > best_total:
                    best_team, best_total, best_info = team, total, info

        if best_team is None:
            # 回退：纯高分队（不用首领），中心型必须含核心
            if sys_def["type"] == "center":
                for cp in sys_def["center_pets"]:
                    if cp not in score_map:
                        continue
                    core = [cp]
                    best_team, best_total, best_info = greedy_fill_team(
                        core, bonus, sys_def, full_pool, sys_pool, True
                    )
                    if best_team:
                        break
            if best_team is None:
                best_team, best_total, best_info = greedy_fill_team([], 0, None, noboss_pool)

        # 调整技能：确保发动机技能被携带
        def adjust_skills(team_members, sys_def):
            adjusted = {}
            for name in team_members:
                rec_sk = recommended.get(name, [])
                if not rec_sk:
                    adjusted[name] = []
                    continue
                ls_map = {sk["name"]: sk for sk in learnsets.get(name, [])}
                engine_skills = sys_def.get("engine_skills", [])
                for es in engine_skills:
                    if es not in ls_map:
                        continue
                    has_es = any(sk.get("name") == es for sk in rec_sk)
                    if not has_es:
                        scored = [(sk, skill_scores.get(sk.get("name", ""), 0))
                                  for sk in rec_sk]
                        scored.sort(key=lambda x: x[1])
                        for old_sk, _ in scored:
                            if "减伤" not in old_sk.get("desc", ""):
                                rec_sk = [ls_map[es] if s.get("name") == old_sk.get("name") else s
                                         for s in rec_sk]
                                break
                # 天气体系：确保天气手携带天气技能
                if sys_def["type"] == "weather":
                    weather_skill = sys_def["weather_skill"]
                    if weather_skill in ls_map:
                        has_ws = any(sk.get("name") == weather_skill for sk in rec_sk)
                        if not has_ws:
                            scored = [(sk, skill_scores.get(sk.get("name", ""), 0))
                                      for sk in rec_sk]
                            scored.sort(key=lambda x: x[1])
                            for old_sk, _ in scored:
                                if "减伤" not in old_sk.get("desc", ""):
                                    rec_sk = [ls_map[weather_skill] if s.get("name") == old_sk.get("name") else s
                                             for s in rec_sk]
                                    break
                adjusted[name] = rec_sk
            return adjusted

        team_skills = adjust_skills(best_team, sys_def)

        # 收集结果
        sys_fit = sum(score_system_fit(m, sys_def) for m in best_team)
        all_system_teams.append({
            "system": sys_def,
            "members": best_team,
            "total": best_total,
            "info": best_info,
            "skills": team_skills,
            "sys_fit": sys_fit,
        })

    # ─── 输出所有体系队伍 ───
    # 按总分排序
    all_system_teams.sort(key=lambda x: -x["total"])

    for rank, st in enumerate(all_system_teams, 1):
        sys_def = st["system"]
        team = st["members"]
        info = st["info"]
        team_skills = st["skills"]

        purity_str = f" 纯净=+{info.get('purity', 0)}" if info.get('purity', 0) else ""
        redundancy_str = f" 冗余=+{info.get('redundancy', 0)}" if info.get('redundancy', 0) else ""
        control_str = f" 控制=+{info.get('control', 0)}" if info.get('control', 0) else ""
        cannon_str = f" 炮台=+{info.get('cannon', 0)}" if info.get('cannon', 0) else ""
        capture_str = f" 捕获=+{info.get('capture', 0)}" if info.get('capture', 0) else ""
        field_str = f" 场地=+{info.get('field', 0)}" if info.get('field', 0) else ""
        tolerance_str = f" 容错=+{info.get('tolerance', 0)}" if info.get('tolerance', 0) else ""
        coverage_str = f" 联防=+{info.get('coverage', 0)}" if info.get('coverage', 0) else ""
        trait_str = f" 特性=+{info.get('trait_bonus', 0):.0f}" if info.get('trait_bonus', 0) else ""
        print(f"\n{'─' * 90}")
        print(f"  [{rank:>2}] {sys_def['name']} — {sys_def['desc']}")
        print(f"      总={st['total']:.1f}  基础={info['base']:.0f}"
              f"{trait_str}  协同=+{info['synergy']}"
              f"{purity_str}{redundancy_str}{control_str}{cannon_str}{capture_str}{field_str}{tolerance_str}{coverage_str}"
              f"  罚={info.get('penalty', 0):.0f}"
              f"  防{info['defense']} 辅{info['utility']} 属{info['attr_coverage']}")
        for n in team:
            p = next((x for x in ranked if x["name"] == n), None)
            if not p:
                continue
            rec_sk = team_skills.get(n, [])
            sk_names = [sk["name"] for sk in rec_sk] if rec_sk else []
            tag = ""
            if sys_def["type"] == "center" and n in sys_def["center_pets"]:
                tag = " ★核心"
            elif score_system_fit(n, sys_def) >= 50:
                tag = " ●"
            team_trait = info.get('trait_scores', {}).get(n, p['trait_score'])
            print(f"    {n:<14}分{p['score']:>5.1f}  {str(p['attrs']):<12}"
                  f"【{p['trait_name']}={team_trait:.0f}】{tag}")
            if sk_names:
                print(f"      技能: {' · '.join(sk_names[:5])}")

    # ─── 保存结果 ───
    # 保存所有体系队伍
    from models.pet_scorer import PetScorer
    _scorer = PetScorer()
    all_teams_data = []
    for st in all_system_teams:
        sys_def = st["system"]
        members_data = {}
        for n in st["members"]:
            members_data[n] = {
                "score": score_map.get(n, 0),
                "attrs": get_attrs(n),
                "trait": pet_scores[n]["trait_name"] if n in pet_scores else "",
                "system_fit": score_system_fit(n, sys_def),
                "recommended_skills": [sk["name"] for sk in (_scorer.recommend_skills(n) or [])],
            }
        all_teams_data.append({
            "system": sys_def["name"],
            "type": sys_def["type"],
            "system_desc": sys_def["desc"],
            "members": st["members"],
            "total_score": st["total"],
            "base_score": st["info"]["base"],
            "synergy_bonus": st["info"]["synergy"],
            "system_fit_total": st["sys_fit"],
            "defense_count": st["info"]["defense"],
            "utility_count": st["info"]["utility"],
            "attr_coverage": st["info"]["attr_coverage"],
            "details": members_data,
        })

    with open(DATA_DIR / "optimal_team.json", "w") as f:
        json.dump(all_teams_data, f, ensure_ascii=False, indent=2)
    print(f"\n所有体系队伍已保存到 data/optimal_team.json ({len(all_teams_data)}队)")

    # ─── 生成队伍展示txt ───
    txt_lines = []
    txt_lines.append("=" * 100)
    txt_lines.append("洛克王国世界 — 体系队伍推荐")
    txt_lines.append("=" * 100)
    txt_lines.append("")

    type_labels = {"mechanic": "机制体系", "weather": "天气体系", "center": "核心精灵体系"}
    for st in all_system_teams:
        sys_def = st["system"]
        team = st["members"]
        info = st["info"]
        team_skills = st["skills"]

        tlabel = type_labels.get(sys_def["type"], "首领化队伍")
        trait_str = f"  特性=+{info.get('trait_bonus', 0):.0f}" if info.get('trait_bonus', 0) else ""
        txt_lines.append(f"【{tlabel}】{sys_def['name']} — {sys_def['desc']}")
        txt_lines.append(f"  总分: {st['total']:.1f}  基础={info['base']:.0f}{trait_str}  "
                        f"协同=+{info['synergy']}  体系匹配={st['sys_fit']:.0f}  "
                        f"罚={info['penalty']:.0f}")
        txt_lines.append(f"  防御:{info['defense']}/6  功能:{info['utility']}/6  "
                        f"属性覆盖:{info['attr_coverage']}种")

        for i, n in enumerate(team, 1):
            p = next((x for x in ranked if x["name"] == n), None)
            if not p:
                continue
            cd = p.get('combat_detail', {})
            actual_spd = cd.get('actual_spd', 0)
            combat_str = _format_combat(cd, p['combat_score'], actual_spd, p.get('attrs'))
            rec_sk = team_skills.get(n, [])
            sk_names = [sk["name"] for sk in rec_sk] if rec_sk else []

            tag = ""
            if sys_def["type"] == "center" and n in sys_def.get("center_pets", []):
                tag = " [核心]"
            elif score_system_fit(n, sys_def) >= 50:
                tag = " [体系]"

            team_trait = info.get('trait_scores', {}).get(n, p['trait_score'])
            txt_lines.append(f"  {i}. {n}{tag}  分{p['score']:.1f}  "
                            f"{str(p['attrs'])}  "
                            f"【{p['trait_name']}={team_trait:.0f}】")
            txt_lines.append(f"     技能: {' · '.join(sk_names[:5])}  "
                            f"{combat_str}")
        txt_lines.append("")

    team_txt_path = DATA_DIR / "all_system_teams.txt"
    team_txt_path.write_text("\n".join(txt_lines), encoding="utf-8")
    print(f"队伍推荐已保存到 data/all_system_teams.txt")

    # 保存排名文件
    lines = []
    def _rank_lines(pets_list, title, start_idx=1):
        out = []
        out.append("=" * 100)
        out.append(title)
        out.append("=" * 100)
        for idx, p in enumerate(pets_list, start_idx):
            cd = p.get('combat_detail', {})
            actual_spd = cd.get('actual_spd', 0)
            combat_str = _format_combat(cd, p['combat_score'], actual_spd, p.get('attrs'))
            sf = p.get('skill_final_scores', {})
            if sf:
                sk_parts = [f"{sn}={sf[sn]:.0f}" if sn in sf else sn for sn in p.get("recommended_skills", [])[:4]]
                sk_names = " · ".join(sk_parts)
            else:
                sk_names = " · ".join(p.get("recommended_skills", [])[:4])
            display_name = p['name'] + ("[首领]" if p['name'] in BOSS_NAMES else "")
            trait_suffix = ""
            if p.get('has_hurricane'):
                sb = p.get('swift_bonuses', {})
                sw = sum(sb.values())
                trait_suffix = f" [迅捷+{sw:.0f}]"
            out.append(
                f"{idx:>3}. {display_name:<16} {p['score']:>6.1f}  "
                f"技能={p['skill_score']:.0f} [{sk_names}] {combat_str}  "
                f"【{p['trait_name']}={p['trait_score']:.0f}{trait_suffix}】{p['trait_desc'][:50]}"
            )
        return out

    lines += _rank_lines(ranked, "精灵综合排名 (技能 + 战斗能力[攻防速])")
    (DATA_DIR / "all_pet_rankings.txt").write_text("\n".join(lines), encoding="utf-8")
    print(f"排名已保存到 data/all_pet_rankings.txt")


if __name__ == "__main__":
    main()
