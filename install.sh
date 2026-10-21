#!/usr/bin/env bash
# Install Plasmify for the current user (root is only needed for missing dependencies).
#
#   ./install.sh              install or update
#   ./install.sh --uninstall  remove the app (your themes and backups are kept)
#   ./install.sh --purge      remove the app and its presets, wallpapers, backups and generated themes
#
# Supported: Arch, Fedora, openSUSE and Debian/Ubuntu families running KDE Plasma 6.
set -euo pipefail

PREFIX="${PREFIX:-$HOME/.local}"
APP_DIR="$PREFIX/lib/plasmify"
BIN="$PREFIX/bin/plasmify"
DESKTOP="$PREFIX/share/applications/io.github.heyquatch.plasmify.desktop"
ICON="$PREFIX/share/icons/hicolor/scalable/apps/io.github.heyquatch.plasmify.svg"
# Menu entry and icon of test builds from before the app id had the owner in it.
OLD_FILES=("$PREFIX/share/applications/io.github.plasmify.desktop" "$PREFIX/share/icons/hicolor/scalable/apps/io.github.plasmify.svg")
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

info() { printf '\033[1;35m::\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# The install path ends up in a shell launcher, a desktop entry and PYTHONPATH: refuse the few
# characters that would break or change them there.
check_prefix() {
    if [[ "$PREFIX" == *[\"\`\$\\%:\|\&]* || "$PREFIX" == *$'\n'* ]]; then
        die "The install folder '$PREFIX' contains a character Plasmify cannot use there (\" \` \$ \\ % : | &). Set PREFIX to another folder."
    fi
}

remove_app() {
    rm -rf "$APP_DIR" "$BIN" "$DESKTOP" "$ICON" "${OLD_FILES[@]}"
}

# A folder every way Plasmify may have written it into configs: as given, the way pathlib
# tidies it (which keeps ".."), fully normalised, and each as a file:// address (Path.as_uri
# percent-encodes spaces, @, accents...). -I: never import code from the current folder.
path_forms() {
    printf '%s\n' "$1"
    python3 -I - "$1" 2>/dev/null <<'PY' || true
import os, pathlib, sys
seen = set()
for path in (str(pathlib.Path(sys.argv[1])), os.path.normpath(sys.argv[1])):
    for form in (path, pathlib.Path(path).as_uri()[len("file://"):]):
        if form not in seen:
            seen.add(form)
            print(form)
PY
}

# What of Plasmify the desktop still uses, one item per line (nothing: safe to purge).
in_use() {
    local config="$1" data="$2" form folders=()
    while IFS= read -r form; do
        folders+=(-e "$form/plasmify/")
    done < <(path_forms "${data%/}"; path_forms "${config%/}")
    config="${config%/}"
    grep -qs '^ColorScheme=Plasmify' "$config/kdeglobals" && echo "its colour scheme"
    grep -qs '^Theme=Plasmify-' "$config/kdeglobals" && echo "its icon theme"
    grep -qs '^name=plasmify-' "$config/plasmarc" && echo "its panel style"
    grep -qs '^Theme=plasmify-splash-' "$config/ksplashrc" && echo "its splash screen"
    grep -qsF "${folders[@]}" "$config/plasma-org.kde.plasma.desktop-appletsrc" "$config/kscreenlockerrc" &&
        echo "its wallpapers"
    grep -qsF "${folders[@]}" "$config/fastfetch/config.jsonc" && echo "its terminal logo"
    return 0
}

restore_hint() {
    if [[ -x "$BIN" ]]; then
        echo "plasmify restore original"
    else
        echo "PYTHONPATH=\"$SRC_DIR/src\" python3 -m plasmify restore original"
    fi
}

uninstall() {
    local used
    used="$(in_use "${XDG_CONFIG_HOME:-$HOME/.config}" "${XDG_DATA_HOME:-$HOME/.local/share}")"
    remove_app
    info "Plasmify removed. Backups are kept in ${XDG_STATE_HOME:-$HOME/.local/state}/plasmify."
    if [[ -n "$used" ]]; then
        info "Your desktop still uses Plasmify's theme. To put your old setup back: $(restore_hint)"
    fi
}

purge() {
    local data="${XDG_DATA_HOME:-$HOME/.local/share}" state="${XDG_STATE_HOME:-$HOME/.local/state}"
    local config="${XDG_CONFIG_HOME:-$HOME/.config}" cache="${XDG_CACHE_HOME:-$HOME/.cache}"
    local targets=("$data/plasmify" "$state/plasmify" "$config/plasmify" "$cache/plasmify")
    shopt -s nullglob
    targets+=("$data"/color-schemes/Plasmify*.colors "$data"/icons/Plasmify-* "$data"/plasma/desktoptheme/plasmify-*)
    targets+=("$data"/plasma/look-and-feel/plasmify-splash-*)
    shopt -u nullglob
    local used
    used="$(in_use "$config" "$data")"
    if [[ -n "$used" ]]; then
        info "Your desktop still uses Plasmify (${used//$'\n'/, }); deleting it now would leave gaps."
        info "Put your old setup back first: $(restore_hint)"
        info "Then run --purge again. If that does not clear it (older versions may no longer have your"
        info "setup from before Plasmify), pick other colours, icons, Plasma style, splash screen,"
        info "wallpaper and lock screen picture in System Settings first."
        if [[ "$used" == *"terminal logo"* ]]; then
            info "For the terminal logo: choose another logo in ~/.config/fastfetch/config.jsonc, or delete that file."
        fi
        exit 1
    fi
    info "This deletes your Plasmify presets, wallpapers, backups and generated themes:"
    printf '   %s\n' "${targets[@]}"
    if [[ "${2:-}" != "--yes" ]]; then
        read -r -p "Type yes to continue: " answer
        [[ "$answer" == "yes" ]] || { info "Nothing was deleted."; exit 0; }
    fi
    rm -rf -- "${targets[@]}"
    remove_app
    info "Plasmify's presets, wallpapers, backups and generated themes are gone. Downloaded themes, cursors and fonts, installed packages and the login screen background stay."
}

[[ "${1:-}" == "--uninstall" ]] && { uninstall; exit 0; }
[[ "${1:-}" == "--purge" ]] && { purge "$@"; exit 0; }

# The installed Plasma version, as text such as "plasmashell 6.6.4" (empty when unknown).
# plasmashell starts Qt before it reads --version, so it gets a platform that needs no display (SSH,
# a TTY); some builds crash on one of them (Fedora 44's on offscreen). The package manager knows too.
plasma_version() {
    local platform out name
    for platform in minimal offscreen; do
        out="$(QT_QPA_PLATFORM=$platform plasmashell --version 2>/dev/null || true)"
        [[ "$out" =~ [0-9]+\.[0-9]+ ]] && { echo "$out"; return; }
    done
    if command -v pacman >/dev/null; then
        out="$(pacman -Q plasma-workspace 2>/dev/null)" && { echo "$out"; return; }
    fi
    if command -v rpm >/dev/null; then
        for name in plasma-workspace plasma6-workspace; do
            out="$(rpm -q --qf '%{VERSION}' "$name" 2>/dev/null)" && { echo "$out"; return; }
        done
    fi
    if command -v dpkg-query >/dev/null; then
        out="$(dpkg-query -W -f '${Version}' plasma-workspace 2>/dev/null)" && { echo "$out"; return; }
    fi
    return 0
}

# The major version in what plasma_version printed ("6" for "plasmashell 6.6.4" or "4:6.3.4-2").
plasma_major() {
    [[ "$1" =~ (^|[[:space:]:])([0-9]+)\.[0-9]+ ]] && echo "${BASH_REMATCH[2]}"
    return 0
}

check_prefix
command -v plasmashell >/dev/null || die "KDE Plasma is not installed."
found="$(plasma_version)"
major="$(plasma_major "$found")"
[[ -n "$major" && "$major" -ge 6 ]] || die "KDE Plasma 6 is required (found ${found:-an unknown version})."

# Dependencies per package manager: Python 3.11+, PyQt6 and git.
if command -v pacman >/dev/null; then
    deps=(python python-pyqt6 git)
    mapfile -t missing < <(pacman -T "${deps[@]}" || true)
    install_cmd=(sudo pacman -S --needed)
elif command -v dnf >/dev/null; then
    deps=(python3 python3-pyqt6 git)
    mapfile -t missing < <(for p in "${deps[@]}"; do rpm -q --quiet --whatprovides "$p" || echo "$p"; done)
    install_cmd=(sudo dnf install --setopt=install_weak_deps=False)
elif command -v zypper >/dev/null; then
    deps=(python3 python3-PyQt6 git)
    mapfile -t missing < <(for p in "${deps[@]}"; do rpm -q --quiet --whatprovides "$p" || echo "$p"; done)
    install_cmd=(sudo zypper install --no-recommends)
elif command -v apt-get >/dev/null; then
    deps=(python3 python3-pyqt6 git)
    mapfile -t missing < <(for p in "${deps[@]}"; do
        dpkg-query -W -f '${db:Status-Abbrev}' "$p" 2>/dev/null | grep -q '^ii' || echo "$p"
    done)
    install_cmd=(sudo apt-get install --no-install-recommends)
else
    die "Unsupported distribution: Plasmify supports Arch, Fedora, openSUSE and Debian/Ubuntu."
fi

if ((${#missing[@]})); then
    info "Installing dependencies: ${missing[*]}"
    "${install_cmd[@]}" "${missing[@]}"
fi

python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || die "Python 3.11 or newer is required."

info "Installing to $APP_DIR"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR" "$(dirname "$BIN")" "$(dirname "$DESKTOP")" "$(dirname "$ICON")"
cp -r "$SRC_DIR/src/plasmify" "$APP_DIR/"
shopt -s globstar nullglob
rm -rf "$APP_DIR"/plasmify/**/__pycache__
shopt -u globstar nullglob

cat >"$BIN" <<LAUNCHER
#!/bin/sh
PYTHONPATH="$APP_DIR\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -P -m plasmify "\$@"
LAUNCHER
chmod +x "$BIN"

# The menu entry starts the launcher by its full path, so it works even when ~/.local/bin is
# not in PATH yet.
# (PREFIX was checked above: no quotes, $, backslashes or sed separators can appear in it.)
rm -f "${OLD_FILES[@]}"
sed "s|^Exec=plasmify|Exec=\"$BIN\"|" "$SRC_DIR/data/io.github.heyquatch.plasmify.desktop" >"$DESKTOP.new"
chmod 644 "$DESKTOP.new"
mv -f "$DESKTOP.new" "$DESKTOP"
install -m644 "$SRC_DIR/src/plasmify/assets/plasmify.svg" "$ICON"
command -v update-desktop-database >/dev/null && update-desktop-database -q "$(dirname "$DESKTOP")" || true

command_name=plasmify
case ":$PATH:" in
    *":$PREFIX/bin:"*) ;;
    *)
        info "Note: $PREFIX/bin is not in your PATH, so the plasmify command needs its full path."
        command_name="$BIN"
        ;;
esac
info "Done. Launch Plasmify from the application menu or run: $command_name gui"
