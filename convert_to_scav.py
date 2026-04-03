"""
Convert ARM (blue), COR (red), and LEG (green) unit buildpics to scavenger purple tint.
Only targets team-color pixels, leaves everything else (backgrounds, effects) untouched.
Faction is detected from filename: arm* = blue, cor* = red, leg* = green.
"""
import numpy as np
from PIL import Image
import os
import sys

# ── Target purple in HSV ──────────────────────────────────────────
TARGET_HUE = 280 / 360.0        # purple hue (0-1 scale)
PURPLE_SAT_BOOST = 1.30         # boost saturation for vivid purple
PURPLE_SAT_MIN = 0.55           # ensure minimum saturation for purple pixels

# ── Team color detection thresholds ───────────────────────────────
# ARM blue team color:  H 190-250°, S > 0.55, V > 0.15
# Sat threshold 0.55 filters out blue water backgrounds while keeping vivid team color
ARM_HUE_MIN = 190 / 360.0
ARM_HUE_MAX = 250 / 360.0
ARM_SAT_MIN = 0.55
ARM_VAL_MIN = 0.15

# COR red team color:   H 0-15° OR 345-360°, S > 0.55, V > 0.20
# Tight ranges to avoid brown/orange/warm backgrounds
COR_HUE_RED_MAX = 15 / 360.0    # pure red only, not orange
COR_HUE_RED_MIN2 = 345 / 360.0  # high end of red/crimson
COR_SAT_MIN = 0.55              # higher threshold to skip brown/beige
COR_VAL_MIN = 0.20

# LEG green team color:  H 95-145°, S > 0.45, V > 0.30
# Neon green team color sits at hue 110-130 with high sat/val
# Lighter green accents sit at sat 0.45-0.60 — still team color
# Grass background is hue 70-95 with lower sat — safely excluded
LEG_HUE_MIN = 95 / 360.0
LEG_HUE_MAX = 145 / 360.0
LEG_SAT_MIN = 0.45
LEG_VAL_MIN = 0.30


# COR units that use blue team color instead of red (ships)
COR_BLUE_OVERRIDES = {"corslrpc", "coresuppt3"}

# LEG units with bright energy glow that extends into yellow-green and cyan hues.
# These need a wider hue range (60-175°) with flood-fill background exclusion.
LEG_GLOW_OVERRIDES = {"legfus", "legafus", "legdeflector"}

# Units to skip entirely — converted manually (e.g. in Photoshop)
SKIP_UNITS = {"legarad"}


def detect_faction(filename):
    """Detect faction from filename: arm* = ARM (blue), cor* = COR (red), leg* = LEG (green)."""
    base = os.path.basename(filename).lower().replace(".webp", "")
    if base in COR_BLUE_OVERRIDES:
        return "COR_BLUE"
    elif base in LEG_GLOW_OVERRIDES:
        return "LEG_GLOW"
    elif base.startswith("arm"):
        return "ARM"
    elif base.startswith("cor"):
        return "COR"
    elif base.startswith("leg"):
        return "LEG"
    else:
        return "UNKNOWN"


def flood_fill_background(arr, tolerance=0.10):
    """Flood-fill from corners to detect background pixels."""
    from collections import deque
    h, w, _ = arr.shape
    visited = np.zeros((h, w), dtype=bool)
    bg_mask = np.zeros((h, w), dtype=bool)
    corners = []
    for cy, cx in [(0, 0), (0, w-1), (h-1, 0), (h-1, w-1)]:
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                ny, nx = cy + dy, cx + dx
                if 0 <= ny < h and 0 <= nx < w:
                    corners.append((ny, nx))
    queue = deque(corners)
    for y, x in corners:
        visited[y, x] = True
        bg_mask[y, x] = True
    while queue:
        y, x = queue.popleft()
        ref = arr[y, x]
        for dy, dx in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx]:
                visited[ny, nx] = True
                diff = np.sqrt(np.sum((arr[ny, nx] - ref) ** 2))
                if diff < tolerance:
                    bg_mask[ny, nx] = True
                    queue.append((ny, nx))
    return bg_mask


def convert_to_purple(input_path, output_path):
    """Convert team-color pixels to purple, leave everything else untouched."""
    img = Image.open(input_path).convert("RGB")
    arr = np.array(img, dtype=np.float64) / 255.0
    h, w, _ = arr.shape

    # Convert to HSV per-pixel using vectorized approach
    r, g, b = arr[:,:,0], arr[:,:,1], arr[:,:,2]

    cmax = np.maximum(np.maximum(r, g), b)
    cmin = np.minimum(np.minimum(r, g), b)
    delta = cmax - cmin

    # Hue calculation
    hue = np.zeros_like(delta)
    mask_r = (cmax == r) & (delta > 0)
    mask_g = (cmax == g) & (delta > 0)
    mask_b = (cmax == b) & (delta > 0)

    hue[mask_r] = (60 * ((g[mask_r] - b[mask_r]) / delta[mask_r]) + 360) % 360
    hue[mask_g] = (60 * ((b[mask_g] - r[mask_g]) / delta[mask_g]) + 120) % 360
    hue[mask_b] = (60 * ((r[mask_b] - g[mask_b]) / delta[mask_b]) + 240) % 360

    # Saturation
    sat = np.where(cmax > 0, delta / cmax, 0)

    # Value
    val = cmax

    # Normalize hue to 0-1
    hue_norm = hue / 360.0

    # Detect faction from filename
    faction = detect_faction(input_path)

    # Build team-color mask based on faction
    if faction == "ARM":
        # Blue team color only
        team_mask = (
            (hue_norm >= ARM_HUE_MIN) &
            (hue_norm <= ARM_HUE_MAX) &
            (sat >= ARM_SAT_MIN) &
            (val >= ARM_VAL_MIN)
        )
        print(f"  Faction: ARM (blue -> purple)")
    elif faction == "COR_BLUE":
        # COR units with blue team color on water — need higher sat to skip water
        team_mask = (
            (hue_norm >= ARM_HUE_MIN) &
            (hue_norm <= ARM_HUE_MAX) &
            (sat >= 0.65) &
            (val >= 0.20)
        )
        print(f"  Faction: COR_BLUE (blue -> purple, water-safe)")
    elif faction == "COR":
        # Red team color only (tight range to avoid brown backgrounds)
        team_mask = (
            ((hue_norm <= COR_HUE_RED_MAX) | (hue_norm >= COR_HUE_RED_MIN2)) &
            (sat >= COR_SAT_MIN) &
            (val >= COR_VAL_MIN)
        )
        print(f"  Faction: COR (red -> purple)")
    elif faction == "LEG_GLOW":
        # Energy glow units (legfus, legafus): green extends into yellow-green (H60-95)
        # and cyan (H145-175). Use flood-fill to exclude grass background.
        bg_mask = flood_fill_background(arr, tolerance=0.10)
        print(f"  Background: {100*np.sum(bg_mask)/(h*w):.1f}% (flood-fill)")

        # Standard LEG green range
        standard = (
            ((hue_norm >= LEG_HUE_MIN) & (hue_norm <= LEG_HUE_MAX) & (sat >= LEG_SAT_MIN) & (val >= LEG_VAL_MIN)) |
            ((hue_norm >= LEG_HUE_MIN) & (hue_norm <= LEG_HUE_MAX) & (sat >= 0.25) & (sat < LEG_SAT_MIN) & (val >= 0.60))
        ) & ~bg_mask

        # Extended: yellow-green glow (H60-95) and cyan glow (H145-175)
        wide_glow = (
            (hue_norm >= 60/360.0) & (hue_norm <= 175/360.0) &
            (sat >= 0.50) & (val >= 0.50) & ((sat * val) >= 0.35) &
            ~bg_mask
        )

        team_mask = standard | wide_glow
        print(f"  Faction: LEG_GLOW (green+cyan -> purple, bg-excluded)")
    elif faction == "LEG":
        # Green team color: saturated green + light/pastel green accents
        # Two tiers: normal green (sat>=0.45) and light green stripes (sat>=0.25, val>=0.60)
        saturated_green = (
            (hue_norm >= LEG_HUE_MIN) &
            (hue_norm <= LEG_HUE_MAX) &
            (sat >= LEG_SAT_MIN) &
            (val >= LEG_VAL_MIN)
        )
        light_green = (
            (hue_norm >= LEG_HUE_MIN) &
            (hue_norm <= LEG_HUE_MAX) &
            (sat >= 0.25) &
            (sat < LEG_SAT_MIN) &
            (val >= 0.60)
        )
        team_mask = saturated_green | light_green
        print(f"  Faction: LEG (green -> purple)")
    else:
        # Unknown prefix — target blue, red, and green
        blue_mask = (
            (hue_norm >= ARM_HUE_MIN) &
            (hue_norm <= ARM_HUE_MAX) &
            (sat >= ARM_SAT_MIN) &
            (val >= ARM_VAL_MIN)
        )
        red_mask = (
            ((hue_norm <= COR_HUE_RED_MAX) | (hue_norm >= COR_HUE_RED_MIN2)) &
            (sat >= COR_SAT_MIN) &
            (val >= COR_VAL_MIN)
        )
        green_mask = (
            (hue_norm >= LEG_HUE_MIN) &
            (hue_norm <= LEG_HUE_MAX) &
            (sat >= LEG_SAT_MIN) &
            (val >= LEG_VAL_MIN)
        )
        team_mask = blue_mask | red_mask | green_mask
        print(f"  Faction: UNKNOWN (targeting blue+red+green)")

    pixel_count = np.sum(team_mask)
    total_pixels = h * w
    print(f"  Team-color pixels: {pixel_count} / {total_pixels} ({100*pixel_count/total_pixels:.1f}%)")

    # Shift only team-color pixels to purple
    new_hue = hue_norm.copy()
    new_sat = sat.copy()

    new_hue[team_mask] = TARGET_HUE
    # Boost saturation and ensure minimum for vivid purple
    boosted = np.maximum(sat[team_mask] * PURPLE_SAT_BOOST, PURPLE_SAT_MIN)
    new_sat[team_mask] = np.minimum(boosted, 1.0)

    # Convert back to RGB
    # Vectorized HSV → RGB
    h6 = new_hue * 6.0
    sector = h6.astype(int) % 6
    f = h6 - h6.astype(int)

    p = val * (1 - new_sat)
    q = val * (1 - new_sat * f)
    t = val * (1 - new_sat * (1 - f))

    # Build RGB channels based on sector
    new_r = np.zeros_like(val)
    new_g = np.zeros_like(val)
    new_b = np.zeros_like(val)

    s0 = sector == 0
    new_r[s0] = val[s0]; new_g[s0] = t[s0]; new_b[s0] = p[s0]
    s1 = sector == 1
    new_r[s1] = q[s1]; new_g[s1] = val[s1]; new_b[s1] = p[s1]
    s2 = sector == 2
    new_r[s2] = p[s2]; new_g[s2] = val[s2]; new_b[s2] = t[s2]
    s3 = sector == 3
    new_r[s3] = p[s3]; new_g[s3] = q[s3]; new_b[s3] = val[s3]
    s4 = sector == 4
    new_r[s4] = t[s4]; new_g[s4] = p[s4]; new_b[s4] = val[s4]
    s5 = sector == 5
    new_r[s5] = val[s5]; new_g[s5] = p[s5]; new_b[s5] = q[s5]

    # Only apply changes where team_mask is true
    result = arr.copy()
    result[:,:,0][team_mask] = new_r[team_mask]
    result[:,:,1][team_mask] = new_g[team_mask]
    result[:,:,2][team_mask] = new_b[team_mask]

    # Clamp and convert back to uint8
    result = np.clip(result * 255, 0, 255).astype(np.uint8)

    out_img = Image.fromarray(result)
    out_img.save(output_path, "WEBP", quality=90)
    print(f"  Saved: {output_path}")


def main():
    input_dir = "buildpics/scavengers"
    output_dir = "converted-to-scav"

    # Allow single file argument
    if len(sys.argv) > 1:
        targets = sys.argv[1:]
    else:
        targets = None  # convert all

    os.makedirs(output_dir, exist_ok=True)

    # Find files that need conversion: files in scavengers/ that DON'T already
    # have the scav purple tint (i.e. the 32 arm/cor originals)
    files = sorted(f for f in os.listdir(input_dir) if f.endswith('.webp'))

    if targets:
        files = [f for f in files if any(t in f for t in targets)]

    print(f"Converting {len(files)} buildpics to scavenger purple...\n")

    for fname in files:
        base = fname.lower().replace(".webp", "")
        if base in SKIP_UNITS:
            print(f"[{fname}] SKIPPED (manual conversion)")
            continue
        print(f"[{fname}]")
        input_path = os.path.join(input_dir, fname)
        output_path = os.path.join(output_dir, fname)
        convert_to_purple(input_path, output_path)
        print()


if __name__ == "__main__":
    main()
