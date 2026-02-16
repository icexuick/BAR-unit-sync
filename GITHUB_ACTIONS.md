# GitHub Actions Uitschakelen / Inschakelen

## ❌ Tijdelijk uitschakelen (Aanbevolen voor testen)

De GitHub Actions workflow is standaard **al uitgeschakeld** voor handmatig gebruik.

De workflow draait alleen als:
1. Je handmatig op "Run workflow" klikt in GitHub UI, OF
2. Het volgens de schedule (dagelijks 2:00 UTC) moet draaien

### Om de automatische schedule uit te zetten:

Verwijder of comment deze regels in `.github/workflows/sync-to-webflow.yml`:

```yaml
# Verwijder of comment deze sectie:
schedule:
  - cron: '0 2 * * *'
```

Wordt:

```yaml
# schedule:
#   - cron: '0 2 * * *'
```

Nu draait de workflow ALLEEN handmatig wanneer jij op de knop drukt!

## ✅ Handmatig workflow uitvoeren (voor testen)

1. Ga naar je repository op GitHub
2. Klik op "Actions" tab
3. Klik op "Sync Units to Webflow" (links)
4. Klik op "Run workflow" (rechts)
5. Kies opties:
   - ☑️ **Run in dry-run mode** (aanvinken voor test!)
   - ☐ Auto-publish (uitvinken voor test)
6. Klik "Run workflow"

De workflow voert dan een dry-run uit en laat je zien wat er zou gebeuren.

## 🔒 Workflow compleet uitschakelen

### Optie 1: Workflow file verwijderen
Verwijder het bestand `.github/workflows/sync-to-webflow.yml`

### Optie 2: Workflow disablen in GitHub
1. Ga naar repository → Settings
2. Klik op "Actions" → "General"
3. Onder "Actions permissions", selecteer "Disable actions"

### Optie 3: Workflow bestand hernoemen
Hernoem `.github/workflows/sync-to-webflow.yml` naar `.github/workflows/sync-to-webflow.yml.disabled`

## 🧪 Testen zonder GitHub Actions

### Test lokaal met één unit:

```bash
# Dry run voor armfast
python sync_single_unit.py armfast --dry-run

# Echte sync voor armfast
python sync_single_unit.py armfast

# Sync en publiceer
python sync_single_unit.py armfast --publish
```

### Test lokaal met alle units:

```bash
# Dry run voor alle units
python sync_units_github_to_webflow.py --dry-run

# Echte sync (zonder publiceren)
python sync_units_github_to_webflow.py

# Sync en publiceer alles
python sync_units_github_to_webflow.py --publish
```

## ⚙️ Aanbevolen workflow voor testen:

1. **Eerst**: Test met één unit
   ```bash
   python sync_single_unit.py armfast --dry-run
   ```

2. **Controleer** de output - klopt het?

3. **Sync één unit** (zonder publish)
   ```bash
   python sync_single_unit.py armfast
   ```

4. **Controleer in Webflow** - is de data correct?

5. **Als alles goed is**: Test met alle units
   ```bash
   python sync_units_github_to_webflow.py --dry-run
   ```

6. **Finaal**: Sync alle units
   ```bash
   python sync_units_github_to_webflow.py --publish
   ```

7. **Pas daarna**: Schakel GitHub Actions schedule in (als je wilt)
