#!/usr/bin/env python3
"""Guard for catalog.json pushes — stdlib only. Schema 5: corridors.

The app never crashes on a bad catalog: JSONDecoder fails, the refresh is
dropped, and every install silently keeps its cached copy forever. That
failure mode is invisible, so this check is the only thing standing between
a bad push and frozen remote updates.

Mirrors the strictness of the app's Swift decoder (Models/Models.swift in
mrfartman77/thaicash): any enum string the models don't know — group,
rateSource, fee kind/per/feeOn/source, funding source — fails the whole
decode there, so it fails here too.

Usage: validate_catalog.py <catalog.json> [--previous <old-catalog.json>]

With --previous, also requires catalogUpdated to lexicographically increase
whenever the catalog content changed (the app only adopts a refresh when
fresh.catalogUpdated > cached.catalogUpdated — forgetting to bump the stamp
ships an update nobody receives).
"""
import json
import re
import sys

SCHEMA_VERSION = 5
GROUPS = {"cash_in_hand", "thb_in_bank", "crypto_thb_bank"}
RATE_SOURCES = {"mid_market", "quoted", "mid_market_margin"}
FEE_KINDS = {"rate_margin", "pct_usd", "flat_usd", "flat_thb"}
FEE_SCOPES = {"transaction", "withdrawal"}
FEE_BASES = {"base", "send", "over_allowance"}
FEE_ORIGINS = {"stored", "user"}
FUNDING_SOURCES = {"bank_ach", "debit_card", "credit_card"}
WHEN_BOOL_KEYS = {"dccAccepted", "isWeekend", "overFxLimit", "overFreeAtm"}

errors = []


def err(msg):
    errors.append(msg)


def is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def require_str(obj, key, where):
    v = obj.get(key)
    if not isinstance(v, str) or not v.strip():
        err(f"{where}: missing or empty '{key}'")
        return None
    return v


def check_fee(fee, where):
    if not isinstance(fee, dict):
        err(f"{where}: fee is not an object")
        return
    kind = fee.get("kind")
    if kind not in FEE_KINDS:
        err(f"{where}: unknown fee kind {kind!r}")
    if not is_number(fee.get("value")):
        err(f"{where}: fee 'value' must be a number")
    require_str(fee, "label", where)
    for key in ("minUsd", "maxUsd"):
        if key in fee and not is_number(fee[key]):
            err(f"{where}: '{key}' must be a number")
    if "per" in fee and fee["per"] not in FEE_SCOPES:
        err(f"{where}: unknown 'per' {fee['per']!r}")
    if "feeOn" in fee and fee["feeOn"] not in FEE_BASES:
        err(f"{where}: unknown 'feeOn' {fee['feeOn']!r}")
    if "source" in fee and fee["source"] not in FEE_ORIGINS:
        err(f"{where}: unknown 'source' {fee['source']!r}")
    if "interestBase" in fee and not isinstance(fee["interestBase"], bool):
        err(f"{where}: 'interestBase' must be a boolean")
    when = fee.get("when")
    if when is not None:
        if not isinstance(when, dict):
            err(f"{where}: 'when' is not an object")
            return
        for key, v in when.items():
            if key in WHEN_BOOL_KEYS:
                if not isinstance(v, bool):
                    err(f"{where}: when.{key} must be a boolean")
            elif key == "fundingSource":
                if v not in FUNDING_SOURCES:
                    err(f"{where}: unknown when.fundingSource {v!r}")
            else:
                err(f"{where}: unknown when-key {key!r}")


def check_leg(leg, index, corridor="top"):
    where = f"{corridor}.legs[{index}]"
    if not isinstance(leg, dict):
        err(f"{where}: not an object")
        return None
    leg_id = require_str(leg, "id", where)
    if leg_id:
        where = f"{corridor}.legs[{index}] ({leg_id})"
    require_str(leg, "label", where)
    group = leg.get("group")
    if group not in GROUPS:
        err(f"{where}: group {group!r} not in {sorted(GROUPS)}")
    if leg.get("rateSource") not in RATE_SOURCES:
        err(f"{where}: rateSource {leg.get('rateSource')!r} not in {sorted(RATE_SOURCES)}")
    fees = leg.get("fees")
    if not isinstance(fees, list):
        err(f"{where}: missing 'fees' list (empty [] is fine)")
    else:
        for i, fee in enumerate(fees):
            check_fee(fee, f"{where}.fees[{i}]")
    for key in ("fxMarginPct", "typicalBoothMargin", "amountCapThb", "freeAtmAmountThb"):
        if key in leg and not is_number(leg[key]):
            err(f"{where}: '{key}' must be a number")
    if "freeAtmWithdrawals" in leg and not isinstance(leg["freeAtmWithdrawals"], int):
        err(f"{where}: 'freeAtmWithdrawals' must be an integer")
    for key in ("subgroup", "subgroupLabel", "subgroupNote", "notes", "linkURL",
                "speed", "acceptance", "acceptanceNote", "taxFlag", "volatility"):
        if key in leg and not isinstance(leg[key], str):
            err(f"{where}: '{key}' must be a string")
    interest = leg.get("interest")
    if interest is not None:
        if not isinstance(interest, dict) or not is_number(interest.get("apr")) \
                or not isinstance(interest.get("accruesOnFees"), bool):
            err(f"{where}: 'interest' needs numeric apr + boolean accruesOnFees")
    return leg_id


def check_entries(entries, where):
    if not isinstance(entries, list) or not entries:
        err(f"{where}: must be a non-empty list")
        return
    for i, e in enumerate(entries):
        ew = f"{where}[{i}]"
        if not isinstance(e, dict):
            err(f"{ew}: not an object")
            continue
        eid = require_str(e, "id", ew)
        if eid:
            ew = f"{where}[{i}] ({eid})"
        require_str(e, "name", ew)
        require_str(e, "areas", ew)


def check_corridor(cor, index):
    where = f"corridors[{index}]"
    if not isinstance(cor, dict):
        err(f"{where}: not an object")
        return None
    cid = require_str(cor, "id", where)
    if cid:
        where = f"corridors[{index}] ({cid})"
    for key in ("base", "baseSymbol", "label"):
        require_str(cor, key, where)

    legs = cor.get("legs")
    if not isinstance(legs, list) or not legs:
        err(f"{where}: 'legs' must be a non-empty list")
    else:
        ids = [check_leg(leg, i, where) for i, leg in enumerate(legs)]
        ids = [i for i in ids if i]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            err(f"{where}: duplicate leg ids: {sorted(dupes)}")

    booths = cor.get("booths")
    if booths is not None:
        check_entries(booths, f"{where}.booths")

    directories = cor.get("directories")
    if directories is not None:
        if not isinstance(directories, dict):
            err(f"{where}: 'directories' must be an object")
        else:
            for key, section in directories.items():
                dwhere = f"{where}.directories.{key}"
                if not isinstance(section, dict):
                    err(f"{dwhere}: not an object")
                    continue
                require_str(section, "title", dwhere)
                check_entries(section.get("entries"), f"{dwhere}.entries")
    return cid


def validate(catalog):
    if not isinstance(catalog, dict):
        err("top level: not a JSON object")
        return

    if catalog.get("schemaVersion") != SCHEMA_VERSION:
        err(f"schemaVersion must be {SCHEMA_VERSION}, got {catalog.get('schemaVersion')!r}")

    updated = require_str(catalog, "catalogUpdated", "top level")
    if updated and not re.match(r"^\d{4}-\d{2}-\d{2}", updated):
        err(f"catalogUpdated {updated!r} must start with an ISO date (YYYY-MM-DD)")

    corridors = catalog.get("corridors")
    if not isinstance(corridors, list) or not corridors:
        err("top level: 'corridors' must be a non-empty list")
        return
    cids = [check_corridor(cor, i) for i, cor in enumerate(corridors)]
    cids = [c for c in cids if c]
    dupes = {c for c in cids if cids.count(c) > 1}
    if dupes:
        err(f"duplicate corridor ids: {sorted(dupes)}")


def check_updated_increases(current, prev_path):
    try:
        with open(prev_path, "rb") as f:
            prev_raw = f.read()
    except OSError as e:
        err(f"--previous: cannot read {prev_path}: {e}")
        return
    try:
        prev = json.loads(prev_raw)
    except ValueError:
        print(f"note: previous catalog is not valid JSON — skipping the stamp check")
        return

    cur_updated = current.get("catalogUpdated", "")
    prev_updated = prev.get("catalogUpdated", "")
    if json.dumps(current, sort_keys=True) == json.dumps(prev, sort_keys=True):
        return   # nothing changed, stamp may stay put
    if not (isinstance(cur_updated, str) and cur_updated > prev_updated):
        err(f"catalog changed but catalogUpdated did not increase "
            f"({prev_updated!r} → {cur_updated!r}) — the app will never adopt this update")


def main():
    args = sys.argv[1:]
    if not args or len(args) not in (1, 3) or (len(args) == 3 and args[1] != "--previous"):
        print(__doc__)
        sys.exit(2)

    path = args[0]
    try:
        with open(path, "rb") as f:
            catalog = json.load(f)
    except OSError as e:
        print(f"FAIL: cannot read {path}: {e}")
        sys.exit(1)
    except ValueError as e:
        print(f"FAIL: {path} is not valid JSON: {e}")
        sys.exit(1)

    validate(catalog)
    if len(args) == 3 and isinstance(catalog, dict):
        check_updated_increases(catalog, args[2])

    if errors:
        print(f"FAIL: {len(errors)} problem(s) in {path}:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    corridors = catalog.get("corridors", [])
    n_legs = sum(len(c.get("legs", [])) for c in corridors if isinstance(c, dict))
    print(f"OK: {path} — schema {SCHEMA_VERSION}, {len(corridors)} corridors / {n_legs} legs, "
          f"stamped {catalog.get('catalogUpdated')}")


if __name__ == "__main__":
    main()
