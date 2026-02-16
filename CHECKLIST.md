# ✅ Repository Setup Checklist

## Bestanden die in je GitHub repository moeten staan:

### 📄 Hoofdbestanden (VERPLICHT)
- [ ] `sync_units_github_to_webflow.py` - Hoofd sync script
- [ ] `sync_single_unit.py` - **NIEUW!** Test script voor één unit
- [ ] `requirements.txt` - Python dependencies
- [ ] `README.md` - Hoofddocumentatie
- [ ] `.gitignore` - Git ignore regels

### 📚 Documentatie (AANBEVOLEN)
- [ ] `QUICKSTART.md` - Snelstart gids
- [ ] `GITHUB_ACTIONS.md` - **NIEUW!** Uitleg GitHub Actions
- [ ] `CONTRIBUTING.md` - Bijdrage richtlijnen
- [ ] `CHANGELOG.md` - Versie geschiedenis
- [ ] `LICENSE` - MIT License

### 🛠️ Hulp scripts (OPTIONEEL)
- [ ] `test_sync.py` - Unit test script
- [ ] `setup.sh` - Automatische installatie (Linux/Mac)
- [ ] `.env.example` - Configuratie template

### ⚙️ GitHub Actions (OPTIONEEL - UIT LATEN!)
- [ ] `.github/workflows/sync-to-webflow.yml` - **PAS OP!** Laat schedule uit staan

## 🚨 Belangrijk voor testen:

### 1. GitHub Actions Schedule UITSCHAKELEN

In `.github/workflows/sync-to-webflow.yml`, comment de schedule regel:

```yaml
on:
  # schedule:                    # ← Comment deze 2 regels!
  #   - cron: '0 2 * * *'       # ← Comment deze 2 regels!
  workflow_dispatch:            # ← Dit laten staan!
```

OF verwijder het hele `.github/workflows/` folder voor nu.

### 2. GEEN .env bestand uploaden!

- ❌ Upload NOOIT je `.env` bestand (met je API token!)
- ✅ Upload WEL `.env.example` (zonder token)
- ✅ Voeg `.env` toe aan `.gitignore` (al gedaan)

## 📝 Wat heb je nodig:

### Om lokaal te testen:
1. Python 3.7 of hoger
2. Webflow API token (van https://webflow.com/dashboard/account/apps)
3. De bestanden gedownload van deze repository

### Voor GitHub Actions (later):
1. Repository secret toevoegen: `WEBFLOW_API_TOKEN`
2. Schedule regel uncommenten in workflow file

## 🧪 Eerste test uitvoeren:

```bash
# 1. Clone je repository
git clone https://github.com/icexuick/bar-unit-sync.git
cd bar-unit-sync

# 2. Installeer dependencies
pip install -r requirements.txt

# 3. Maak .env bestand
cp .env.example .env
# Edit .env en voeg je WEBFLOW_API_TOKEN toe

# 4. Test met armfast (dry run)
python sync_single_unit.py armfast --dry-run

# 5. Als het goed is, sync armfast echt
python sync_single_unit.py armfast

# 6. Controleer in Webflow of de data correct is!
```

## ✅ Verificatie

Controleer of je deze bestanden hebt geüpload naar GitHub:

```bash
# Minimaal deze bestanden:
bar-unit-sync/
├── sync_units_github_to_webflow.py  ✅
├── sync_single_unit.py              ✅ NIEUW!
├── requirements.txt                  ✅
├── README.md                         ✅
├── .gitignore                        ✅
└── .env.example                      ✅
```

## 🔍 Check je repository:

1. Ga naar https://github.com/icexuick/bar-unit-sync
2. Controleer of bovenstaande bestanden er staan
3. Check of `.github/workflows/` folder er NIET is (of schedule is uitgecomment)
4. Kijk of er GEEN `.env` bestand staat (met je token!)

## 📞 Volgende stappen na upload:

1. **Test lokaal** met `sync_single_unit.py armfast --dry-run`
2. **Verifieer** dat de data klopt
3. **Sync één unit** zonder dry-run
4. **Check Webflow** of het werkt
5. **Rapporteer terug** wat je ziet!
