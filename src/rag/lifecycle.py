"""Revision registry and immutable, atomically published retrieval generations.

SQLite is the authority for eligibility. Vector files are never the authority for withdrawal.
"""
from contextlib import contextmanager
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import uuid

import numpy as np

from .access import allowed

ROOT = Path(__file__).resolve().parents[2]


def hashed(value):
    return sha256(value).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


class Lifecycle:
    def __init__(self, root=None):
        self.root = Path(root or os.getenv("RAG_LIFECYCLE_ROOT") or ROOT / "var/lifecycle")
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "generations").mkdir(exist_ok=True)
        with self.db() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
            INSERT OR IGNORE INTO meta VALUES('active','');
            INSERT OR IGNORE INTO meta VALUES('epoch','0');
            CREATE TABLE IF NOT EXISTS revisions(source TEXT,revision TEXT,workspace TEXT,corpus TEXT,
              family TEXT,start TEXT,end TEXT,url TEXT,content TEXT,hash TEXT,status TEXT,checked TEXT,owner_workspace TEXT,
              PRIMARY KEY(source,revision));
            CREATE TABLE IF NOT EXISTS defaults(source TEXT PRIMARY KEY,revision TEXT);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,actor TEXT,action TEXT,source TEXT,detail TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS builds(id TEXT PRIMARY KEY,base TEXT,epoch INTEGER,status TEXT,detail TEXT,created TEXT);
            ''')

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.root / "registry.sqlite", timeout=30)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    @staticmethod
    def event(c, actor, action, source, detail):
        c.execute("INSERT INTO audit(actor,action,source,detail,created) VALUES(?,?,?,?,?)",
                  (actor, action, source, json.dumps(detail), now()))

    @staticmethod
    def bump(c):
        c.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='epoch'")

    def snapshot(self):
        with self.db() as c:
            c.execute("BEGIN")
            meta = dict(c.execute("SELECT key,value FROM meta"))
            rows = [dict(r) for r in c.execute("SELECT * FROM revisions")]
            defaults = dict(c.execute("SELECT source,revision FROM defaults"))
        return {"generation": meta["active"], "epoch": int(meta["epoch"]), "rows": rows, "defaults": defaults}

    @staticmethod
    def manage(row, user):
        if user.get("role") != "curator" or row.get("owner_workspace", row["workspace"]) != user.get("workspace"):
            raise PermissionError("Curator access to the owning workspace is required")

    def stage(self, source, text, user, *, corpus="technical", family=None, start=None, end=None, url=None, revision=None):
        if corpus not in ("technical", "policy") or not text.strip() or len(text) > 2_000_000:
            raise ValueError("Invalid corpus or source content")
        if corpus == "policy":
            if not family or not start:
                raise ValueError("Policy family and purchase-date start are required")
            date.fromisoformat(start)
            if end and date.fromisoformat(end) <= date.fromisoformat(start):
                raise ValueError("Policy end must be after start (exclusive)")
        digest = hashed(text.encode())
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            existing = c.execute("SELECT * FROM revisions WHERE source=? ORDER BY rowid DESC", (source,)).fetchone()
            if existing:
                self.manage(dict(existing), user)
                if existing["corpus"] != corpus or existing["family"] != family:
                    raise ValueError("Source corpus and family are immutable")
                if existing["hash"] == digest and existing["start"] == start and existing["end"] == end:
                    c.execute("UPDATE revisions SET checked=? WHERE source=? AND revision=?", (now(), source, existing["revision"]))
                    self.event(c, user["sub"], "refresh_unchanged", source, {})
                    return {"revision": existing["revision"], "changed": False}
            self.manage({"workspace": user["workspace"]}, user)
            rev = revision or ("r-" + uuid.uuid4().hex[:12])
            c.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (source, rev, existing["workspace"] if existing else user["workspace"], corpus, family, start, end, url, text, digest, "staged", now(), user["workspace"]))
            self.bump(c)
            self.event(c, user["sub"], "stage", source, {"revision": rev, "hash": digest})
        return {"revision": rev, "changed": True}

    def withdraw(self, source, revision, user):
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM revisions WHERE source=? AND revision=?", (source, revision)).fetchone()
            if row is None:
                raise PermissionError("Source unavailable")
            self.manage(dict(row), user)
            c.execute("UPDATE revisions SET status='withdrawn' WHERE source=? AND revision=?", (source, revision))
            self.bump(c)
            self.event(c, user["sub"], "withdraw", source, {"revision": revision})

    @staticmethod
    def check_overlap(rows):
        policies = [r for r in rows if r["corpus"] == "policy" and r["status"] == "published"]
        for i, a in enumerate(policies):
            for b in policies[i + 1:]:
                if (a["workspace"], a["family"]) == (b["workspace"], b["family"]) and a["start"] < (b["end"] or "9999-12-31") and b["start"] < (a["end"] or "9999-12-31"):
                    raise ValueError("Overlapping published policy purchase-date intervals")

    def build(self, source=None, revision=None, user=None, embed=None):
        """Build outside the registry transaction; publish uses generation + epoch CAS."""
        snap = self.snapshot()
        rows = [dict(r) for r in snap["rows"]]
        if source:
            target = next((r for r in rows if r["source"] == source and r["revision"] == revision), None)
            if not target:
                raise PermissionError("Source unavailable")
            self.manage(target, user)
            if target["status"] == "withdrawn":
                raise ValueError("Withdrawn revisions cannot be republished")
            target["status"] = "published"
        self.check_overlap(rows)
        generation = uuid.uuid4().hex
        directory = self.root / "generations" / generation
        directory.mkdir()
        with self.db() as c:
            c.execute("INSERT INTO builds VALUES(?,?,?,?,?,?)", (generation, snap["generation"], snap["epoch"], "building", "", now()))
        try:
            from .chunking import chunk_document
            chunks = []
            for r in rows:
                if r["status"] != "published":
                    continue
                for ordinal, chunk in enumerate(chunk_document(r["content"], r["source"])):
                    chunks.append({**chunk.to_dict(), "source_id": r["source"], "revision": r["revision"],
                                   "workspace": r["workspace"], "corpus": r["corpus"], "url": r["url"],
                                   "synthetic": r["corpus"] == "policy" or "SYNTHETIC" in r["content"][:100], "generation": generation,
                                   "chunk_uid": hashed(f'{r["source"]}:{r["revision"]}:{ordinal}:{chunk.text}'.encode()),
                                   "chunk_id": len(chunks)})
            if not chunks:
                raise ValueError("Cannot publish an empty generation")
            if embed is None:
                from .embeddings import embed_texts
                embed = embed_texts
            vectors = np.asarray(embed([r["text"] for r in chunks]), dtype="float32")
            if vectors.ndim != 2 or vectors.shape[0] != len(chunks):
                raise ValueError("Invalid embedding matrix")
            if snap["generation"]:
                prior = self.validate_generation(snap["generation"])
                if vectors.ndim != 2 or vectors.shape[1] != prior["dimension"]:
                    raise ValueError("Embedding dimension differs from the serving index")
            (directory / "chunks.json").write_text(json.dumps(chunks, ensure_ascii=False), encoding="utf-8")
            np.save(directory / "embeddings.npy", vectors)
            gate = ROOT / "research-gate.json"
            (directory / "gate.json").write_bytes(gate.read_bytes() if gate.exists() else b'{"threshold": 0}')
            manifest = {"generation": generation, "base_generation": snap["generation"], "registry_epoch": snap["epoch"],
                        "rows": len(chunks), "dimension": int(vectors.shape[1]), "created": now(),
                        "defaults": {**snap["defaults"], **({source: revision} if source else {})},
                        "benchmark": "Unscored generation; historical benchmark does not apply.",
                        "hashes": {f: hashed((directory / f).read_bytes()) for f in ("chunks.json", "embeddings.npy", "gate.json")}}
            (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            self.validate_generation(generation)
            with self.db() as c:
                c.execute("UPDATE builds SET status='ready',detail=? WHERE id=?", (json.dumps({"source": source, "revision": revision}), generation))
            return generation
        except Exception as exc:
            with self.db() as c:
                c.execute("UPDATE builds SET status='failed',detail=? WHERE id=?", (str(exc)[:400], generation))
            raise

    def validate_generation(self, generation):
        # Generation IDs are opaque UUID hex, never caller-controlled filesystem paths.
        if not re_generation(generation):
            raise ValueError("Invalid generation ID")
        folder = self.root / "generations" / generation
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        if manifest["generation"] != generation:
            raise ValueError("Manifest generation mismatch")
        for name in ("chunks.json", "embeddings.npy", "gate.json"):
            if hashed((folder / name).read_bytes()) != manifest["hashes"][name]:
                raise ValueError("Generation hash mismatch")
        chunks = json.loads((folder / "chunks.json").read_text(encoding="utf-8"))
        vectors = np.load(folder / "embeddings.npy", allow_pickle=False)
        if vectors.ndim != 2 or vectors.shape != (manifest["rows"], manifest["dimension"]) or not np.isfinite(vectors).all() or len(chunks) != len(vectors):
            raise ValueError("Invalid vector dimensions or row count")
        if len({c["chunk_uid"] for c in chunks}) != len(chunks) or [c["chunk_id"] for c in chunks] != list(range(len(chunks))):
            raise ValueError("Invalid chunk IDs")
        for chunk in chunks:
            if chunk["generation"] != generation or not all(chunk.get(k) for k in ("source_id", "revision", "workspace", "corpus", "text")):
                raise ValueError("Invalid chunk metadata")
        return manifest

    def publish(self, generation, user):
        self.validate_generation(generation)
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            b = c.execute("SELECT * FROM builds WHERE id=?", (generation,)).fetchone()
            meta = dict(c.execute("SELECT key,value FROM meta"))
            if not b or b["status"] != "ready":
                raise ValueError("Build is not ready")
            if b["base"] != meta["active"] or b["epoch"] != int(meta["epoch"]):
                raise ValueError("Stale build: rebuild from current registry state")
            detail = json.loads(b["detail"])
            if detail.get("source"):
                row = c.execute("SELECT * FROM revisions WHERE source=? AND revision=?", (detail["source"], detail["revision"])).fetchone()
                self.manage(dict(row), user)
                c.execute("UPDATE revisions SET status='published' WHERE source=? AND revision=?", (detail["source"], detail["revision"]))
                c.execute("INSERT OR REPLACE INTO defaults VALUES(?,?)", (detail["source"], detail["revision"]))
            elif user.get("role") != "curator":
                raise PermissionError("Curator required")
            self.check_overlap([dict(r) for r in c.execute("SELECT * FROM revisions")])
            c.execute("UPDATE meta SET value=? WHERE key='active'", (generation,))
            self.bump(c)
            c.execute("UPDATE builds SET status='published' WHERE id=?", (generation,))
            self.event(c, user["sub"], "publish", detail.get("source"), {"generation": generation})
        return {"generation": generation}

    def rollback(self, generation, user, expected_generation):
        manifest = self.validate_generation(generation)
        if "defaults" not in manifest:
            raise ValueError("Generation predates rollback metadata; rebuild it before rollback")
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            current = c.execute("SELECT value FROM meta WHERE key='active'").fetchone()[0]
            if current != expected_generation:
                raise ValueError("Active generation changed; retry rollback")
            rows = [dict(r) for r in c.execute("SELECT * FROM revisions")]
            # A workspace curator cannot move another workspace's serving pointer.
            for row in rows:
                self.manage(row, user)
            c.execute("UPDATE meta SET value=? WHERE key='active'", (generation,))
            for source, revision in manifest.get("defaults", {}).items():
                c.execute("INSERT OR REPLACE INTO defaults VALUES(?,?)", (source, revision))
            self.bump(c)
            self.event(c, user["sub"], "rollback", None, {"generation": generation})
        # Revision status is deliberately untouched: withdrawal survives rollback.
        return {"generation": generation}

    def eligible(self, chunks, user, *, corpus="technical", revision=None, purchase_date=None, source_id=None, snapshot=None):
        snap = snapshot or self.snapshot()
        if corpus not in ("technical", "policy"):
            raise ValueError("Unknown corpus")
        if purchase_date:
            date.fromisoformat(purchase_date)
        if corpus == "policy" and not purchase_date:
            return [], "needs_context", []
        registry = {(r["source"], r["revision"]): r for r in snap["rows"]}
        ids, reasons = [], {}
        conflict = False
        for i, chunk in enumerate(chunks):
            row = registry.get((chunk.get("source_id"), chunk.get("revision")))
            if not row or not allowed(chunk, user) or not allowed(row, user) or row["corpus"] != corpus or (source_id and row["source"] != source_id):
                continue
            reason = "eligible"
            if row["status"] != "published":
                reason = "withdrawn_or_unpublished"
            elif revision and row["revision"] != revision:
                reason = "different_revision"
            elif corpus == "policy" and not (row["start"] <= purchase_date < (row["end"] or "9999-12-31")):
                reason = "outside_purchase_date_interval"
                conflict |= bool(revision)
            elif corpus == "technical" and not revision and snap["defaults"].get(row["source"]) != row["revision"]:
                reason = "superseded_default"
            if reason == "eligible":
                ids.append(i)
            reasons[(row["source"], row["revision"])] = {"source_id": row["source"], "revision": row["revision"], "reason": reason}
        status = "context_conflict" if conflict else "ok" if ids else "insufficient_evidence"
        return ([] if conflict else ids), status, list(reasons.values())

    def validate_evidence(self, evidence, user, context=None):
        context = context or {}
        # Resolve each reference from its immutable generation. Never trust submitted text or ACL.
        canonical = []
        for ref in evidence:
            try:
                generation = ref["generation"]
                self.validate_generation(generation)
                folder = self.root / "generations" / generation
                rows = json.loads((folder / "chunks.json").read_text(encoding="utf-8"))
                chunk = next(c for c in rows if c["chunk_uid"] == ref["chunk_uid"])
                canonical.append(chunk)
            except (KeyError, ValueError, OSError, StopIteration):
                return {"valid": False, "status": "stale_evidence", "evidence": []}
        snap = self.snapshot()
        ids, status, _ = self.eligible(canonical, user, snapshot=snap, **context)
        valid = len(ids) == len(evidence) and status == "ok"
        return {"valid": valid, "status": "ok" if valid else "stale_evidence", "epoch": snap["epoch"], "evidence": canonical if valid else []}


def re_generation(value):
    return isinstance(value, str) and len(value) == 32 and all(c in "0123456789abcdef" for c in value)
