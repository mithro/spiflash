#!/bin/sh
# Run by .github/workflows/deb.yml's "Install test" step in a clean
# debian:<suite> container, after installing the built python3-spiflash.
set -eu
spiflash --version
spiflash id ef4018
spiflash id ef4018 | grep -q Winbond
spiflash find mx25l12835f | grep -q c22018
spiflash jep106 7f1c | grep -q Eon
python3 - <<'EOF'
import spiflash

(w,) = spiflash.lookup("ef4018")
assert w.size == 16 * 1024 * 1024, w.size
assert len(spiflash.flashes()) > 500, len(spiflash.flashes())
print(len(spiflash.flashes()), "chip ids,", len(spiflash.records()), "records")
EOF
