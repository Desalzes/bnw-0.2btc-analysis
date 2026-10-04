"""Reconcile every gpu_setperm config on disk against the run ledger and the checkpoints (read-only; no GPU).

usage: python ledger_recon.py [--list-never]

For each .conf under harness/templates/ and scratch/ it computes the run signature the engine would use today
(gpu_setperm.sig_gpu: config, prize, paths, passphrases, block size and the engine-source hash), then matches it to
scratch/btc02-gpu/runs.jsonl and to every scratch/**/*.ck. A config is VERIFIED when a NO_MATCH ledger line covers
all its blocks with at least one block verified in that session; STARTED when it has START lines only; NEVER
otherwise. Checks: ledger runs that match no config on disk (a config edited after its run), tested == the config's
candidate count (DISTINCT configs excepted, they are listed), plants found per block, audits skipped, the sign-off
text, checkpoint completeness and that checkpoint valid == ledger valid, and a binomial z of valid/tested
(BIP39-12 1/16, Electrum 1/256). Configs other tools read (SLOT, TARGET directives) are counted and skipped.

The signature includes the hash of the engine sources AS CHECKED OUT. After an engine change, run this from a checkout
of the engine the runs used (e.g. `git worktree add ../pb-207a235 207a235`), or every older run shows as NEVER.
"""
import collections
import glob
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, HERE)
os.chdir(HERE)
import gpu_setperm as g   # noqa: E402  (no OpenCL import: sp_gpu is imported lazily)
import sp_space            # noqa: E402

PASS_RATE = {("bip39", 12): 1 / 16, ("bip39", 15): 1 / 32, ("bip39", 18): 1 / 64, ("bip39", 21): 1 / 128,
             ("bip39", 24): 1 / 256}


def load_configs():
    paths = sorted(set(glob.glob(HERE + "/templates/**/*.conf", recursive=True)
                       + [q for q in glob.glob(REPO + "/scratch/**/*.conf", recursive=True)
                          if "/btc02-gpu-audit-" not in q]))   # an auditor's copy of the harness, not a run source
    out, other = [], []
    for p in paths:
        try:
            sp = sp_space.Space.load(p)
        except BaseException as e:            # setperm's parser exits on directives of other tools (SLOT, TARGET)
            other.append((os.path.relpath(p, REPO), str(e)[:80]))
            continue
        ub = g.plan_block_units(sp)
        out.append(dict(path=os.path.relpath(p, REPO), name=os.path.basename(p)[:-5], sig=g.sig_gpu(sp, g.TARGET, ub),
                        total=sp.total, nblocks=-(-sp.units // ub), scheme=sp.scheme, n=sp.n, npass=len(sp.passes),
                        distinct=bool(sp.cfg.get("distinct")), hours=sp.estimate()["derivations"] / 230e3 / 3600))
    return out, other


def load_checkpoints():
    cks = collections.defaultdict(list)
    for p in glob.glob(REPO + "/scratch/**/*.ck", recursive=True):
        sig, done, bad = None, {}, 0
        for line in open(p):
            f = line.split()
            if not f:
                continue
            if f[0] == "SIG":
                sig = f[1]
            elif f[0] == "done":
                if len(f) == 5 and f[4] == "ok":
                    done[int(f[1])] = (int(f[2]), int(f[3]))
                else:
                    bad += 1
        cks[sig].append(dict(path=os.path.relpath(p, REPO), n=len(done), bad=bad,
                             contiguous=sorted(done) == list(range(len(done))),
                             tested=sum(t for t, _ in done.values()), derived=sum(d for _, d in done.values())))
    return cks


def main():
    cfgs, other = load_configs()
    cks = load_checkpoints()
    led = [json.loads(line) for line in open(g.LEDGER)]
    by_sig = collections.defaultdict(list)
    for c in cfgs:
        by_sig[c["sig"]].append(c)
    led_by = collections.defaultdict(list)
    for e in led:
        led_by[e["sig"]].append(e)
    issues, rows = [], []
    for s, es in led_by.items():
        if s not in by_sig:
            issues.append(f"ledger sig {s} (first {es[0]['ts']}) matches no config on disk with today's engine "
                          f"(an older engine, or a config edited after its run); checkpoints {[c['path'] for c in cks.get(s, [])]}")
    for s, cs in by_sig.items():
        c = min(cs, key=lambda x: (not x["path"].startswith("puzzles"), x["path"]))
        es = led_by.get(s, [])
        full = [e for e in es if e["status"] == "NO_MATCH" and e["blocks"] == c["nblocks"]
                and e["blocks_verified"] + e["blocks_from_checkpoint"] == c["nblocks"]]
        ver = [e for e in full if e["blocks_verified"] > 0]
        st = "VERIFIED" if ver else "TRUSTED_ONLY" if full else "STARTED" if es else "NEVER"
        rows.append((st, c, [x["name"] for x in cs if x is not c]))
        if not ver:
            continue
        e = ver[-1]
        nm = c["name"]
        if e["tested"] != c["total"]:
            issues.append(f"{nm}: tested {e['tested']:,} != candidates {c['total']:,}"
                          + (" (DISTINCT: check the distinct count by hand)" if c["distinct"] else " <-- NOT DISTINCT"))
        if e.get("audits_skipped") or e["audits"] != e["blocks_verified"]:
            issues.append(f"{nm}: audits {e['audits']} skipped {e.get('audits_skipped')} of {e['blocks_verified']} blocks")
        if e.get("plants_found") is not None and e["plants_found"] < 64 * e["blocks_verified"] \
                and e["plants_found"] != e["valid"]:
            issues.append(f"{nm}: plants found {e['plants_found']} < 64 per block and != valid {e['valid']}")
        if not e.get("signoff"):
            issues.append(f"{nm}: no sign-off text in the ledger")
        p = 1 / 256 if c["scheme"] == "electrum" else PASS_RATE.get((c["scheme"], c["n"]))
        if p and e["tested"] * p >= 30:
            z = (e["valid"] - e["tested"] * p) / math.sqrt(e["tested"] * p * (1 - p))
            if abs(z) > 4:
                issues.append(f"{nm}: valid/tested z {z:+.1f}")
        full_ck = [k for k in cks.get(s, []) if k["n"] == c["nblocks"]]
        if not full_ck:
            issues.append(f"{nm}: no checkpoint holds all {c['nblocks']} blocks")
        for k in cks.get(s, []):
            if k["bad"] or not k["contiguous"]:
                issues.append(f"{nm}: checkpoint {k['path']} has {k['bad']} malformed done lines, contiguous={k['contiguous']}")
        for k in full_ck:
            if k["derived"] % c["npass"] or k["derived"] // c["npass"] != e["valid"]:
                issues.append(f"{nm}: checkpoint {k['path']} valid {k['derived'] / c['npass']:,.0f} != ledger {e['valid']:,}")
    cnt = collections.Counter(st for st, _, _ in rows)
    print(f"{len(cfgs)} configs ({len(by_sig)} distinct runs), {len(other)} for other tools; ledger {len(led)} lines")
    print("status:", dict(cnt))
    print("== issues ==")
    print("\n".join(issues) if issues else "none")
    print("== started, not finished ==")
    for st, c, al in rows:
        if st in ("STARTED", "TRUSTED_ONLY"):
            print(f"{st} {c['path']} {[(k['path'], k['n']) for k in cks.get(c['sig'], [])]} of {c['nblocks']}")
    if "--list-never" in sys.argv:
        print("== never run (priced at 230k/s) ==")
        for st, c, al in sorted(rows, key=lambda r: r[1]["path"]):
            if st == "NEVER":
                print(f"{c['hours']:7.2f} h  {c['path']}" + (f"  (same as {al})" if al else ""))


if __name__ == "__main__":
    main()
