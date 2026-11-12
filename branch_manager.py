"""Branch inventory and guarded remote deletion for Jembatan.

Safety signals come from Git ancestry or an exact-HEAD merged GitHub PR.
A branch is never deleted based on its name or a closed issue alone.
"""
import re

SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


def _pages(app, path, max_pages=10):
    """Fetch complete safety-critical GitHub lists; fail closed on truncation."""
    out = []
    for page in range(1, max_pages + 1):
        suffix = "&" if "?" in path else "?"
        values = app.gh(f"{path}{suffix}per_page=100&page={page}")
        if not isinstance(values, list):
            raise app.Err("Respons GitHub tidak valid.", 502)
        out.extend(values)
        if len(values) < 100:
            return out
    raise app.Err("Terlalu banyak data GitHub untuk memastikan keamanan branch. Tidak ada branch yang boleh dihapus.", 409)


def _local(app, repo):
    d = app.repo_dir(repo)
    if not (d / ".git").exists():
        raise app.Err("Repo belum disinkron. Centang lalu klik Sinkron.", 404)
    return d


def _same_repo_pr(pr, repo, branch):
    head = pr.get("head") or {}
    upstream = head.get("repo") or {}
    return upstream.get("full_name") == repo and head.get("ref") == branch


def inventory(app, repo, refresh=True):
    """Analyze refs; deletion permission is recomputed on the server later."""
    d = _local(app, repo)
    if refresh:
        app.git(d, "fetch", "origin", "--prune", net=True, timeout=300)
    meta = app.gh(f"/repos/{repo}")
    base = meta.get("default_branch")
    if not base:
        raise app.Err("Branch utama GitHub tidak diketahui.", 502)
    if not app.git(d, "rev-parse", "--verify", "-q",
                   f"refs/remotes/origin/{base}", check=False):
        raise app.Err(f"Branch utama {base} belum tersinkron; lakukan Sinkron lagi.", 409)

    protected_refs = {
        b["name"]: bool(b.get("protected"))
        for b in _pages(app, f"/repos/{repo}/branches")
    }
    open_prs = _pages(app, f"/repos/{repo}/pulls?state=open")
    # Scan recent closed PRs for merged-PR evidence; absence never grants safety.
    closed_prs = []
    for page in range(1, 6):
        items = app.gh(f"/repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=100&page={page}")
        if not isinstance(items, list):
            raise app.Err("Riwayat PR GitHub tidak valid.", 502)
        closed_prs.extend(items)
        if len(items) < 100:
            break

    # Git's for-each-ref format uses literal ASCII unit separator.
    fmt = "%(refname:short)\x1f%(objectname)\x1f%(committerdate:short)"
    all_refs = app.git(d, "for-each-ref", f"--format={fmt}",
                       "refs/remotes/origin").splitlines()
    records = []
    for line in all_refs:
        fields = line.split("\x1f", 2)
        if len(fields) != 3:
            continue
        ref, sha, date = fields
        if not ref.startswith("origin/") or ref == "origin/HEAD":
            continue
        branch = ref[len("origin/"):]
        if branch == base:
            continue
        if not SHA_RE.fullmatch(sha):
            continue

        behind, ahead = map(int, app.git(
            d, "rev-list", "--left-right", "--count",
            f"origin/{base}...{ref}").split())
        # Each remote ref that contains HEAD proves Git commit ancestry.
        containment = app.git(
            d, "for-each-ref", f"--contains={sha}",
            "--format=%(refname:short)", "refs/remotes/origin").splitlines()
        targets = {}
        for match in containment:
            if match.startswith("origin/") and match not in ("origin/HEAD", ref):
                tgt = match[len("origin/"):]
                targets[tgt] = {"name": tgt, "evidence": "ancestry", "pr": None}

        merged_here = [
            p for p in closed_prs
            if p.get("merged_at") and _same_repo_pr(p, repo, branch)
            and (p.get("head") or {}).get("sha") == sha
        ]
        for pr in merged_here:
            target = (pr.get("base") or {}).get("ref")
            if target and target not in targets:
                targets[target] = {
                    "name": target, "evidence": "merged_pr",
                    "pr": pr.get("number")
                }

        opening = [
            p.get("number") for p in open_prs
            if _same_repo_pr(p, repo, branch)
        ]
        has_main_ancestry = base in targets and targets[base]["evidence"] == "ancestry"
        has_main_pr = base in targets and targets[base]["evidence"] == "merged_pr"
        remote_protected = protected_refs.get(branch)
        if remote_protected is None:
            status, why = "blocked", "Status proteksi branch belum terverifikasi di GitHub."
        elif remote_protected:
            status, why = "blocked", "Branch dilindungi GitHub."
        elif opening:
            status, why = "blocked", f"Masih digunakan PR terbuka: {', '.join('#'+str(i) for i in opening)}."
        elif has_main_ancestry:
            status, why = "safe", f"Commit terakhir sudah menjadi bagian riwayat {base}."
        elif has_main_pr:
            number = targets[base]["pr"]
            status, why = "safe", f"PR #{number} untuk commit terakhir sudah di-merge ke {base} (termasuk squash/rebase)."
        elif targets:
            status, why = "review", f"Teridentifikasi pada branch lain, tetapi belum terbukti masuk ke {base}."
        else:
            status, why = "keep", f"Belum terbukti masuk ke {base}; masih mungkin berisi pekerjaan."
        records.append({
            "name": branch, "sha": sha, "date": date,
            "ahead": ahead, "behind": behind,
            "merged_into": sorted(targets.values(),
                                  key=lambda x: (x["name"] != base, x["name"])),
            "open_prs": opening, "protected": remote_protected,
            "status": status, "safe_to_delete": status == "safe",
            "reason": why
        })

    order = {"blocked": 0, "keep": 1, "review": 2, "safe": 3}
    records.sort(key=lambda row: (order[row["status"]], row["name"].lower()))
    return {"repo": repo, "base": base, "branches": records}


def delete_remote(app, repo, branch, expected_sha):
    """Delete only a verifiably integrated branch, with a Git lease on its SHA."""
    d = _local(app, repo)
    app.need_branch(branch)
    if not SHA_RE.fullmatch(expected_sha or ""):
        raise app.Err("SHA branch tidak valid.", 400)
    if not app.git(d, "check-ref-format", "--branch", branch, check=False):
        raise app.Err("Nama branch tidak valid.", 400)
    # Always fetch again; the preview may be stale.
    app.git(d, "fetch", "origin", "--prune", net=True, timeout=300)
    snapshot = inventory(app, repo, refresh=False)
    if branch == snapshot["base"]:
        raise app.Err("Branch utama tidak boleh dihapus.", 403)
    row = next((item for item in snapshot["branches"] if item["name"] == branch), None)
    if row is None:
        raise app.Err("Branch sudah tidak ada di GitHub.", 404)
    if row["sha"] != expected_sha:
        raise app.Err("Branch berubah sejak diperiksa. Muat ulang sebelum menghapus.", 409)
    if not row["safe_to_delete"]:
        raise app.Err("Penghapusan diblokir: " + row["reason"], 409)

    # --force-with-lease ensures the remote SHA has not changed between
    # the check and the push. Never force-remove an updated branch.
    lease = f"--force-with-lease=refs/heads/{branch}:{expected_sha}"
    app.git(d, "push", lease, "origin", f":refs/heads/{branch}",
            net=True, timeout=300)
    warning = None
    try:
        app.git(d, "fetch", "origin", "--prune", net=True, timeout=300)
    except app.Err:
        warning = "Branch terhapus di GitHub, tetapi cache lokal perlu disinkron ulang."
    return {"deleted": True, "name": branch, "sha": expected_sha,
            "merged_into": row["merged_into"], "warning": warning}
