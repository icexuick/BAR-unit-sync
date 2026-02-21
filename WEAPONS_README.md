# BAR Weapon Sync to Webflow

Syncs individual weapon data from Beyond All Reason to Webflow CMS "Unit Weapons" collection.

## 🎯 What it does

Parses each weapon from unit files and creates separate CMS items with detailed stats:

```
armcom (unit)
  ├─ armcom-armcomlaser (weapon 1, count: 2)
  ├─ armcom-armcomgauss (weapon 2, count: 1)
  └─ armcom-disintegrator (weapon 3, count: 1)
```

Each weapon gets **31 fields** synced to Webflow.

## 📊 Synced Fields

### Basic Info
- **Name**: `armcom-armcomlaser` (slug identifier)
- **Full Name**: `Laser` (human-readable from weapondef `name`)
- **Weapon Count**: How many times this weapon appears on the unit
- **Weapon Type**: BeamLaser, Cannon, MissileLauncher, etc.
- **Weapon Category**: Auto-detected reference to category (Beam, Missile, etc.)

### Damage Stats
- **DPS**: Calculated damage per second
- **Damage Default**: Base damage
- **Damage Commanders**: Damage vs commanders
- **Damage VTOL**: Damage vs aircraft
- **Damage Submarines**: Damage vs subs

### Weapon Stats
- **Reload Time**: Seconds between shots (5 decimals)
- **Range**: Maximum range (integer)
- **Accuracy**: Accuracy value (integer)
- **Area of Effect**: Splash radius (integer)
- **Edge Effectiveness**: Damage falloff at edge (2 decimals)
- **Impulse**: Knockback force (2 decimals)

### Projectile Stats
- **Projectiles**: Number of projectiles per shot
- **Velocity**: Projectile speed
- **Burst**: Shots in succession
- **Homing**: Tracks target (boolean)
- **Turn Rate**: How fast projectile can turn

### Cost Stats
- **Energy Per Shot**: Energy cost
- **Metal Per Shot**: Metal cost
- **Stockpile**: Can stockpile ammo (boolean)
- **Stockpile Limit**: Max ammo storage
- **Stockpile Time**: Time to build 1 ammo

### Special Properties
- **Paralyzer**: EMP weapon (boolean)
- **Paralyze Duration**: Stun duration
- **Water Weapon**: Can fire underwater (boolean)

### Visual
- **Color**: RGB color as hex (#ff8000)

## 🔧 Usage

### Test with single unit
```bash
python sync_weapons_to_webflow.py --unit armcom
```

### Dry-run (preview changes)
```bash
python sync_weapons_to_webflow.py --unit armcom --dry-run
```

### Full sync (coming soon)
```bash
python sync_weapons_to_webflow.py
```

## 📋 Setup Requirements

### 1. Webflow Collections

**Weapon Categories** (Collection ID: `6998dad0bb861bb8fa17d237`)
- Create categories: beam, missile, cannon, etc.
- Script auto-detects category based on weapon type

**Unit Weapons** (Collection ID: `699446edb237b8c196b4c683`)
- All 31 fields must be created
- Field slugs must match (see field list above)

**Units** (Collection ID: `6564c6553676389f8ba45a9e`)
- Add field: **Weapons** (MultiReference → Unit Weapons)

### 2. Environment Variables

Same `.env` as main sync:
```env
WEBFLOW_API_TOKEN=your_token_here
GITHUB_TOKEN=your_github_token (optional)
```

## 🎨 Weapon Category Detection

Auto-detects category based on rules:

| Weapon Type | Reload | Category |
|------------|--------|----------|
| BeamLaser | < 0.1s | Continuous Beam |
| BeamLaser | ≥ 0.1s | Beam |
| MissileLauncher | - | Missile |
| Cannon | - | Cannon* |
| EmgCannon | - | EMG |
| Plasma | - | Plasma |
| Flame | - | Flame |
| LaserCannon | - | Laser |
| LightningCannon | - | Lightning |
| TorpedoLauncher | - | Torpedo |
| Melee | - | Melee |
| AircraftBomb | - | Bomb |

*Cannons can be expanded with light/medium/heavy subcategories based on damage

## 🔍 How It Works

### 1. Parse Weapons
```python
weapondefs = {
    armcomlaser = {
        name = "Laser",
        damage = { default = 50 },
        reloadtime = 0.5,
        range = 300,
        ...
    }
}

weapons = {
    [1] = { def = "armcomlaser" },  # Count: 1
    [2] = { def = "armcomlaser" },  # Count: 2
}
```

### 2. Count Weapons
Tracks how many times each weapondef appears in the `weapons = {}` array

### 3. Calculate DPS
```
DPS = (damage / reload) × salvo × burst × projectiles
```

### 4. Detect Category
BeamLaser + reload < 0.1 → Continuous Beam

### 5. Sync to Webflow
Create/update weapon items as drafts

### 6. Link to Unit
Update unit's `weapons` multi-reference field

## 🎯 Example Output

```
Processing unit: armcom

  📊 Found 3 weapons
    🔫 armcom-armcomlaser (count: 2, DPS: 100)
       ✅ Created (draft)
    🔫 armcom-armcomgauss (count: 1, DPS: 250)
       ✅ Created (draft)
    🔫 armcom-disintegrator (count: 1, DPS: 500)
       ✅ Created (draft)
  
  ✅ Linked 3 weapons to unit
```

## 🚧 RGB Color Parsing

Handles both formats:

**Simple:**
```lua
rgbcolor = "1 0.5 0"  → #ff8000
```

**Table:**
```lua
rgbcolor = {
    [1] = 1,
    [2] = 0.33,
    [3] = 0.7,
}  → #ff54b3
```

## ⚠️ Limitations

- Weapons with 'bogus' or 'mine' in name are skipped
- Full sync mode not yet implemented (test with --unit first)
- Category detection is rule-based (can be expanded)

## 🔄 Integration with Main Sync

Eventually this will be integrated into the main unit sync, so weapons are automatically synced alongside units.

For now, run separately to test and verify weapon data.

## 📝 License

BAR Only - See LICENSE file
