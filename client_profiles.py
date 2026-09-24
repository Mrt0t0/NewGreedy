#!/usr/bin/env python3
"""NewGreedy v1.7.7 — emulated BitTorrent client profiles"""
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
import random

PROFILES = {
    "qbittorrent": {
        "name": "qBittorrent",
        "identities": [
            (b"-qB5000-", "qBittorrent/5.0.0"),
            (b"-qB5001-", "qBittorrent/5.0.1"),
            (b"-qB5010-", "qBittorrent/5.1.0"),
            (b"-qB5012-", "qBittorrent/5.1.2"),
        ],
        "peer_id_charset": "alnum",
        "param_order": ["info_hash", "peer_id", "port", "uploaded", "downloaded",
                        "left", "corrupt", "key", "event", "numwant", "compact",
                        "no_peer_id", "supportcrypto", "redundant", "trackerid"],
        "headers": {
            "Accept-Encoding": "gzip",
            "Accept": "text/plain, application/x-bittorrent, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
        },
        "numwant": 200,
        "extra_params": {"supportcrypto": "1", "no_peer_id": "1", "compact": "1"},
        "sends_corrupt": True,
    },
    "transmission": {
        "name": "Transmission",
        "identities": [
            (b"-TR4050-", "Transmission/4.0.5"),
            (b"-TR4060-", "Transmission/4.0.6"),
            (b"-TR4100-", "Transmission/4.1.0"),
        ],
        "peer_id_charset": "alnum",
        "param_order": ["info_hash", "peer_id", "port", "uploaded", "downloaded",
                        "left", "numwant", "key", "compact", "supportcrypto", "event",
                        "trackerid"],
        "headers": {
            "Accept-Encoding": "gzip;q=1.0, deflate, identity",
            "Accept": "text/plain, application/x-bittorrent",
            "Connection": "keep-alive",
        },
        "numwant": 80,
        "extra_params": {"compact": "1", "supportcrypto": "1"},
        "sends_corrupt": False,
    },
    "deluge": {
        "name": "Deluge",
        "identities": [
            (b"-DE2110-", "Deluge/2.1.1 libtorrent/2.0.9"),
            (b"-DE2200-", "Deluge/2.2.0 libtorrent/2.0.10"),
        ],
        "peer_id_charset": "printable",
        "param_order": ["info_hash", "peer_id", "port", "uploaded", "downloaded",
                        "left", "corrupt", "key", "event", "numwant", "compact",
                        "no_peer_id", "supportcrypto", "redundant", "trackerid"],
        "headers": {
            "Accept-Encoding": "gzip",
            "Accept": "*/*",
            "Connection": "keep-alive",
        },
        "numwant": 200,
        "extra_params": {"supportcrypto": "1", "no_peer_id": "1", "compact": "1",
                         "redundant": "0"},
        "sends_corrupt": True,
    },
    "libtorrent": {
        "name": "libtorrent",
        "identities": [
            (b"-LT2090-", "libtorrent/2.0.9"),
            (b"-LT20A0-", "libtorrent/2.0.10"),
            (b"-LT20B0-", "libtorrent/2.0.11"),
        ],
        "peer_id_charset": "printable",
        "param_order": ["info_hash", "peer_id", "port", "uploaded", "downloaded",
                        "left", "corrupt", "key", "event", "numwant", "compact",
                        "no_peer_id", "supportcrypto", "redundant", "trackerid"],
        "headers": {
            "Accept-Encoding": "gzip",
            "Accept": "text/plain, application/x-bittorrent",
            "Connection": "keep-alive",
        },
        "numwant": 200,
        "extra_params": {"supportcrypto": "1", "no_peer_id": "1", "compact": "1"},
        "sends_corrupt": True,
    },
    "rtorrent": {
        "name": "rTorrent",
        "identities": [
            (b"-lt0D60-", "rtorrent/0.9.6/0.13.6"),
            (b"-lt0D80-", "rtorrent/0.9.8/0.13.8"),
        ],
        "peer_id_charset": "alnum",
        "param_order": ["info_hash", "peer_id", "port", "uploaded", "downloaded",
                        "left", "key", "compact", "numwant", "event", "trackerid"],
        "headers": {
            "Accept-Encoding": "gzip",
        },
        "numwant": 100,
        "extra_params": {"compact": "1"},
        "sends_corrupt": False,
    },
}


_ALNUM = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
_PRINTABLE = _ALNUM + b"-._~"


def profile_names():
    return sorted(PROFILES.keys())


def resolve_profile(name: str) -> str:
    name = (name or "").strip().lower()
    return name if name in PROFILES else random.choice(profile_names())


def user_agents(profile: dict) -> list:
    return [ua for _, ua in profile["identities"]]


def pick_identity(profile: dict, user_agent: str = "") -> tuple:
    for prefix, ua in profile["identities"]:
        if user_agent and ua == user_agent:
            return prefix, ua
    return random.choice(profile["identities"])


def make_peer_id(profile: dict, user_agent: str = "") -> bytes:
    prefix, _ = pick_identity(profile, user_agent)
    charset = _ALNUM if profile.get("peer_id_charset") == "alnum" else _PRINTABLE
    return prefix + bytes(random.choice(charset) for _ in range(20 - len(prefix)))


def pick_user_agent(profile: dict) -> str:
    return random.choice(user_agents(profile))


def identity_index(profile: dict, user_agent: str) -> int:
    for i, (_, ua) in enumerate(profile["identities"]):
        if ua == user_agent:
            return i
    return -1


def identity_at(profile: dict, index: int) -> tuple:
    ids = profile["identities"]
    return ids[max(0, min(index, len(ids) - 1))]


def bump_identity(profile: dict, user_agent: str) -> tuple:
    i = identity_index(profile, user_agent)
    if i < 0:
        return pick_identity(profile, user_agent)
    return identity_at(profile, i + 1)


def can_bump(profile: dict, user_agent: str) -> bool:
    i = identity_index(profile, user_agent)
    return 0 <= i < len(profile["identities"]) - 1


def order_params(profile: dict, pairs: list) -> list:
    index = {k: i for i, k in enumerate(profile["param_order"])}
    known = [kv for kv in pairs if kv[0] in index]
    unknown = [kv for kv in pairs if kv[0] not in index]
    known.sort(key=lambda kv: index[kv[0]])
    return known + unknown
