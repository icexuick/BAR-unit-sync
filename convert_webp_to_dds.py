"""
Convert scavenger buildpic WebP files to DDS (ARGB8888 with mipmaps).
Output format matches BAR's unitpics/ requirements:
  - Uncompressed ARGB8888 (32-bit)
  - Full mipmap chain (256x256 → 9 levels, 512x512 → 10 levels)
  - Power-of-2 dimensions
"""
import numpy as np
from PIL import Image
import struct
import os
import sys


# DDS header constants
DDS_MAGIC = 0x20534444  # "DDS "
DDSD_CAPS = 0x1
DDSD_HEIGHT = 0x2
DDSD_WIDTH = 0x4
DDSD_PITCH = 0x8
DDSD_PIXELFORMAT = 0x1000
DDSD_MIPMAPCOUNT = 0x20000
DDSD_LINEARSIZE = 0x80000

DDPF_ALPHAPIXELS = 0x1
DDPF_RGB = 0x40

DDSCAPS_COMPLEX = 0x8
DDSCAPS_MIPMAP = 0x400000
DDSCAPS_TEXTURE = 0x1000


def write_dds_header(f, width, height, mipmap_count):
    """Write DDS file header for ARGB8888 uncompressed format with mipmaps."""
    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PITCH | DDSD_PIXELFORMAT | DDSD_MIPMAPCOUNT
    pitch = width * 4  # 4 bytes per pixel (ARGB8888)
    caps = DDSCAPS_COMPLEX | DDSCAPS_MIPMAP | DDSCAPS_TEXTURE

    # DDS_MAGIC
    f.write(struct.pack('<I', DDS_MAGIC))

    # DDS_HEADER (124 bytes)
    f.write(struct.pack('<I', 124))          # dwSize
    f.write(struct.pack('<I', flags))        # dwFlags
    f.write(struct.pack('<I', height))       # dwHeight
    f.write(struct.pack('<I', width))        # dwWidth
    f.write(struct.pack('<I', pitch))        # dwPitchOrLinearSize
    f.write(struct.pack('<I', 0))            # dwDepth
    f.write(struct.pack('<I', mipmap_count)) # dwMipMapCount
    f.write(b'\x00' * 44)                   # dwReserved1[11]

    # DDS_PIXELFORMAT (32 bytes)
    f.write(struct.pack('<I', 32))           # dwSize
    f.write(struct.pack('<I', DDPF_RGB | DDPF_ALPHAPIXELS))  # dwFlags
    f.write(struct.pack('<I', 0))            # dwFourCC (not used for uncompressed)
    f.write(struct.pack('<I', 32))           # dwRGBBitCount
    f.write(struct.pack('<I', 0x00FF0000))   # dwRBitMask
    f.write(struct.pack('<I', 0x0000FF00))   # dwGBitMask
    f.write(struct.pack('<I', 0x000000FF))   # dwBBitMask
    f.write(struct.pack('<I', 0xFF000000))   # dwABitMask

    # Back to DDS_HEADER
    f.write(struct.pack('<I', caps))         # dwCaps
    f.write(struct.pack('<I', 0))            # dwCaps2
    f.write(struct.pack('<I', 0))            # dwCaps3
    f.write(struct.pack('<I', 0))            # dwCaps4
    f.write(struct.pack('<I', 0))            # dwReserved2


def generate_mipmaps(img):
    """Generate full mipmap chain from PIL Image. Returns list of RGBA numpy arrays."""
    mipmaps = []
    current = img.copy()

    while True:
        arr = np.array(current)
        mipmaps.append(arr)
        w, h = current.size
        if w == 1 and h == 1:
            break
        new_w = max(1, w // 2)
        new_h = max(1, h // 2)
        current = current.resize((new_w, new_h), Image.LANCZOS)

    return mipmaps


def rgba_to_bgra(rgba_array):
    """Convert RGBA pixel array to BGRA byte order for DDS."""
    # Input: (H, W, 4) with R, G, B, A channels
    # Output: flat bytes in B, G, R, A order
    r = rgba_array[:, :, 0]
    g = rgba_array[:, :, 1]
    b = rgba_array[:, :, 2]
    a = rgba_array[:, :, 3]

    # Stack as BGRA
    bgra = np.stack([b, g, r, a], axis=-1)
    return bgra.tobytes()


def convert_webp_to_dds(input_path, output_path, target_size=None):
    """Convert a WebP image to DDS with ARGB8888 format and mipmaps."""
    img = Image.open(input_path).convert("RGBA")

    # Determine target size
    w, h = img.size
    if target_size:
        if img.size != (target_size, target_size):
            img = img.resize((target_size, target_size), Image.LANCZOS)
    else:
        # Use nearest power of 2
        size = max(w, h)
        if size <= 256:
            target = 256
        else:
            target = 512
        if img.size != (target, target):
            img = img.resize((target, target), Image.LANCZOS)

    width, height = img.size

    # Generate mipmaps
    mipmaps = generate_mipmaps(img)
    mipmap_count = len(mipmaps)

    # Write DDS file
    with open(output_path, 'wb') as f:
        write_dds_header(f, width, height, mipmap_count)
        for mip in mipmaps:
            f.write(rgba_to_bgra(mip))

    file_size = os.path.getsize(output_path)
    print(f"  {os.path.basename(input_path)} -> {os.path.basename(output_path)} ({width}x{height}, {mipmap_count} mipmaps, {file_size:,} bytes)")


def main():
    input_dir = "buildpics/scavengers"
    output_dir = "buildpics-scavs-dds"

    os.makedirs(output_dir, exist_ok=True)

    # Filter by argument or convert all
    if len(sys.argv) > 1:
        targets = sys.argv[1:]
    else:
        targets = None

    files = sorted(f for f in os.listdir(input_dir) if f.endswith('.webp'))
    if targets:
        files = [f for f in files if any(t in f for t in targets)]

    print(f"Converting {len(files)} buildpics to DDS (ARGB8888 + mipmaps)...\n")

    for fname in files:
        input_path = os.path.join(input_dir, fname)
        unit_name = fname.replace('.webp', '')
        dds_name = f"{unit_name.lower()}.dds"
        output_path = os.path.join(output_dir, dds_name)
        convert_webp_to_dds(input_path, output_path)

    print(f"\nDone! {len(files)} DDS files saved to {output_dir}/")


if __name__ == "__main__":
    main()
