# Roko Team — Quantitative Team Analysis for Roko Kingdom World

A data-driven team building and optimization system for **洛克王国世界 (Roko Kingdom World)**, scoring every spirit, recommending optimal skill sets, and searching for the best 6-member teams via genetic algorithms.

## Architecture

```
roko_team/
├── scripts/                  # Standalone scoring & ranking pipelines
│   ├── score_all_skills.py         # Universal skill scoring
│   ├── recommend_skills.py         # Per-pet optimal skill set recommendation
│   ├── score_traits_and_rank.py    # Pet scoring & leaderboard generation
│   └── generate_multi_teams.py     # Multi-team composition search
├── models/                   # Structured OOP scoring models
│   ├── pet_scorer.py               # Per-pet 6-dimension scoring
│   ├── team_analyzer.py            # Team synergy & constraint evaluation
│   ├── attribute_matrix.py         # Type chart & matchup calculations
│   ├── role_classifier.py          # Role detection (wall/sweeper/balanced)
│   └── system_detector.py          # Team archetype recognition
├── algorithms/
│   └── genetic_optimizer.py        # Genetic algorithm for team search
├── data/                     # Static data files
│   ├── pets.json / pets_final.json # Spirit base stats, types, traits
│   ├── pet_learnset.json           # Learnable skills per spirit
│   ├── pet_recommended.json        # Recommended 4-skill loadouts
│   ├── attribute_chart.json        # Type effectiveness matrix
│   ├── spirits_detail.json         # Full spirit & skill database
│   ├── all_pet_rankings.json       # Generated leaderboard
│   └── optimal_team.json           # Best team composition
└── utils/                    # Data pipeline
    ├── fetch_all_data.py           # Full data crawler
    └── fetch_wiki_data.py          # Wiki scraper
```

## Scoring System

Each spirit is evaluated across five independent dimensions, with **no double-counting** between them:

| Dimension | What it measures | Formula |
|-----------|-----------------|---------|
| **Attack** | Raw offensive stat × STAB | `max(atk, matk) × 0.2 × (STAB? 1.25 : 1.0)` |
| **Defense** | Physical & special bulk | `phys_def = HP × def / 180 × 0.65`, `spec_def = HP × mdef / 180 × 0.35` |
| **Speed** | Speed tier (exponential) | `max(spd − 98, 0)^1.5 × 0.32` (0 below 100) |
| **Attribute** | Type resistances + synergy | Resist: `+3.5×weight`, Weak: `−3.0×weight`, Dual-type synergy bonus |
| **Skill** | Skill power, cost, effects | Per-skill scoring on power efficiency, marks, buffs, control |

**Key design principles:**
- **Damage formula basis**: Defense uses `HP × Defense` (damage ∝ ATK/DEF, soak ∝ HP×DEF)
- **No double-counting**: Skill power is scored *only* in the Skill dimension; Attack dimension uses *race stats only*
- **Type coverage**: Skill elements feed into offensive coverage scoring (attack dimension), while pet type resistances feed into the Attribute dimension
- **Speed is exponential**: Below 100 base speed gives 0 points; 125+ is where real advantage starts
- **Trait-specific adjustments**: Traits like "lose half HP on entry" directly modify the effective HP before defense calculation

### Run scoring pipeline

```bash
# 1. Score all skills
python scripts/score_all_skills.py

# 2. Recommend optimal 4-skill sets per spirit
python scripts/recommend_skills.py

# 3. Score all spirits and rank them
python scripts/score_traits_and_rank.py
```

Outputs: `data/all_pet_rankings.json`, `data/all_pet_rankings.txt`, `data/optimal_team.json`

## Team Optimization

The system searches for optimal 6-spirit teams using:

1. **Greedy filling** — Start from synergy cores (Rain team, Thunder team, etc.) and fill remaining slots by score
2. **Genetic algorithm** — Population-based search with crossover/mutation, constrained by type limits (max 2 same-type) and role requirements (3+ defense skills)

Team evaluation includes:
- Base score sum of all members
- Synergy bonuses for system packages (rain, thunder, shadow)
- Penalties for missing defense coverage (< 3 tanks)
- Penalties for trait anti-synergy (e.g.,奉献 without same-type teammates)

### Run full pipeline

```bash
python main.py
```

## Key Design Decisions

- **Physical meta weighting**: 65% physical / 35% special — reflects the current PvP environment where physical attackers dominate
- **Chess family (棋类)**: Only the boss form `棋契国王` is scored; all intermediate chess forms are excluded to avoid duplication
- **Trait HP modifier**: Spirits with "lose half HP on entry" traits have their HP halved *before* defense calculation, accurately modeling their reduced durability
- **Balanced-type assumption**: All spirits treated as balanced — no role-based stat reweighting, purely data-driven

## Data Sources

All spirit data, skill data, and type charts are sourced from the Roko Kingdom World game database via the crawlers in `utils/`.
