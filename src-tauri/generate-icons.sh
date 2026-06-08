#!/usr/bin/env bash
# Generate placeholder icons for Tauri desktop build
# Requires: ImageMagick (convert) or Python with Pillow

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ICONS_DIR="$SCRIPT_DIR/icons"
mkdir -p "$ICONS_DIR"

# Generate a simple PrismBI icon using ImageMagick if available
if command -v convert &> /dev/null; then
  echo "Using ImageMagick to generate icons..."
  
  # 32x32 PNG
  convert -size 32x32 xc:"#1677ff" -fill white -draw "circle 16,16 16,8" -font DejaVu-Sans-Bold -pointsize 18 -gravity center -annotate +0+0 "P" "$ICONS_DIR/32x32.png"
  
  # 128x128 PNG
  convert -size 128x128 xc:"#1677ff" -fill white -draw "circle 64,64 64,24" -font DejaVu-Sans-Bold -pointsize 64 -gravity center -annotate +0+0 "P" "$ICONS_DIR/128x128.png"
  
  # 256x256 PNG (@2x)
  convert -size 256x256 xc:"#1677ff" -fill white -draw "circle 128,128 128,48" -font DejaVu-Sans-Bold -pointsize 128 -gravity center -annotate +0+0 "P" "$ICONS_DIR/128x128@2x.png"
  
  # ICO (Windows)
  convert "$ICONS_DIR/32x32.png" "$ICONS_DIR/icon.ico"
  
  # ICNS (macOS) - requires png2icns or icnsutil
  if command -v png2icns &> /dev/null; then
    png2icns "$ICONS_DIR/icon.icns" "$ICONS_DIR/128x128.png" "$ICONS_DIR/128x128@2x.png"
  else
    echo "png2icns not available, creating placeholder ICNS..."
    cp "$ICONS_DIR/128x128.png" "$ICONS_DIR/icon.icns"
  fi
  
  echo "Icons generated in $ICONS_DIR"
else
  echo "ImageMagick not found. Creating minimal placeholder icons with Python..."
  
  python3 -c "
from struct import pack
import os

def create_png(width, height, r, g, b):
    # Minimal valid PNG with solid color
    def make_chunk(chunk_type, data):
        chunk = chunk_type + data
        import zlib
        crc = pack('>I', zlib.crc32(chunk) & 0xffffffff)
        return pack('>I', len(data)) + chunk + crc
    
    header = b'\x89PNG\r\n\x1a\n'
    ihdr = make_chunk(b'IHDR', pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
    raw_data = b''
    for y in range(height):
        raw_data += b'\x00' + bytes([r, g, b]) * width
    compressed = zlib.compress(raw_data)
    idat = make_chunk(b'IDAT', compressed)
    iend = make_chunk(b'IEND', b'')
    return header + ihdr + idat + iend

icons_dir = '$ICONS_DIR'
for name, size in [('32x32.png', 32), ('128x128.png', 128), ('128x128@2x.png', 256)]:
    with open(os.path.join(icons_dir, name), 'wb') as f:
        f.write(create_png(size, size, 22, 119, 255))
with open(os.path.join(icons_dir, 'icon.ico'), 'wb') as f:
    f.write(create_png(32, 32, 22, 119, 255))
with open(os.path.join(icons_dir, 'icon.icns'), 'wb') as f:
    f.write(create_png(128, 128, 22, 119, 255))
print('Placeholder icons created')
"
fi

echo "Done. Icons in: $ICONS_DIR"
ls -la "$ICONS_DIR"