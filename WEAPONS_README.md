# BAR Weapon Sync — `sync_weapons_to_webflow.py`

Syncs weapon data from Beyond All Reason unit files to the Webflow CMS **Unit Weapons** collection. Each weapon becomes a separate CMS item with full stats, auto-detected category, and a back-reference to its parent unit.

```
armcom (unit)
  ├─ armcom-armcomlaser    (weapon 1, count: 2)
  ├─ armcom-armcomgauss    (weapon 2, count: 1)
  └─ armcom-disintegrator  (weapon 3, count: 1)
```

---

## 🚀 Usage

```bash
# Test a single unit (dry-run — no writes)
python sync_weapons_to_webflow.py --unit armcom --dry-run

# Sync a single unit (creates/updates in Webflow as draft)
python sync_weapons_to_webflow.py --unit armcom

# Sync a single unit and publish immediately
python sync_weapons_to_webflow.py --unit armcom --publish

# Sync all units in Webflow
python sync_weapons_to_webflow.py --all
python sync_weapons_to_webflow.py --all --dry-run
python sync_weapons_to_webflow.py --all --publish

# Sync only mines and crawling bombs
python sync_weapons_to_webflow.py --mines
python sync_weapons_to_webflow.py --mines --publish
```

---

## 📊 Synced Fields

### Basic Info
| Field | Webflow slug | Notes |
|---|---|---|
| Name | `name` | `unitname-weapondefkey` e.g. `armcom-armcomlaser` |
| Full Name | `full-name` | Human-readable from weapondef `name` field |
| Weapon Count | `weapon-count` | Times this weapondef appears on the unit |
| Weapon Type | `weapon-type` | `BeamLaser`, `Cannon`, `MissileLauncher`, etc. |
| Weapon Category | `weapon-category` | Auto-detected reference (see category table below) |

### Damage Stats
| Field | Webflow slug | Notes |
|---|---|---|
| DPS | `dps` | Calculated (see formula below) |
| DOT | `dot` | Damage Over Time (cluster, napalm, lightning chain) |
| PPS | `pps` | Paralyse Per Second (EMP/paralyzer weapons only) |
| Damage Default | `damage-default` | Base damage (× sweepfire if applicable) |
| Damage Commanders | `damage-commanders` | Bonus vs commanders |
| Damage VTOL | `damage-vtol` | Bonus vs aircraft |
| Damage Submarines | `damage-submarines` | Bonus vs subs |

### Weapon Stats
| Field | Webflow slug | Notes |
|---|---|---|
| Reload Time | `reload-time` | Seconds between shots (5 decimals) |
| Range | `range` | Maximum range (integer) |
| Accuracy | `accuracy` | Spread/inaccuracy value |
| Area of Effect | `area-of-effect` | Splash radius |
| Edge Effectiveness | `edge-effectiveness` | Damage falloff at splash edge (2 decimals) |
| Impulse | `impulse` | Knockback force (`impulsefactor`, 2 decimals) |

### Projectile Stats
| Field | Webflow slug | Notes |
|---|---|---|
| Projectiles | `projectiles` | Projectiles per shot |
| Velocity | `velocity` | Projectile speed |
| Burst | `burst` | Rapid-fire shots before reload |
| Salvo Size | `salvo-size` | Shots per salvo |
| Homing | `homing` | Tracks target (boolean, from `tracks`) |
| Turn Rate | `turn-rate` | How fast homing projectile turns |

### Cost Stats
| Field | Webflow slug | Notes |
|---|---|---|
| Energy Per Shot | `energy-per-shot` | Energy cost per shot |
| Metal Per Shot | `metal-per-shot` | Metal cost per shot |
| Stockpile | `stockpile` | Can stockpile ammo (boolean) |
| Stockpile Limit | `stockpile-limit` | From `customparams.stockpilelimit` |
| Stockpile Time | `stockpile-time` | Time to produce 1 ammo |

### Special Properties
| Field | Webflow slug | Notes |
|---|---|---|
| Paralyzer | `paralyzer` | EMP weapon (boolean) |
| Paralyze Duration | `paralyze-duration` | Stun duration in seconds |
| Water Weapon | `water-weapon` | Can fire underwater |
| Command Fire | `commandfire` | Requires explicit fire order |

### Target Capabilities
| Field | Webflow slug |
|---|---|
| Can Target Surface | `can-target-surface` |
| Can Target Air | `can-target-air` |
| Can Target Subs | `can-target-subs` |

### Visual
| Field | Webflow slug |
|---|---|
| Color | `color` | RGB as hex e.g. `#ff8000` |

### Linked Unit
| Field | Webflow slug |
|---|---|
| Parent Unit | `unit` | Reference to the unit this weapon belongs to |

---

## 🧮 DPS Formula

```
DPS = (damage / reload) × salvosize × burst × projectiles
```

Where `damage = max(damage_vtol, damage_default)`.

**Special cases:**
- **Sweepfire**: if `customparams.sweepfire = N`, `damage_default` is multiplied by N before DPS calculation
- **Paralyzer weapons**: DPS = 0, PPS is calculated instead
- **Shield weapons**: DPS = 0, DOT = 0, PPS = 0
- **Commented-out damage values** (e.g. `--vtol = 400`) are stripped before parsing

---

## 🏷️ Weapon Category Detection

Categories are auto-detected in priority order. **First match wins.**

| Priority | Category | Weapon Type | Conditions |
|---|---|---|---|
| 1 | **Trigger EMP** | any | Explode unit (`_is_mine`) + `paralyzer = true` (spy bombs, EMP buildings) |
| 2 | **Trigger Explosive** | any | Explode unit (`_is_mine`) without paralyzer (crawling bombs, mines) |
| 3 | **Anti-Nuke** | StarburstLauncher | `interceptor = 1` |
| 4 | **Crush / Stomp** | Cannon | `range < 60` + `customparams.nofire = true` |
| 5 | **Napalm Launcher** | Cannon | `customparams.area_onhit_damage` present |
| 6 | **Cluster Plasma Cannon** | Cannon | `customparams.cluster_number` present |
| 7 | **Sniper** | Cannon | `accuracy = 0` + `range > 800` + `damage > 250` |
| 8 | **Railgun** | LaserCannon | `customparams.overpenetrate = true` |
| 9 | **Heat Ray** | BeamLaser | `reload < 0.1` (continuous beam) |
| 10 | **Tachyon Laser Beam** | BeamLaser | `reload ≥ 0.1` |
| 11 | **Sea Laser Cannon** | LaserCannon | `waterweapon = true` |
| 12 | **Nuclear Missile** | StarburstLauncher | `customparams.nuclear=1` + `commandfire=true` + `damage ≥ 8000` |
| 13 | **Tactical Missile** | StarburstLauncher | `customparams.nuclear=1` + `commandfire=true` + `damage < 8000` |
| 14 | **Missile Launcher** | MissileLauncher | `tracks = true` (homing) |
| 15 | **Rocket Launcher** | MissileLauncher | `tracks = false` (non-homing) |
| 16 | **Vertical Rocket Launcher** | StarburstLauncher | (all others) |
| 17 | **Flak Cannon** | Cannon | `can_target_air` + flak color OR `'flak'` in name |
| 18 | **Plasma Repeater** | Cannon | `burst ≥ 3` + `reload ≤ 0.7` |
| 19 | **Plasma Shotgun** | Cannon | `projectiles ≥ 3` |
| 20 | **Plasma Blast** | Cannon | `impulsefactor ≥ 0.5` |
| 21 | **Cannon** | Cannon | (all others) |
| 22 | **EMG Cannon** | EmgCannon | — |
| 23 | **Plasma** | Plasma | — |
| 24 | **Flamethrower** | Flame | — |
| 25 | **Gatling Gun** | LaserCannon | `reload < 0.5` + `burst ≥ 3` |
| 26 | **Shotgun Cannon** | LaserCannon | `projectiles ≥ 3` |
| 27 | **Laser Cannon** | LaserCannon | (all others) |
| 28 | **Lightning Cannon** | LightningCannon | — |
| 29 | **Torpedo** | TorpedoLauncher | `tracks = true` (homing) |
| 29b | **Dumb-fire Torpedo** | TorpedoLauncher | `tracks = false` or absent (unguided) |
| 30 | **Shield** | Shield | — |
| 31 | **Aircraft EMP Bomb** | AircraftBomb | `paralyzer = true` |
| 32 | **Aircraft Bomb** | AircraftBomb | — |
| 33 | **D-Gun** | DGun | `'disintegrator'` in weapondef key name |
| 34 | **Disintegrator Cannon** | DGun | `weapontype = DGun` but NOT a disintegrator beam (e.g. corjugg) |
| 35 | **Melee** | Melee | — |

---

## 💣 Mine, Crawling Bomb & Explode Unit Detection

These unit types have **no real weapondefs** in their unit file — their explosion weapon lives in an external file under `weapons/`. The script detects them and fetches the correct weapon file automatically.

### Mine
Detected when `customparams.mine = true` in the unitdef.

### Crawling Bomb
Detected when **all three** of these are present:
- `selfdestructcountdown = 0` (root level)
- `customparams.unitgroup = "explo"`
- `customparams.instantselfd = true`

Examples: `armvader`, `corroach`, `corsktl`, `legsnapper`

### Spy Bomb
Detected when **both** of these are present:
- `selfdestructcountdown = 0` (root level)
- `customparams.unitgroup = "buildert2"`

Uses the `SPYBOMBX` paralyzer weapon (EMP self-destruct). Assigned **Trigger EMP** category.

Examples: `armspy`, `corspy`, `legaspy`

### EMP Building
Detected when `selfdestructas = "empblast"` is present in the unitdef.

Uses the `empblast` paralyzer weapon from `Unit_Explosions.lua`. Assigned **Trigger EMP** category.

Examples: `armamex`

### selfdestructas preferred over explodeas
For all explode unit types, **`selfdestructas` is always preferred** over `explodeas` when both are present — `selfdestructas` is the contact/self-destruct explosion (higher damage), while `explodeas` is only the shot-down explosion.

### How it works
1. Checks for `selfdestructas` first, falls back to `explodeas`
2. Tries `weapons/<key>.lua` directly (fast path)
3. If not found: scans all `.lua` files in `weapons/` to build an index, then looks up which file contains the key (slow path, cached for the rest of the run)
4. Parses the weapondef from that file
5. Assigns **Trigger EMP** (if paralyzer) or **Trigger Explosive** (otherwise)
6. All weapondefs in the unitdef itself are ignored

---

## ⚙️ Special Parsing Rules

### Sweepfire
If `customparams.sweepfire = N` is present in a weapondef:
- `damage_default` is multiplied by N (shown in Webflow)
- DPS is calculated from the already-multiplied damage value

### Bogus weapons — skipped
A weapon is skipped if any of the following is true:
- `customparams.bogus = 1`
- `'bogus'` appears in the weapondef key name
- `'mine'` appears in the weapondef key name
- `'detonator'` appears in the weapondef key name (contact-trigger weapons on crawling bombs, `range = 1`)

**Exception:** Crush/stomp weapons are never skipped even if flagged bogus.

### Smart backup weapons — skipped for DPS
Weapons with `customparams.smart_backup = true` are excluded from the parent unit's DPS total (they are an alternative fire mode, not a primary weapon).

### Commented-out Lua values
All `--` comments are stripped from the damage block before parsing, so values like `--vtol = 400` are correctly ignored.

---

## 🔧 Configuration

Constants at the top of `sync_weapons_to_webflow.py`:

```python
GITHUB_REPO            = "beyond-all-reason/Beyond-All-Reason"
GITHUB_BRANCH          = "master"
WEBFLOW_SITE_ID        = "5c68622246b367adf6f3041d"
WEAPONS_COLLECTION_ID  = "..."   # Unit Weapons collection
UNITS_COLLECTION_ID    = "6564c6553676389f8ba45a9e"
CATEGORIES_COLLECTION_ID = "..."
```

### Environment variables

```env
WEBFLOW_API_TOKEN=your_token_here
GITHUB_TOKEN=your_github_token     # optional but recommended (avoids rate limits)
```

---

## 📝 License

**Custom License — Beyond All Reason Use Only**
See [LICENSE](LICENSE) for full terms.
