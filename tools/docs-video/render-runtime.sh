#!/bin/sh
# The shared libraries and fonts chrome-headless-shell needs, for a machine whose image has none and gives no root:
# pinned Ubuntu 24.04 packages, checked by sha256 and unpacked under $DOCS_VIDEO_RUNTIME (default /tmp/docs-video-runtime),
# and Playwright's chrome-headless-shell next to them. Usage, before stills.mjs or render.mjs: . ./render-runtime.sh
# (it exports LD_LIBRARY_PATH, FONTCONFIG_FILE and PLAYWRIGHT_BROWSERS_PATH; a second run reuses what the first unpacked).
RT=${DOCS_VIDEO_RUNTIME:-/tmp/docs-video-runtime}/browser-runtime-1
if [ ! -f "$RT/fonts.conf" ]; then
  rm -rf "$RT.partial"; mkdir -p "$RT.partial/debs" "$RT.partial/root"
  while read -r path sha; do
    [ -n "$path" ] || continue
    file="$RT.partial/debs/$(basename "$path")"
    node -e 'const fs=require("fs");fetch(process.argv[1]).then(r=>{if(!r.ok)throw new Error(r.status+" "+process.argv[1]);return r.arrayBuffer()}).then(b=>fs.writeFileSync(process.argv[2],Buffer.from(b)))' "http://archive.ubuntu.com/ubuntu/$path" "$file" || exit 7
    echo "$sha  $file" | sha256sum -c --quiet - || exit 8
    dpkg-deb -x "$file" "$RT.partial/root" || exit 9
  done <<'PINS'
pool/main/a/alsa-lib/libasound2t64_1.2.11-1build2_amd64.deb c2f0caa30869876791ba349bef4907d5cfec47b8cfd1ae9889a05dce0feb7c39
pool/main/a/at-spi2-core/libatk-bridge2.0-0t64_2.52.0-1build1_amd64.deb 22b7d47e3c0f7953a78d3cfd309d1b20771085de9220fbe610c0989b9e808c9b
pool/main/a/at-spi2-core/libatk1.0-0t64_2.52.0-1build1_amd64.deb 42c5d4b00954f17c2c3c4b866844f691eb8b6d57bf08b59203e65f12dc84a4f9
pool/main/a/at-spi2-core/libatspi2.0-0t64_2.52.0-1build1_amd64.deb d686ace4080eca9c2d6ce8de69392a5ac56c846701894c0858922d4ce341a1d7
pool/main/d/dbus/libdbus-1-3_1.14.10-4ubuntu4_amd64.deb bbb55111182a44d0fe97a84120407e17bb6e23d41e2670d73aa78f619e29efa2
pool/main/f/fonts-dejavu/fonts-dejavu-core_2.37-8_all.deb 40049660c194f3b8a2541fc7369efebb10e9f94bdac836a2f38fafedd10fa73a
pool/main/f/fonts-dejavu/fonts-dejavu-mono_2.37-8_all.deb 8a599d6553307db7ecb795d2f0e5a301e03234afc75c7358b0ba43466454c89a
pool/main/f/fonts-liberation/fonts-liberation_2.1.5-3_all.deb 065c2ab1abc9108b17d401016dc594b79750904390f095845c93bb06e1153acc
pool/main/g/glib2.0/libglib2.0-0t64_2.80.0-6ubuntu1_amd64.deb 629359e915f4466cf9cb0fa6c38270d9af1e03e28fab557ce31977d5ab34f13d
pool/main/libd/libdrm/libdrm2_2.4.120-2build1_amd64.deb f5fb4e7ce17921cc466fb7911abf91495ffb181b36772f68e2e82cb621703112
pool/main/libx/libx11/libx11-6_1.8.7-1build1_amd64.deb 397f84347476a3c5786b39f3ff6f0f82866eb3d8be6d2ad3efeadf019efe5b80
pool/main/libx/libxau/libxau6_1.0.9-1build6_amd64.deb e40d29f1d1a62393bacaedebe0da3d9006084152a9f7e5e029293f08ce1c5c80
pool/main/libx/libxcb/libxcb-randr0_1.15-1ubuntu2_amd64.deb b5cb519823a05a617b543dc9b6a9289b7648b20048244abe021caa706309835a
pool/main/libx/libxcb/libxcb1_1.15-1ubuntu2_amd64.deb e1c6611d11ad7398326f1bf028afc34c3b14c51d917a3426b966ed4b9687fa58
pool/main/libx/libxcomposite/libxcomposite1_0.4.5-1build3_amd64.deb a12d5d9ae70b798cbbf284080eae32b87367561fc9c24492f641b8860ac0f308
pool/main/libx/libxdamage/libxdamage1_1.1.6-1build1_amd64.deb 6891faf325e996ebd28ef53ebb9c043dc96f67327500490affc3e89d366d35ed
pool/main/libx/libxdmcp/libxdmcp6_1.1.3-0ubuntu6_amd64.deb bcd336fce11ce2a45f34d0f95e6980af22529f22147e8f98c156e5cee8ee42bb
pool/main/libx/libxext/libxext6_1.3.4-1build2_amd64.deb 45783969a9ece9d7b7b733b8c60981584c53c6bc5ee3b42d295d2f80d1285679
pool/main/libx/libxfixes/libxfixes3_6.0.0-2build1_amd64.deb 0ee1015cccd063249e01c0cd0bf45f513c8ac9a1e5e485070c12e69192455e4a
pool/main/libx/libxi/libxi6_1.8.1-1build1_amd64.deb 0ea7acb5e8a8ce4d6653b30e03a319bfe136db7bfb5ee7cad34c2aa1272ea8d9
pool/main/libx/libxkbcommon/libxkbcommon0_1.6.0-1build1_amd64.deb 2b9caeb423efb540296a1cb20b872cc630c23908407ecb5c1c787a617622d664
pool/main/libx/libxrandr/libxrandr2_1.5.2-2build1_amd64.deb f2955a5e594f5724b58ad241d9231ea191cb36574a0d5e5ca6b661cd41d6256d
pool/main/libx/libxrender/libxrender1_0.9.10-1.1build1_amd64.deb d70bd831aebe8d4834b5dd2ed98df26dd6bd27f1042c47543bd7f66df1ae22ea
pool/main/m/mesa/libgbm1_24.0.5-1ubuntu1_amd64.deb bffad21d2383429d6dbec8395ba8d8476a0ed69e7e0b1c3ef2f27da99211b200
pool/main/n/nspr/libnspr4_4.35-1.1build1_amd64.deb e579e72d091f6c7a13f5a756c31065b15aae5b81840d61b069355aa2283c07b4
pool/main/n/nss/libnss3_3.98-1build1_amd64.deb 88247fe0db5cd4c273b7dd026d9ded4ff9ba828b62437d12a2f1c2abc29468d2
pool/main/s/sqlite3/libsqlite3-0_3.45.1-1ubuntu2_amd64.deb 6ea94c8a822551187cef0085073910eeae0a7525cfec4415171942c7019c68af
pool/main/w/wayland/libwayland-server0_1.22.0-2.1build1_amd64.deb edbfa4b6857691ae922cc768a753fab230eee9e956aa2ce4f2eaa8d9ad777dca
PINS
  printf '<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n<fontconfig><dir>%s/root/usr/share/fonts</dir><cachedir>%s/fontcache</cachedir></fontconfig>\n' "$RT" "$RT" > "$RT.partial/fonts.conf"
  rm -rf "$RT.partial/debs"; mv "$RT.partial" "$RT"
fi
export LD_LIBRARY_PATH="$RT/root/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export FONTCONFIG_FILE="$RT/fonts.conf"
export PLAYWRIGHT_BROWSERS_PATH="${DOCS_VIDEO_RUNTIME:-/tmp/docs-video-runtime}/pw"
ls -d "$PLAYWRIGHT_BROWSERS_PATH"/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell >/dev/null 2>&1 || npx -y playwright@1.64.1 install chromium-headless-shell >/dev/null 2>&1
