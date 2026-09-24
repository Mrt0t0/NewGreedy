#!/usr/bin/env python3
"""NewGreedy v1.7.7 — config.ini migration between versions"""
#
# NewGreedy - BitTorrent announce proxy
# Copyright (C) 2026  Mrt0t0
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
import argparse
import configparser
import sys

FORCED_DEFAULTS = {("anti_detection", "intercept_scrape"): "false"}
FORCED_REASON   = "v1.7.7 default, a client that never scrapes stands out"
CARRIED_NOTE    = "; carried over from your previous config.ini"


def _parse(text):
    c = configparser.ConfigParser(inline_comment_prefixes=(";", "#"), interpolation=None)
    c.read_string(text)
    return c


def _assign(lhs, value):
    line = f"{lhs}= {value}" if lhs.endswith(" ") else f"{lhs} = {value}"
    return line.rstrip()


def _section_bounds(lines):
    out, name, start = {}, None, None

    def close(end):
        if name is None:
            return
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        out[name] = (start, end)

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            close(i)
            name, start = stripped[1:-1], i
    close(len(lines))
    return out


def _carry_over(lines, carried):
    if not carried:
        return lines
    by_section = {}
    for section, key, value in carried:
        by_section.setdefault(section, []).append((key, value))
    bounds = _section_bounds(lines)
    known   = [s for s in by_section if s in bounds]
    unknown = [s for s in by_section if s not in bounds]
    for section in sorted(known, key=lambda s: bounds[s][1], reverse=True):
        block = [CARRIED_NOTE] + [f"{k} = {v}" for k, v in by_section[section]]
        lines[bounds[section][1]:bounds[section][1]] = block
    for section in unknown:
        lines += ["", f"[{section}]", CARRIED_NOTE]
        lines += [f"{k} = {v}" for k, v in by_section[section]]
    return lines


def _verify(merged, old, forced):
    check = _parse(merged)
    for section in old.sections():
        for key in old[section]:
            if (section, key) in forced:
                continue
            if not check.has_option(section, key):
                raise ValueError(f"[{section}] {key} was lost in the merge")
            if check[section][key] != old[section][key]:
                raise ValueError(f"[{section}] {key}: {old[section][key]!r} "
                                 f"became {check[section][key]!r}")


def merge(shipped_text, current_text, forced=None):
    forced = FORCED_DEFAULTS if forced is None else forced
    new, old = _parse(shipped_text), _parse(current_text)

    kept, added, changed, carried = [], [], [], []
    for section in new.sections():
        for key in new[section]:
            if old.has_option(section, key):
                kept.append((section, key))
            else:
                added.append((section, key, new[section][key]))
    for section in old.sections():
        for key in old[section]:
            if not new.has_option(section, key):
                carried.append((section, key, old[section][key]))

    lines = shipped_text.splitlines()
    section = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
            continue
        if not section or "=" not in line or stripped.startswith((";", "#")):
            continue
        lhs = line.split("=", 1)[0]
        key = lhs.strip()
        want = forced.get((section, key))
        if want is not None:
            if old.has_option(section, key) and old[section][key].strip().lower() != want:
                changed.append((section, key, old[section][key], want))
            lines[i] = _assign(lhs, want)
            continue
        if old.has_option(section, key):
            lines[i] = _assign(lhs, old[section][key])

    merged = "\n".join(_carry_over(lines, carried)).rstrip("\n") + "\n"
    _verify(merged, old, forced)
    return merged, {"kept": kept, "added": added, "changed": changed, "carried": carried}


def format_report(report):
    out = [f"kept    {len(report['kept'])} setting(s) from the current config.ini",
           f"added   {len(report['added'])} new key(s)"]
    for section, key, value in report["added"]:
        out.append(f"  + [{section}] {key} = {value}")
    for section, key, was, now in report["changed"]:
        out.append(f"  ~ [{section}] {key}: {was} -> {now}   ({FORCED_REASON})")
    for section, key, value in report["carried"]:
        out.append(f"  ? [{section}] {key} = {value}   (unknown in this version, kept)")
    return out


def merge_files(shipped, current, out=None, forced=None):
    with open(shipped, encoding="utf-8") as f:
        shipped_text = f.read()
    try:
        with open(current, encoding="utf-8") as f:
            current_text = f.read()
    except FileNotFoundError:
        current_text = ""
    merged, report = merge(shipped_text, current_text, forced)
    if out:
        try:
            from _version import atomic_write_bytes
            atomic_write_bytes(out, merged.encode("utf-8"))
        except ImportError:
            with open(out, "w", encoding="utf-8") as f:
                f.write(merged)
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description="Merge a NewGreedy config.ini forward")
    p.add_argument("--shipped", required=True, help="config.ini of the new version")
    p.add_argument("--current", required=True, help="config.ini already installed")
    p.add_argument("--out", help="where to write the result; omit for a preview")
    p.add_argument("--keep-scrape", action="store_true",
                   help="leave intercept_scrape as it is instead of applying the new default")
    args = p.parse_args(argv)
    forced = {} if args.keep_scrape else FORCED_DEFAULTS
    try:
        report = merge_files(args.shipped, args.current, args.out, forced)
    except (OSError, ValueError, configparser.Error) as e:
        print(f"config merge failed: {e}", file=sys.stderr)
        return 1
    for line in format_report(report):
        print(line)
    if args.out:
        print(f"merged config written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
