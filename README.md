# Roxie — Plymouth Boot Theme

Minimal, black-and-purple, premium boot animation for Arch Linux.
Thin line draws itself across the screen, resolves into the glowing
**ROXIE** wordmark, settles into a slow breathing glow with a small
segmented spinner underneath, then fades to black on completion.

No hacker-theme clichés, no RGB, no green terminal text. Pure black
(`#000000`) with a violet/purple palette (`#8b5cf6`, `#a855f7`,
`#c084fc`) and small white highlights only.

```
Roxie/
    Roxie.plymouth        theme descriptor
    Roxie.script           boot animation logic
    logo_final.png          crisp resting wordmark (idle state)
    logo_glow.png            soft bloom underlay (breathing glow)
    logo/frame_00..27.png    the "line draws itself into the word" reveal
    particle.png              ambient background fleck
    spinner_dot.png           spinner ring dot
    bullet.png                 password-prompt dot (LUKS etc.)
    generate_assets.py         regenerates every PNG above (see below)
    README.md                   this file
```

## Install (Arch Linux)

```bash
# 1. Make sure plymouth is installed
sudo pacman -S plymouth

# 2. Copy the theme into place
sudo cp -r Roxie /usr/share/plymouth/themes/

# 3. Set it as the default theme and rebuild the initramfs
sudo plymouth-set-default-theme -R Roxie
```

`plymouth-set-default-theme -R` regenerates your initramfs
automatically via `mkinitcpio`. If that step reports no changes, or
you've never enabled Plymouth on this system before, do the two
things below first, then re-run step 3.

### One-time Plymouth setup (skip if already using Plymouth)

**a) Add the `plymouth` hook.** Edit `/etc/mkinitcpio.conf` and add
`plymouth` to `HOOKS`, right after `base udev`:

```
HOOKS=(base udev plymouth autodetect microcode modconf kms keyboard keymap consolefont block filesystems fsck)
```

If your root filesystem is on LUKS, put `plymouth` **before**
`encrypt` so the passphrase prompt renders through the theme instead
of as raw text:

```
HOOKS=(base udev plymouth autodetect modconf kms keyboard keymap consolefont block encrypt filesystems fsck)
```

**b) Add the `splash` kernel parameter.**

- *GRUB* — edit `/etc/default/grub`, add `splash` to
  `GRUB_CMDLINE_LINUX_DEFAULT`, then:
  ```bash
  sudo grub-mkconfig -o /boot/grub/grub.cfg
  ```
- *systemd-boot* — add `splash` to the `options` line in the
  relevant entry under `/boot/loader/entries/*.conf`.

Then rebuild the initramfs and reboot:

```bash
sudo mkinitcpio -P
sudo reboot
```

## Verify

```bash
plymouth-set-default-theme --list     # Roxie should be listed
plymouth-set-default-theme            # confirms Roxie is active
```

There is no reliable live-preview for a script-based theme short of
actually booting or shutting down — `plymouthd --debug` plus
`journalctl -b -1 -u plymouth-start.service` afterwards is the most
useful way to check for script errors if something doesn't render.
Boot with `plymouth.debug` on the kernel command line to get a
`/var/log/plymouth-debug.log` for that boot.

## Uninstall / switch back

```bash
sudo plymouth-set-default-theme -R <other-theme-name>
# e.g. sudo plymouth-set-default-theme -R bgrt
sudo rm -rf /usr/share/plymouth/themes/Roxie
```

## Customizing

Every timing and size in the animation is a named constant at the
top of `Roxie.script` (section `0.`) — refresh rate, fade/reveal
durations, breathing speed and amplitude, spinner speed, dot count,
trail length, colors are not re-declared there since they're already
baked into the PNGs.

To change the **wordmark, palette, glow strength, letter spacing, or
reveal timing itself**, edit the constants at the top of
`generate_assets.py` (colors, `FONT_SIZE`, `TRACKING`,
`REVEAL_LINE_FRAMES`, `REVEAL_LETTER_FRAMES`, `MAX_BLUR`, etc.) and
regenerate:

```bash
pip install --break-system-packages pillow   # if not already installed
python3 generate_assets.py
```

This overwrites everything under `Roxie/` except `Roxie.script`,
`Roxie.plymouth`, and this README. If you rename the word, the
existing frame count (28) and file names stay the same — only pixel
content changes — so `Roxie.script` needs no edits for a recolor or
a font tweak. If you change `REVEAL_LINE_FRAMES` /
`REVEAL_LETTER_FRAMES` so the total frame count is no longer 28,
update `logo_reveal_frame_count` at the top of `Roxie.script` to
match.

## Design notes

- All art is rendered "4K-native" and only ever scaled **down** at
  boot time for 1080p/1440p — never upscaled — so the wordmark stays
  sharp at every supported resolution.
- The reveal flipbook is pre-rendered because Plymouth Script itself
  cannot do arbitrary blur/bloom/vector drawing at boot time, and
  cannot be relied on to have a nice font available inside the
  initramfs. Baking it to PNG once at build time keeps the boot-time
  script to nothing but sprite positioning and opacity math.
- The idle "breathing" only touches the soft glow layer behind the
  wordmark, never the crisp letters themselves — so it reads as a
  living glow, not flicker.
- Ambient particles are placed once at boot and never move; only
  their opacity is nudged per tick. Nothing is redrawn or re-decoded
  in the animation loop, which is what keeps this light on
  integrated GPUs.
