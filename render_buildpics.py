"""
Render 3D buildpics for BAR units using the online model viewer.

Uses headless Chrome + Selenium to load each unit's page on beyondallreason.info,
waits for the 3D model to load, renders a 1024x1024 screenshot via the Three.js
renderer, then downscales to 400x400 WebP and uploads to Webflow.

Usage:
    python render_buildpics.py --unit legcom          # Single unit
    python render_buildpics.py --faction leg           # All LEG units
    python render_buildpics.py --all                   # All units
    python render_buildpics.py --faction leg --dry-run  # Preview without uploading
    python render_buildpics.py --faction leg --publish  # Upload + publish in Webflow
"""
import argparse
import base64
import glob
import io
import os
import sys
import time

import requests
from dotenv import load_dotenv
from PIL import Image
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────────────
SITE_URL = "https://www.beyondallreason.info/unit"
RENDER_SIZE = 2048           # Render resolution (square) — higher = sharper downscale
OUTPUT_SIZE = 400            # Final output size (square)
WEBP_QUALITY = 75            # WebP compression quality
OUTPUT_DIR = "buildpic-render"  # Local output directory
MODEL_LOAD_TIMEOUT = 20      # Max seconds to wait for model
MODEL_SETTLE_TIME = 2        # Extra seconds after load for rendering to settle
CAMERA_ANGLE_DEG = 40        # Default camera orbit angle (vehicles, defenses, factories, ships, buildings)
CAMERA_ELEVATION_DEG = 5     # Extra camera elevation (degrees above default)
FACTORY_ELEVATION_DEG = 10   # Extra camera elevation for factories
CAMERA_ZOOM_OUT = 1.35       # Zoom out factor (>1 = further away) to ensure unit fits in frame
BRIGHTNESS_BOOST = 1.00      # Brightness multiplier (1.0 = no change)
CONTRAST_BOOST = 1.16        # Contrast multiplier (1.0 = no change)
SATURATION_BOOST = 1.05      # Color saturation multiplier (1.0 = no change)

# Per-unit-type camera angle overrides
# Webflow unit type reference IDs
UNIT_TYPE_IDS = {
    "bot":       "6564c6553676389f8ba45b2e",
    "vehicle":   "6564c6553676389f8ba45b03",
    "ship":      "6564c6553676389f8ba45b19",
    "factory":   "6564c6553676389f8ba45fa8",
    "aircraft":  "6564c6553676389f8ba45aee",
    "hovercraft":"6564c6553676389f8ba45ad4",
    "building":  "6564c6553676389f8ba45fa6",
    "defense":   "6564c6553676389f8ba45fa7",
}
# Unit types that get a less-rotated angle (more frontal view)
BOT_ANGLE_TYPES = {"bot"}
FRONTAL_ANGLE_TYPES = {"factory"}
BOT_ANGLE_DEG = 30           # Angle for bots
FACTORY_ANGLE_DEG = 40       # Angle for factories
VEHICLE_ANGLE_DEG = 40       # Angle for vehicles/tanks

# Commanders: rendered with walking animation and custom angle
COMMANDER_UNITS = {"armcom", "armdecom", "corcom", "cordecom", "legcom", "legdecom"}

# Units to force cloak-split even if model doesn't have can_cloak flag
FORCE_CLOAK_UNITS = {"armpb"}

# Units that should NOT deploy (show in idle/closed state)
# Constructors and anti-nukes
NO_DEPLOY_PATTERNS = {"consul", "amd"}  # unit names containing these
NO_DEPLOY_SUFFIXES = {"ack", "aca", "acv"}  # constructor suffixes (armack, corack, legack, etc.)

WEBFLOW_API_TOKEN = os.getenv("WEBFLOW_API_TOKEN", "")
WEBFLOW_SITE_ID = "5c68622246b367adf6f3041d"
WEBFLOW_COLLECTION_ID = "6564c6553676389f8ba45a9e"  # Units collection
WEBFLOW_FIELD_SLUG = "buildpic-2"  # Webflow field slug for 3D render image (displayName: "BuildPic")

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO_OWNER = os.getenv("ICON_REPO_OWNER", "icexuick")
GITHUB_REPO_NAME = os.getenv("ICON_REPO_NAME", "bar-unit-sync")
GITHUB_BRANCH = os.getenv("ICON_BRANCH", "main")

# Faction IDs in Webflow (used to filter out scavenger units)
SCAV_FACTION_ID = "6564c6553676389f8ba461dc"


# ── Cloak JS ───────────────────────────────────────────────────────────────
# Check if the loaded unit can cloak (reads userData from the 3D model)
CLOAK_DETECT_JS = """
const ctx = window._editorCtx;
if (!ctx) return false;
let canCloak = false;
ctx.scene.traverse(n => {
    if (n.userData && n.userData.can_cloak) canCloak = true;
});
return canCloak;
"""

# Re-render with a different cloak value, without moving the camera.
# Used for the second pass of a cloak split (camera is already positioned).
CLOAK_RERENDER_JS = """
const ctx = window._editorCtx;
if (!ctx) return 'ERROR:no ctx';
const { renderer, composer, camera, ssaoPass, smaaPass } = ctx;
const scene = ctx.scene;
const w = arguments[0], h = arguments[1];
const cloakVal = arguments[2];

// Force cloak uniform on all materials
scene.traverse(n => {
    if (!n.isMesh || !n.material) return;
    const mats = Array.isArray(n.material) ? n.material : [n.material];
    for (const m of mats) {
        if (m.userData && m.userData.shader && m.userData.shader.uniforms.cloakAmount) {
            m.userData.shader.uniforms.cloakAmount.value = cloakVal;
        }
    }
});

// Render at high resolution (camera is already positioned)
const origPR = renderer.getPixelRatio();
const container = document.getElementById('model-container');
const origW = container.offsetWidth, origH = container.offsetHeight;

renderer.setPixelRatio(1);
renderer.setSize(w, h, false);
composer.setPixelRatio(1);
composer.setSize(w, h);
camera.aspect = 1;
camera.updateProjectionMatrix();
if (ssaoPass) ssaoPass.setSize(w, h);
if (smaaPass) smaaPass.setSize(w, h);
renderer.setClearColor(0x000000, 0);
composer.render();

const data = renderer.domElement.toDataURL('image/png');

// Restore original size
renderer.setPixelRatio(origPR);
renderer.setSize(origW, origH, false);
composer.setPixelRatio(origPR);
composer.setSize(origW, origH);
camera.aspect = origW / origH;
camera.updateProjectionMatrix();
if (ssaoPass) ssaoPass.setSize(origW, origH);
if (smaaPass) smaaPass.setSize(origW * origPR, origH * origPR);

return data;
"""



# ── Render JS ───────────────────────────────────────────────────────────────
RENDER_JS_PROBE = """
// Probe UI toggles and viewer setup
const toggles = [];
document.querySelectorAll('input[type=checkbox], button').forEach(el => {
    toggles.push({id: el.id, type: el.type, text: el.textContent?.trim().substring(0, 50), checked: el.checked});
});
const ctx = window._editorCtx;
const info = { toggles };
if (ctx) {
    // Check for emissive/glow materials
    const emissives = [];
    ctx.scene.traverse(n => {
        if (n.isMesh && n.material) {
            const mats = Array.isArray(n.material) ? n.material : [n.material];
            for (const m of mats) {
                if (m.emissive && (m.emissive.r > 0 || m.emissive.g > 0 || m.emissive.b > 0)) {
                    emissives.push({name: n.name, color: [m.emissive.r, m.emissive.g, m.emissive.b], intensity: m.emissiveIntensity});
                }
            }
        }
    });
    info.emissives = emissives.slice(0, 10);
    // Check for point/spot lights inside the model (glow lights)
    const modelLights = [];
    ctx.scene.traverse(n => {
        if ((n.isPointLight || n.isSpotLight) && n.parent && n.parent.type !== 'Scene') {
            modelLights.push({name: n.name, type: n.type, color: [n.color.r, n.color.g, n.color.b], intensity: n.intensity, parent: n.parent.name});
        }
    });
    info.modelLights = modelLights.slice(0, 10);
}
return JSON.stringify(info);
"""

RENDER_JS = """
const canvas = document.querySelector('#model-container canvas');
if (!canvas) return 'ERROR:no canvas';

const ctx = window._editorCtx;
if (!ctx) return 'ERROR:no _editorCtx';

const { renderer, composer, camera, ssaoPass, smaaPass, controls } = ctx;
const scene = ctx.scene;
const w = arguments[0], h = arguments[1];
const angleDeg = arguments[2];
const zoomOut = arguments[3];
const elevDeg = arguments[4];
const recenterBase = arguments[5];
const cloakOverride = (arguments.length > 6 && arguments[6] !== null) ? arguments[6] : null;

// Helper: force cloak uniform on all materials right before render
function applyCloak() {
    if (cloakOverride === null) return;
    scene.traverse(n => {
        if (!n.isMesh || !n.material) return;
        const mats = Array.isArray(n.material) ? n.material : [n.material];
        for (const m of mats) {
            if (m.userData && m.userData.shader && m.userData.shader.uniforms.cloakAmount) {
                m.userData.shader.uniforms.cloakAmount.value = cloakOverride;
            }
        }
    });
}

// ── 0. Recenter on base (exclude aim pieces for XZ, use all for Y) ──
if (recenterBase) {
    // Collect world-space bounding boxes for base pieces and all pieces
    let baseMinX = Infinity, baseMaxX = -Infinity;
    let baseMinZ = Infinity, baseMaxZ = -Infinity;
    let fullMinX = Infinity, fullMaxX = -Infinity;
    let fullMinY = Infinity, fullMaxY = -Infinity;
    let fullMinZ = Infinity, fullMaxZ = -Infinity;
    let hasBase = false;
    scene.traverse(n => {
        if (!n.isMesh) return;
        n.updateWorldMatrix(true, false);
        const geom = n.geometry;
        if (!geom) return;
        if (!geom.boundingBox) geom.computeBoundingBox();
        if (!geom.boundingBox) return;
        // Get 8 corners of bounding box in world space
        const bb = geom.boundingBox;
        for (let ix = 0; ix <= 1; ix++) {
            for (let iy = 0; iy <= 1; iy++) {
                for (let iz = 0; iz <= 1; iz++) {
                    const v = new camera.position.constructor(
                        ix ? bb.max.x : bb.min.x,
                        iy ? bb.max.y : bb.min.y,
                        iz ? bb.max.z : bb.min.z
                    );
                    v.applyMatrix4(n.matrixWorld);
                    fullMinX = Math.min(fullMinX, v.x);
                    fullMaxX = Math.max(fullMaxX, v.x);
                    fullMinY = Math.min(fullMinY, v.y);
                    fullMaxY = Math.max(fullMaxY, v.y);
                    fullMinZ = Math.min(fullMinZ, v.z);
                    fullMaxZ = Math.max(fullMaxZ, v.z);
                    // Exclude aim/turret/barrel pieces from base XZ
                    const nm = (n.name || '').toLowerCase();
                    const isAim = nm.includes('aim') || nm.includes('turret') || nm.includes('barrel') || nm.includes('sleeve') || nm.includes('flare');
                    if (!isAim) {
                        baseMinX = Math.min(baseMinX, v.x);
                        baseMaxX = Math.max(baseMaxX, v.x);
                        baseMinZ = Math.min(baseMinZ, v.z);
                        baseMaxZ = Math.max(baseMaxZ, v.z);
                        hasBase = true;
                    }
                }
            }
        }
    });
    if (hasBase && fullMinY !== Infinity) {
        const baseCX = (baseMinX + baseMaxX) / 2;
        const baseCZ = (baseMinZ + baseMaxZ) / 2;
        const fullCY = (fullMinY + fullMaxY) / 2;
        const newTarget = new camera.position.constructor(baseCX, fullCY, baseCZ);
        const shift = newTarget.clone().sub(controls.target);
        controls.target.copy(newTarget);
        camera.position.add(shift);
    }
}

// ── 1. Orbit camera to desired angle ──
const pos = camera.position;
const dist = Math.sqrt(pos.x * pos.x + pos.z * pos.z) * zoomOut;
const heightFactor = pos.y / Math.sqrt(pos.x * pos.x + pos.z * pos.z);
const angleRad = angleDeg * Math.PI / 180;
camera.position.set(
    dist * Math.cos(angleRad),
    dist * heightFactor,
    dist * Math.sin(angleRad)
);

// ── 2. Elevate camera ──
if (elevDeg !== 0) {
    const elevRad = elevDeg * Math.PI / 180;
    const offset = camera.position.clone().sub(controls.target);
    const d = offset.length();
    const currentElev = Math.asin(offset.y / d);
    const newElev = Math.min(Math.PI / 2 - 0.01, currentElev + elevRad);
    const horizDist = d * Math.cos(newElev);
    const hAngle = Math.atan2(offset.z, offset.x);
    camera.position.set(
        controls.target.x + horizDist * Math.cos(hAngle),
        controls.target.y + d * Math.sin(newElev),
        controls.target.z + horizDist * Math.sin(hAngle)
    );
}

// ── 3. Rotate lights + environment to match camera orbit ──
// The initial camera is at 45° (atan2(48, 48) = PI/4).
// We rotated to angleDeg. Compute the delta so lights follow.
// lightLag: lights rotate LESS than camera, so light comes more from the right
const lightLagDeg = 0;  // no extra light offset (viewer ?buildpic handles lighting)
const initialAngle = Math.PI / 4;  // 45° — initial camera horizontal angle
const deltaAngle = angleRad - initialAngle - (lightLagDeg * Math.PI / 180);

// Rotate directional lights around Y-axis by deltaAngle
scene.traverse(n => {
    if (n.isDirectionalLight || n.isPointLight || n.isSpotLight) {
        const x = n.position.x;
        const z = n.position.z;
        n.position.x = x * Math.cos(deltaAngle) - z * Math.sin(deltaAngle);
        n.position.z = x * Math.sin(deltaAngle) + z * Math.cos(deltaAngle);
        if (n.target) {
            const tx = n.target.position.x;
            const tz = n.target.position.z;
            n.target.position.x = tx * Math.cos(deltaAngle) - tz * Math.sin(deltaAngle);
            n.target.position.z = tx * Math.sin(deltaAngle) + tz * Math.cos(deltaAngle);
            n.target.updateMatrixWorld();
        }
    }
});

// Rotate environment map (HDR reflections)
if (scene.environmentRotation) {
    scene.environmentRotation.y = deltaAngle;
    scene.environment.needsUpdate = true;
}

controls.update();
applyCloak();
composer.render();

// ── 4. Render at high resolution ──
const origPR = renderer.getPixelRatio();
const container = document.getElementById('model-container');
const origW = container.offsetWidth, origH = container.offsetHeight;

renderer.setPixelRatio(1);
renderer.setSize(w, h, false);
composer.setPixelRatio(1);
composer.setSize(w, h);
camera.aspect = 1;
camera.updateProjectionMatrix();
if (ssaoPass) ssaoPass.setSize(w, h);
if (smaaPass) smaaPass.setSize(w, h);
renderer.setClearColor(0x000000, 0);
applyCloak();
composer.render();

const data = renderer.domElement.toDataURL('image/png');

// ── 5. Restore original size ──
renderer.setPixelRatio(origPR);
renderer.setSize(origW, origH, false);
composer.setPixelRatio(origPR);
composer.setSize(origW, origH);
camera.aspect = origW / origH;
camera.updateProjectionMatrix();
if (ssaoPass) ssaoPass.setSize(origW, origH);
if (smaaPass) smaaPass.setSize(origW * origPR, origH * origPR);

return data;
"""


# ── Webflow helpers ─────────────────────────────────────────────────────────
class WebflowAPI:
    """Minimal Webflow v2 API client for updating unit items."""

    def __init__(self, token: str):
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "accept": "application/json",
            "content-type": "application/json",
        }
        self.base = "https://api.webflow.com/v2"
        self._units_cache = None

    def _request(self, method: str, url: str, **kwargs):
        time.sleep(1.5)  # Rate limit (50 req/min)
        resp = requests.request(method, url, headers=self.headers, **kwargs)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 10))
            print(f"  Rate limited, waiting {wait}s...")
            time.sleep(wait)
            resp = requests.request(method, url, headers=self.headers, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def get_all_units(self):
        """Fetch all units from Webflow (cached)."""
        if self._units_cache is not None:
            return self._units_cache
        items = []
        offset = 0
        while True:
            data = self._request(
                "GET",
                f"{self.base}/collections/{WEBFLOW_COLLECTION_ID}/items",
                params={"limit": 100, "offset": offset},
            )
            items.extend(data.get("items", []))
            if offset + 100 >= data.get("pagination", {}).get("total", 0):
                break
            offset += 100
        self._units_cache = items
        return items

    def update_item(self, item_id: str, field_data: dict):
        return self._request(
            "PATCH",
            f"{self.base}/collections/{WEBFLOW_COLLECTION_ID}/items/{item_id}",
            json={"fieldData": field_data},
        )

    def publish_items(self, item_ids: list):
        for i in range(0, len(item_ids), 100):
            batch = item_ids[i : i + 100]
            self._request(
                "POST",
                f"{self.base}/collections/{WEBFLOW_COLLECTION_ID}/items/publish",
                json={"itemIds": batch},
            )


# ── GitHub upload ───────────────────────────────────────────────────────────
def upload_to_github(webp_data: bytes, filename: str) -> str | None:
    """Upload a WebP file to the GitHub repo under renders/. Returns raw URL."""
    repo_path = f"buildpic-render/{filename}"
    api_url = (
        f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}"
        f"/contents/{repo_path}"
    )
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    # Check if file exists (get SHA for update)
    sha = None
    resp = requests.get(api_url, headers=headers, params={"ref": GITHUB_BRANCH})
    if resp.status_code == 200:
        existing = resp.json()
        sha = existing.get("sha")
        if existing.get("size") == len(webp_data):
            raw_url = (
                f"https://raw.githubusercontent.com/{GITHUB_REPO_OWNER}"
                f"/{GITHUB_REPO_NAME}/{GITHUB_BRANCH}/{repo_path}"
            )
            print(f"     Already up-to-date ({len(webp_data)} bytes)")
            return raw_url

    payload = {
        "message": f"Add/update 3D render: {filename}",
        "content": base64.b64encode(webp_data).decode(),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    resp = requests.put(api_url, headers=headers, json=payload)
    resp.raise_for_status()

    raw_url = (
        f"https://raw.githubusercontent.com/{GITHUB_REPO_OWNER}"
        f"/{GITHUB_REPO_NAME}/{GITHUB_BRANCH}/{repo_path}"
    )
    print(f"     Uploaded to GitHub ({len(webp_data)} bytes)")
    return raw_url


# ── Renderer ────────────────────────────────────────────────────────────────
class UnitRenderer:
    """Headless Chrome renderer for BAR unit 3D models."""

    def __init__(self):
        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--window-size=800,800")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        self.driver = webdriver.Chrome(options=opts)
        self.angle_override = None
        self.elevation_override = None
        self.zoom_override = None
        self.recenter_base = False
        self.no_shadows = False
        self.keep_walking = False
        self.skip_deploy = False

    def _load_unit_page(self, unit_name: str) -> bool:
        """Load unit page and wait for 3D model to initialize. Returns True on success."""
        url = f"{SITE_URL}/{unit_name}?buildpic"
        self.driver.get(url)

        try:
            WebDriverWait(self.driver, MODEL_LOAD_TIMEOUT).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "#model-container canvas")
                )
            )
        except Exception:
            print(f"  No 3D model found (timeout)")
            return False

        # Wait for _editorCtx to be available (viewer fully initialized)
        for _ in range(40):  # Max 20 seconds (40 x 0.5s)
            has_ctx = self.driver.execute_script("return !!window._editorCtx")
            if has_ctx:
                break
            time.sleep(0.5)
        else:
            print(f"  Viewer not initialized (_editorCtx missing)")
            return False

        # Extra settle time for model to finish loading
        time.sleep(MODEL_SETTLE_TIME)
        return True

    def _setup_animations(self):
        """Handle walk/deploy/shadow toggles after page load."""
        # Disable walk animation unless --walking flag is set
        if not self.keep_walking:
            toggled = self.driver.execute_script("""
                const cb = document.getElementById('movementToggle');
                if (cb && cb.checked) {
                    cb.click();
                    return true;
                }
                return false;
            """)
            if toggled:
                time.sleep(1)  # Let pose reset to static

        # Activate open/deploy animation if available (e.g. defenses that unfold)
        # Skip deploy for constructors and anti-nukes (show them in idle state)
        if self.skip_deploy:
            deployed = False
        else:
            deployed = self.driver.execute_script("""
            const btn = document.getElementById('anim-toggle-button');
            if (btn && btn.offsetParent !== null) {
                btn.click();
                return true;
            }
            return false;
        """)
        if deployed:
            # Wait for deploy animation to fully complete
            for _ in range(20):  # Max 10 seconds (20 x 0.5s)
                time.sleep(0.5)
                still_playing = self.driver.execute_script("""
                    if (window._editorCtx && window._editorCtx.scene) {
                        let playing = false;
                        window._editorCtx.scene.traverse(n => {
                            if (n.userData && n.userData._mixer) {
                                const actions = n.userData._mixer._actions;
                                for (const a of actions) {
                                    if (a.isRunning()) playing = true;
                                }
                            }
                        });
                        return playing;
                    }
                    return false;
                """)
                if not still_playing:
                    break
            time.sleep(0.5)  # Small extra settle time

        # Disable shadows if requested
        if self.no_shadows:
            self.driver.execute_script("""
                const btn = document.getElementById('shadow-toggle-button');
                if (btn) { btn.click(); }
            """)
            time.sleep(0.3)

    def _capture_render(self, cloak_override: float | None = None) -> bytes | None:
        """Execute the render JS and return raw PNG bytes.
        cloak_override: if set, forces cloakAmount uniform to this value right before render
        (bypasses the animation loop that would otherwise overwrite it).
        """
        angle = self.angle_override if self.angle_override is not None else CAMERA_ANGLE_DEG
        elev = self.elevation_override if self.elevation_override is not None else CAMERA_ELEVATION_DEG
        zoom = self.zoom_override if self.zoom_override is not None else CAMERA_ZOOM_OUT
        result = self.driver.execute_script(
            RENDER_JS, RENDER_SIZE, RENDER_SIZE, angle, zoom, elev,
            self.recenter_base, cloak_override,
        )

        if not result or not result.startswith("data:"):
            print(f"  Render failed: {result}")
            return None

        # Decode base64 PNG
        png_data = base64.b64decode(result.split(",")[1])
        return png_data

    def _capture_cloak_rerender(self, cloak_value: float) -> bytes | None:
        """Re-render with a different cloak value without moving the camera."""
        result = self.driver.execute_script(
            CLOAK_RERENDER_JS, RENDER_SIZE, RENDER_SIZE, cloak_value,
        )
        if not result or not result.startswith("data:"):
            print(f"  Cloak re-render failed: {result}")
            return None
        return base64.b64decode(result.split(",")[1])

    def _detect_cloak(self) -> bool:
        """Check if the loaded unit can cloak (reads userData from 3D model)."""
        return self.driver.execute_script(CLOAK_DETECT_JS) or False

    def render_unit(self, unit_name: str) -> bytes | None:
        """
        Load unit page, wait for 3D model, render at RENDER_SIZE and return
        the raw PNG bytes. Returns None on failure.
        """
        if not self._load_unit_page(unit_name):
            return None
        self._setup_animations()
        return self._capture_render()

    def render_unit_cloak_split(self, unit_name: str) -> tuple[bytes | None, bool, bytes | None]:
        """
        Render a unit with cloak split if the unit can cloak.
        Returns (png_data, is_cloak_split, bbox_ref_png).
        If the unit can cloak, png_data is a composite with left=cloaked, right=normal,
        and bbox_ref_png is the normal render (for bounding box detection in png_to_webp).
        If not, png_data is a normal render, is_cloak_split=False, bbox_ref_png=None.
        """
        if not self._load_unit_page(unit_name):
            return None, False, None
        self._setup_animations()

        can_cloak = self._detect_cloak() or unit_name in FORCE_CLOAK_UNITS
        if not can_cloak or unit_name in COMMANDER_UNITS:
            return self._capture_render(), False, None

        print(f"  Unit can cloak — rendering split (left=cloak, right=normal)")

        # Render 1: Normal — full camera setup + cloak forced to 0.0
        # (cloak_override is applied inside RENDER_JS right before composer.render(),
        # so the animation loop cannot overwrite it)
        png_normal = self._capture_render(cloak_override=0.0)
        if not png_normal:
            return None, False, None

        # Render 2: Cloaked — re-render with cloak=1.0, same camera position
        # Uses CLOAK_RERENDER_JS which skips camera orbit/elevation/light rotation
        png_cloak = self._capture_cloak_rerender(1.0)
        if not png_cloak:
            return png_normal, False, None  # Fallback to normal only

        # Return both renders — diagonal blending happens in png_to_webp after crop
        return png_normal, True, png_cloak

    def close(self):
        self.driver.quit()


def png_to_webp(png_data: bytes, padding_pct: float = 0.10, cloak_png: bytes | None = None) -> bytes:
    """Smart-crop PNG to tight square around unit, apply contrast/brightness/sharpen, resize to 400x400 WebP.
    cloak_png: if provided, the cloaked render to blend diagonally with the normal render.
    The diagonal blend is applied AFTER cropping so the split is consistent regardless of unit position.
    """
    import numpy as np
    from PIL import ImageEnhance, ImageFilter

    img = Image.open(io.BytesIO(png_data)).convert("RGBA")

    # Apply color adjustments (on RGB, preserve alpha)
    r, g, b, a = img.split()
    rgb = Image.merge("RGB", (r, g, b))
    if BRIGHTNESS_BOOST != 1.0:
        rgb = ImageEnhance.Brightness(rgb).enhance(BRIGHTNESS_BOOST)
    if CONTRAST_BOOST != 1.0:
        rgb = ImageEnhance.Contrast(rgb).enhance(CONTRAST_BOOST)
    if SATURATION_BOOST != 1.0:
        rgb = ImageEnhance.Color(rgb).enhance(SATURATION_BOOST)
    img = Image.merge("RGBA", (*rgb.split(), a))

    # Bounding box from the normal (fully opaque) render
    alpha = np.array(img)[:, :, 3]

    # Find bounding box of opaque pixels (ignore semi-transparent shadow)
    rows = np.any(alpha > 200, axis=1)
    cols = np.any(alpha > 200, axis=0)

    if not rows.any():
        # Fully transparent — just resize
        img = img.resize((OUTPUT_SIZE, OUTPUT_SIZE), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "WEBP", quality=WEBP_QUALITY)
        return buf.getvalue()

    y_min, y_max = np.where(rows)[0][[0, -1]]
    x_min, x_max = np.where(cols)[0][[0, -1]]

    # Add padding based on unit type
    obj_w = x_max - x_min
    obj_h = y_max - y_min
    pad = int(max(obj_w, obj_h) * padding_pct)

    # Make square crop centered on the object
    cx = (x_min + x_max) // 2
    cy = (y_min + y_max) // 2
    half = max(obj_w, obj_h) // 2 + pad

    # Clamp to image bounds
    h, w = alpha.shape
    crop_x1 = max(0, cx - half)
    crop_y1 = max(0, cy - half)
    crop_x2 = min(w, cx + half)
    crop_y2 = min(h, cy + half)

    # Ensure square: expand the shorter side
    crop_w = crop_x2 - crop_x1
    crop_h = crop_y2 - crop_y1
    if crop_w > crop_h:
        diff = crop_w - crop_h
        crop_y1 = max(0, crop_y1 - diff // 2)
        crop_y2 = min(h, crop_y1 + crop_w)
    elif crop_h > crop_w:
        diff = crop_h - crop_w
        crop_x1 = max(0, crop_x1 - diff // 2)
        crop_x2 = min(w, crop_x1 + crop_h)

    cropped = img.crop((crop_x1, crop_y1, crop_x2, crop_y2))
    cropped = cropped.resize((OUTPUT_SIZE, OUTPUT_SIZE), Image.LANCZOS)

    # If cloak render provided: crop+resize it identically, then diagonal blend
    if cloak_png:
        img_cloak = Image.open(io.BytesIO(cloak_png)).convert("RGBA")
        # Apply same color adjustments
        r2, g2, b2, a2 = img_cloak.split()
        rgb2 = Image.merge("RGB", (r2, g2, b2))
        if BRIGHTNESS_BOOST != 1.0:
            rgb2 = ImageEnhance.Brightness(rgb2).enhance(BRIGHTNESS_BOOST)
        if CONTRAST_BOOST != 1.0:
            rgb2 = ImageEnhance.Contrast(rgb2).enhance(CONTRAST_BOOST)
        if SATURATION_BOOST != 1.0:
            rgb2 = ImageEnhance.Color(rgb2).enhance(SATURATION_BOOST)
        img_cloak = Image.merge("RGBA", (*rgb2.split(), a2))
        # Same crop + resize
        cropped_cloak = img_cloak.crop((crop_x1, crop_y1, crop_x2, crop_y2))
        cropped_cloak = cropped_cloak.resize((OUTPUT_SIZE, OUTPUT_SIZE), Image.LANCZOS)

        # Diagonal blend on the final OUTPUT_SIZE x OUTPUT_SIZE image
        arr_normal = np.array(cropped, dtype=np.float32)
        arr_cloak = np.array(cropped_cloak, dtype=np.float32)
        s = OUTPUT_SIZE
        xs = np.arange(s, dtype=np.float32) / s
        ys = np.arange(s, dtype=np.float32) / s
        xx, yy = np.meshgrid(xs, ys)
        diagonal = (xx + (1.0 - yy)) / 2.0  # 0=top-left, 1=bottom-right
        transition_width = 0.05
        gradient = np.clip((diagonal - 0.5) / transition_width + 0.5, 0.0, 1.0)
        gradient = gradient[:, :, np.newaxis]
        # gradient=0 → cloak (top-left), gradient=1 → normal (bottom-right)
        blended = arr_cloak * (1.0 - gradient) + arr_normal * gradient
        cropped = Image.fromarray(np.clip(blended, 0, 255).astype(np.uint8), "RGBA")

    # Smart sharpen (unsharp mask on RGB, preserve alpha)
    r, g, b, a = cropped.split()
    rgb = Image.merge("RGB", (r, g, b))
    rgb = rgb.filter(ImageFilter.UnsharpMask(radius=1.2, percent=80, threshold=2))
    cropped = Image.merge("RGBA", (*rgb.split(), a))

    buf = io.BytesIO()
    cropped.save(buf, "WEBP", quality=WEBP_QUALITY)
    return buf.getvalue()


# ── Main ────────────────────────────────────────────────────────────────────
def _resolve_unit_type(fd):
    """Resolve unit type string from Webflow fieldData."""
    type_ref = fd.get("unittype")
    if not type_ref:
        return "unknown"
    # Reverse lookup from ID to name
    for name, uid in UNIT_TYPE_IDS.items():
        if uid == type_ref:
            return name
    return "unknown"


def get_unit_names(webflow: WebflowAPI, faction: str = None, unit: str = None):
    """Get list of (name, id, techlevel, unit_type, metalcost) tuples to process."""
    all_units = webflow.get_all_units()

    if unit:
        # Single unit
        for u in all_units:
            fd = u.get("fieldData", {})
            if fd.get("name", "") == unit:
                techlevel = fd.get("techlevel", 1) or 1
                unit_type = _resolve_unit_type(fd)
                metalcost = fd.get("metal-cost", 0) or 0
                return [(fd["name"], u["id"], techlevel, unit_type, metalcost)]
        print(f"Unit '{unit}' not found in Webflow")
        return []

    # Filter by faction prefix and active status, exclude scavenger units
    skipped_scav = 0
    result = []
    for u in all_units:
        fd = u.get("fieldData", {})
        name = fd.get("name", "")
        if not name:
            continue
        if u.get("isDraft") or u.get("isArchived"):
            continue
        if faction and not name.startswith(faction.lower()):
            continue
        if fd.get("faction-ref") == SCAV_FACTION_ID:
            skipped_scav += 1
            continue
        techlevel = fd.get("techlevel", 1) or 1
        unit_type = _resolve_unit_type(fd)
        metalcost = fd.get("metal-cost", 0) or 0
        result.append((name, u["id"], techlevel, unit_type, metalcost))
    if skipped_scav:
        print(f"  Skipped {skipped_scav} Scavenger units")

    result.sort(key=lambda x: x[0])
    return result


def main():
    parser = argparse.ArgumentParser(description="Render 3D buildpics for BAR units")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--unit", help="Single unit name (e.g. legcom)")
    group.add_argument("--faction", help="Faction prefix (e.g. leg, arm, cor)")
    group.add_argument("--all", action="store_true", help="All units")
    parser.add_argument("--dry-run", action="store_true", help="Render only, no upload")
    parser.add_argument("--publish", action="store_true", help="Publish after updating")
    parser.add_argument("--limit", type=int, help="Limit number of units to process")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Skip units that already have a buildpic set")
    parser.add_argument("--min-metal", type=int, default=None,
                        help="Only process units with metalcost >= this value")
    parser.add_argument("--angle", type=int, default=None,
                        help="Override model Y-rotation in degrees (default: from config)")
    parser.add_argument("--elevation", type=float, default=None,
                        help="Override extra camera elevation in degrees (default: from config)")
    parser.add_argument("--padding", type=float, default=None,
                        help="Override padding percentage (e.g. 0.05 for 5%%)")
    parser.add_argument("--walking", action="store_true",
                        help="Keep walk animation on (default: off)")
    parser.add_argument("--no-shadows", action="store_true",
                        help="Disable shadows in render")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Override output directory (default: buildpic-render)")
    parser.add_argument("--site-url", type=str, default=None,
                        help="Override viewer site URL (e.g. https://blue-and-red.webflow.io/unit)")
    args = parser.parse_args()

    if not WEBFLOW_API_TOKEN:
        print("Error: WEBFLOW_API_TOKEN not set in .env")
        sys.exit(1)

    print("=" * 70)
    print("BAR 3D Buildpic Renderer")
    print("=" * 70)

    webflow = WebflowAPI(WEBFLOW_API_TOKEN)

    # Get units to process
    faction = args.faction if args.faction else None
    unit_arg = args.unit if args.unit else None
    if args.all:
        faction = None

    print("Fetching units from Webflow...")
    units = get_unit_names(webflow, faction=faction, unit=unit_arg)

    if args.skip_existing:
        all_units_data = webflow.get_all_units()
        lookup = {u.get("fieldData", {}).get("name", ""): u for u in all_units_data}
        filtered = []
        for entry in units:
            name = entry[0]
            fd = lookup.get(name, {}).get("fieldData", {})
            if not fd.get(WEBFLOW_FIELD_SLUG):
                filtered.append(entry)
            else:
                pass  # Already has buildpic
        skipped = len(units) - len(filtered)
        if skipped:
            print(f"  Skipping {skipped} units with existing buildpic")
        units = filtered

    if args.min_metal:
        units = [u for u in units if u[4] >= args.min_metal]
        print(f"  Filtered to {len(units)} units with metalcost >= {args.min_metal}")

    if args.limit:
        units = units[: args.limit]

    if not units:
        print("No units to process.")
        return

    print(f"Processing {len(units)} units")
    if args.dry_run:
        print("DRY RUN - renders will be saved locally only")
    print()

    output_dir = args.output_dir if args.output_dir else OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)

    global SITE_URL
    if args.site_url:
        SITE_URL = args.site_url
        print(f"Using custom site URL: {SITE_URL}")

    renderer = UnitRenderer()
    if args.angle is not None:
        renderer.angle_override = args.angle
    if args.elevation is not None:
        renderer.elevation_override = args.elevation
    renderer.keep_walking = args.walking
    renderer.no_shadows = args.no_shadows
    success_count = 0
    skip_count = 0
    error_count = 0
    publish_ids = []

    try:
        for idx, (unit_name, unit_id, techlevel, unit_type, metalcost) in enumerate(units, 1):
            is_factory = unit_type == "factory"
            is_commander = unit_name in COMMANDER_UNITS

            # Determine padding
            if args.padding is not None:
                padding = args.padding
            elif metalcost > 6100:
                padding = -0.10  # Negative = crop tighter, unit fills more of the frame
            elif is_commander:
                padding = 0.0
            else:
                is_mine = "mine" in unit_name.lower()
                if is_mine:
                    padding = 0.30
                elif is_factory:
                    padding = 0.05
                elif techlevel >= 3:
                    padding = 0.0
                elif techlevel <= 1:
                    padding = 0.15
                else:
                    padding = 0.10

            # Determine camera angle per unit type (unless overridden by CLI)
            if args.angle is None:
                if is_commander:
                    renderer.angle_override = 20
                elif unit_type == "vehicle":
                    renderer.angle_override = VEHICLE_ANGLE_DEG
                elif unit_type in BOT_ANGLE_TYPES:
                    renderer.angle_override = BOT_ANGLE_DEG
                elif unit_type in FRONTAL_ANGLE_TYPES:
                    renderer.angle_override = FACTORY_ANGLE_DEG
                else:
                    renderer.angle_override = CAMERA_ANGLE_DEG

                # Expensive units: halve the angle (more frontal view)
                if metalcost > 6100:
                    renderer.angle_override = renderer.angle_override // 2

            # Per-type elevation (unless overridden by CLI)
            if args.elevation is None:
                if is_factory:
                    renderer.elevation_override = FACTORY_ELEVATION_DEG
                else:
                    renderer.elevation_override = CAMERA_ELEVATION_DEG

            # Recenter camera on base (exclude aim/turret pieces) for specific large-cannon units
            renderer.recenter_base = unit_name in ("armvulc", "corbuzz")

            # Commanders and T3 bots get walking animation
            if is_commander or (techlevel >= 3 and unit_type == "bot"):
                renderer.keep_walking = True
            elif not args.walking:
                renderer.keep_walking = False

            # Skip deploy for constructors and anti-nukes
            name_suffix = unit_name[3:]  # strip faction prefix (arm/cor/leg)
            renderer.skip_deploy = (
                name_suffix in NO_DEPLOY_SUFFIXES
                or any(p in unit_name for p in NO_DEPLOY_PATTERNS)
            )

            # Zoom override for expensive units (>5000 metal: 20% bigger = zoom closer)
            if metalcost > 6100:
                renderer.zoom_override = CAMERA_ZOOM_OUT * 0.80
            else:
                renderer.zoom_override = None

            print(f"[{idx}/{len(units)}] {unit_name} ({unit_type}, T{techlevel}, angle={renderer.angle_override}°, metal={metalcost})")

            # Render (with cloak split detection)
            png_data, is_cloak_split, png_cloak = renderer.render_unit_cloak_split(unit_name)
            if not png_data:
                print(f"  Skipped (no render)")
                skip_count += 1
                print()
                continue

            # Convert to WebP (pass cloak render for diagonal blend after crop)
            webp_data = png_to_webp(png_data, padding_pct=padding, cloak_png=png_cloak)
            webp_filename = f"{unit_name}.webp"

            # Save locally
            local_path = os.path.join(output_dir, webp_filename)
            with open(local_path, "wb") as f:
                f.write(webp_data)
            print(f"  Saved: {local_path} ({len(webp_data):,} bytes)")

            if args.dry_run:
                success_count += 1
                print()
                continue

            # Upload to GitHub
            try:
                raw_url = upload_to_github(webp_data, webp_filename)
            except Exception as e:
                print(f"  GitHub upload failed: {e}")
                error_count += 1
                print()
                continue

            if not raw_url:
                error_count += 1
                print()
                continue

            # Update Webflow
            try:
                webflow.update_item(unit_id, {WEBFLOW_FIELD_SLUG: {"url": raw_url}})
                print(f"  Updated Webflow: {WEBFLOW_FIELD_SLUG} = {raw_url}")
                publish_ids.append(unit_id)
                success_count += 1
            except Exception as e:
                print(f"  Webflow update failed: {e}")
                error_count += 1

            print()

    finally:
        renderer.close()

    # Publish
    if publish_ids and args.publish:
        print(f"Publishing {len(publish_ids)} units...")
        try:
            webflow.publish_items(publish_ids)
            print(f"  Published!")
        except Exception as e:
            print(f"  Publish failed: {e}")

    # Summary
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total:   {len(units)}")
    print(f"Success: {success_count}")
    print(f"Skipped: {skip_count}")
    print(f"Errors:  {error_count}")
    print("=" * 70)


if __name__ == "__main__":
    main()
