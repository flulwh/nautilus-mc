"""Plugin / mod management backed by two public market APIs.

* **Modrinth v2**  - widest coverage, returns SHA-1 + SHA-512 per file.
* **Hangar v1**    - PaperMC's official registry.

Both are queried through a TTL cache to respect their rate limits, and every
download is hash-verified before it is placed in ``plugins/`` or ``mods/``.
Installed jars are inspected locally (no network) by reading ``plugin.yml`` /
``fabric.mod.json`` / ``mods.toml`` out of the archive.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import tomllib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from cachetools import TTLCache

from ..models import Server

log = logging.getLogger("nautilus.plugins")

UA = "nautilus-mc/0.1 (+https://github.com/flulwh/nautilus-mc)"
MODRINTH = "https://api.modrinth.com/v2"
HANGAR = "https://hangar.papermc.io/api/v1"

_cache: TTLCache = TTLCache(maxsize=256, ttl=300)
_client: httpx.AsyncClient | None = None


class PluginError(RuntimeError):
    pass


@dataclass
class Artifact:
    url: str
    filename: str
    version: str = ""
    project_id: str = ""
    name: str = ""
    sha1: str = ""
    sha512: str = ""
    sha256: str = ""
    source: str = ""
    page: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, read=180.0),
            headers={"User-Agent": UA, "Accept": "application/json"},
            follow_redirects=True,
        )
    return _client


async def close() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
    _client = None


async def _cached_get(url: str, params: dict | None = None) -> Any:
    key = url + "|" + json.dumps(params or {}, sort_keys=True, default=str)
    if key in _cache:
        return _cache[key]
    r = await _http().get(url, params=params)
    if r.status_code == 429:
        raise PluginError("插件源限流，请稍后重试")
    r.raise_for_status()
    data = r.json()
    _cache[key] = data
    return data


def loader_for(server: Server) -> str:
    """Modrinth *category* name for the server's content loader.

    Vanilla has no loader at all — it only accepts datapacks — so it must not
    be filtered by any category (``datapack`` is a project type, not a
    category; using it as a facet silently returned zero results).
    """
    return {
        "paper": "paper",
        "purpur": "purpur",
        "vanilla": "",
        "fabric": "fabric",
        "forge": "forge",
        "neoforge": "neoforge",
        "bedrock": "",
        "custom": "bukkit",
    }.get(server.kind, "paper")


def project_type_for(server: Server) -> str:
    if server.kind == "vanilla":
        return "datapack"
    return "mod" if server.kind in {"fabric", "forge", "neoforge"} else "plugin"


def content_dir(server: Server) -> Path:
    """Where downloaded content belongs for this server kind.

    Vanilla has no plugin loader at all: its only server-side extension point
    is the datapack, which lives inside the world folder.
    """
    if server.kind in {"fabric", "forge", "neoforge"}:
        return server.directory / "mods"
    if server.kind == "vanilla":
        # Datapacks live inside the *active* world folder, which is a property
        # (`level-name`), not always "world".
        from .backups import level_name

        return server.directory / level_name(server) / "datapacks"
    return server.directory / "plugins"


def _require_compatible_source(server: Server | None, source: str) -> None:
    """Hangar only ships Paper-family plugins; fail loudly instead of empty."""
    if source == "hangar" and server is not None and server.kind in {"vanilla", "bedrock"}:
        raise PluginError(
            "Hangar 只提供 Paper 系插件，当前服务端不兼容；请在 Modrinth 中搜索"
            + ("数据包" if server.kind == "vanilla" else "可用内容")
        )


_PROJECT_RE = re.compile(r"^[A-Za-z0-9._/-]{1,120}$")


def _project_id(project: str) -> str:
    """Validate a project slug before it is interpolated into a URL path.

    Slugs are user input; ``../`` or ``?`` would rewrite the upstream request
    (path traversal / query injection against the market API).
    """
    value = (project or "").strip()
    if not _PROJECT_RE.match(value) or ".." in value.split("/"):
        raise PluginError("非法的项目标识")
    return value


# ------------------------------------------------------------------- search


async def search(query: str, *, server: Server | None = None, source: str = "modrinth", limit: int = 20, offset: int = 0) -> list[dict]:
    _require_compatible_source(server, source)
    if source == "hangar":
        data = await _cached_get(f"{HANGAR}/projects", {"q": query, "limit": limit, "offset": offset})
        out = []
        for p in data.get("result", []):
            ns = p.get("namespace", {})
            slug = f"{ns.get('owner')}/{ns.get('slug')}"
            out.append(
                {
                    "source": "hangar",
                    "id": slug,
                    "slug": slug,
                    "name": p.get("name"),
                    "description": p.get("description"),
                    "downloads": p.get("stats", {}).get("downloads", 0),
                    "stars": p.get("stats", {}).get("stars", 0),
                    "author": ns.get("owner"),
                    "icon": None,
                    "page": f"https://hangar.papermc.io/{slug}",
                }
            )
        return out

    facets: list[list[str]] = []
    if server is not None:
        facets.append([f"project_type:{project_type_for(server)}"])
        loader = loader_for(server)
        if loader:
            facets.append([f"categories:{loader}"])
    params: dict[str, Any] = {"query": query, "limit": limit, "offset": offset}
    if facets:
        params["facets"] = json.dumps(facets)
    data = await _cached_get(f"{MODRINTH}/search", params)
    return [
        {
            "source": "modrinth",
            "id": h["project_id"],
            "slug": h.get("slug"),
            "name": h.get("title"),
            "description": h.get("description"),
            "downloads": h.get("downloads", 0),
            "author": h.get("author"),
            "icon": h.get("icon_url"),
            "categories": h.get("categories", []),
            "page": f"https://modrinth.com/project/{h.get('slug')}",
        }
        for h in data.get("hits", [])
    ]


# ----------------------------------------------------------------- versions


async def versions(project: str, *, server: Server, source: str = "modrinth", limit: int = 12) -> list[dict]:
    _require_compatible_source(server, source)
    project = _project_id(project)
    if source == "hangar":
        platform = {"paper": "PAPER", "purpur": "PAPER"}.get(server.kind, "PAPER")
        data = await _cached_get(f"{HANGAR}/projects/{project}/versions", {"limit": limit, "platform": platform})
        out = []
        for v in data.get("result", []):
            dl = v.get("downloads", {}).get(platform) or {}
            info = dl.get("fileInfo") or {}
            out.append(
                {
                    "id": str(v.get("name")),
                    "name": v.get("name"),
                    "channel": (v.get("channel") or {}).get("name", ""),
                    "downloads": v.get("stats", {}).get("totalDownloads", 0),
                    "date": v.get("createdAt"),
                    "filename": info.get("name", ""),
                    "installable": bool(dl.get("downloadUrl")),
                }
            )
        return out

    params: dict[str, Any] = {}
    loader = loader_for(server)
    if loader and server.kind not in {"vanilla"}:
        params["loaders"] = json.dumps([loader])
    if server.version:
        params["game_versions"] = json.dumps([server.version])
    data = await _cached_get(f"{MODRINTH}/project/{project}/version", params or None)
    out = []
    for v in data[:limit]:
        files = v.get("files", [])
        primary = next((f for f in files if f.get("primary")), files[0] if files else {})
        out.append(
            {
                "id": v["id"],
                "name": v.get("version_number"),
                "channel": v.get("version_type", "release"),
                "downloads": v.get("downloads", 0),
                "date": v.get("date_published"),
                "filename": primary.get("filename", ""),
                "installable": bool(primary.get("url")),
            }
        )
    return out


async def resolve(project: str, *, server: Server, source: str = "modrinth", version_id: str = "") -> Artifact:
    _require_compatible_source(server, source)
    project = _project_id(project)
    if source == "hangar":
        platform = "PAPER"
        versions_list = await versions(project, server=server, source="hangar", limit=25)
        chosen = next((v for v in versions_list if v["id"] == version_id), versions_list[0] if versions_list else None)
        if not chosen:
            raise PluginError("找不到可用版本")
        data = await _cached_get(f"{HANGAR}/projects/{project}/versions", {"limit": 25, "platform": platform})
        entry = next((x for x in data.get("result", []) if str(x.get("name")) == chosen["id"]), None)
        if not entry:
            raise PluginError("版本信息已变化，请重新搜索")
        dl = (entry.get("downloads") or {}).get(platform) or {}
        info = dl.get("fileInfo") or {}
        url = dl.get("downloadUrl")
        if not url:
            raise PluginError("该版本仅提供外部下载（GitHub Releases），面板无法直接安装")
        return Artifact(
            url=url,
            filename=info.get("name") or f"{project.split('/')[-1]}-{chosen['id']}.jar",
            version=str(chosen["id"]),
            project_id=project,
            name=project.split("/")[-1],
            sha256=info.get("sha256Hash", "") or "",
            source="hangar",
            page=f"https://hangar.papermc.io/{project}",
        )

    data = await _cached_get(f"{MODRINTH}/project/{project}/version")
    entry = None
    if version_id:
        entry = next((v for v in data if v["id"] == version_id), None)
    if entry is None:
        loader = loader_for(server)
        candidates = [
            v
            for v in data
            if (not server.version or server.version in v.get("game_versions", []))
            and (not loader or loader in v.get("loaders", []) or server.kind == "vanilla")
        ]
        entry = candidates[0] if candidates else (data[0] if data else None)
    if entry is None:
        raise PluginError("该项目没有可用版本")
    files = entry.get("files", [])
    primary = next((f for f in files if f.get("primary")), files[0] if files else None)
    if not primary:
        raise PluginError("该版本没有可下载文件")
    hashes = primary.get("hashes", {})
    return Artifact(
        url=primary["url"],
        filename=primary["filename"],
        version=entry.get("version_number", ""),
        project_id=project,
        name=project,
        sha1=hashes.get("sha1", ""),
        sha512=hashes.get("sha512", ""),
        source="modrinth",
        page=f"https://modrinth.com/project/{project}",
    )


# ------------------------------------------------------------------ install


async def _download_verified(artifact: Artifact, target: Path) -> Path:
    from .provision import download

    if not (artifact.sha1 or artifact.sha512 or artifact.sha256):
        raise PluginError("下载源未提供哈希，拒绝安装（防投毒）")
    return await download(
        artifact.url,
        target,
        expected_sha256=artifact.sha256,
        expected_sha1=artifact.sha1,
        progress=None,
    )


async def install(server: Server, project: str, *, source: str = "modrinth", version_id: str = "") -> dict:
    artifact = await resolve(project, server=server, source=source, version_id=version_id)
    folder = content_dir(server)
    folder.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._+\-]", "_", Path(artifact.filename).name)
    target = folder / safe_name
    await _download_verified(artifact, target)

    from ..db import session_scope
    from ..models import PluginRecord

    async with session_scope() as s:
        s.add(
            PluginRecord(
                server_id=server.id,
                provider=source,
                project_id=artifact.project_id,
                slug=project,
                name=artifact.name or project,
                version=artifact.version,
                filename=safe_name,
            )
        )
    log.info("installed %s %s into server %s", project, artifact.version, server.id)
    return {
        "filename": safe_name,
        "version": artifact.version,
        "source": source,
        "page": artifact.page,
        "size": target.stat().st_size,
    }


# ------------------------------------------------------------------ inspect


def _read_manifest(jar: Path) -> dict:
    info: dict[str, Any] = {"name": jar.stem, "version": "", "main": "", "api": "", "authors": []}
    try:
        with zipfile.ZipFile(jar) as zf:
            names = set(zf.namelist())
            if "plugin.yml" in names:
                from ruamel.yaml import YAML

                data = YAML(typ="safe").load(zf.read("plugin.yml").decode("utf-8", "replace")) or {}
                info.update(
                    name=data.get("name", jar.stem),
                    version=str(data.get("version", "")),
                    main=data.get("main", ""),
                    api=str(data.get("api-version", "")),
                    authors=[data["author"]] if isinstance(data.get("author"), str) else data.get("authors", []),
                )
            elif "paper-plugin.yml" in names:
                from ruamel.yaml import YAML

                data = YAML(typ="safe").load(zf.read("paper-plugin.yml").decode("utf-8", "replace")) or {}
                info.update(name=data.get("name", jar.stem), version=str(data.get("version", "")), main=data.get("main", ""))
            elif "fabric.mod.json" in names:
                data = json.loads(zf.read("fabric.mod.json").decode("utf-8", "replace"))
                info.update(
                    name=data.get("name", jar.stem),
                    version=str(data.get("version", "")),
                    main=(data.get("entrypoints", {}).get("main") or [""])[0] if data.get("entrypoints") else "",
                    authors=[a.get("name", "") for a in data.get("authors", []) if isinstance(a, dict)],
                )
            elif "META-INF/mods.toml" in names:
                data = tomllib.loads(zf.read("META-INF/mods.toml").decode("utf-8", "replace"))
                mods = data.get("mods", [{}])
                info.update(
                    name=mods[0].get("displayName", jar.stem),
                    version=str(mods[0].get("version", "")),
                    main=mods[0].get("modId", ""),
                )
    except (zipfile.BadZipFile, OSError, ValueError, KeyError):
        info["broken"] = True
    return info


def installed(server: Server) -> list[dict]:
    folder = content_dir(server)
    if not folder.exists():
        return []
    out = []
    for jar in sorted(folder.iterdir()):
        if not jar.is_file():
            continue
        enabled = jar.suffix == ".jar"
        if jar.name.endswith(".jar.disabled"):
            enabled = False
        elif not enabled:
            continue
        meta = _read_manifest(jar) if jar.suffix == ".jar" else {}
        out.append(
            {
                "filename": jar.name,
                "size": jar.stat().st_size,
                "mtime": jar.stat().st_mtime,
                "enabled": enabled,
                **{k: v for k, v in meta.items() if k in {"name", "version", "main", "api", "broken"}},
            }
        )
    return out


def toggle(server: Server, filename: str, enabled: bool) -> str:
    folder = content_dir(server)
    src = _safe(folder, filename)
    if enabled and src.name.endswith(".jar.disabled"):
        dst = src.with_name(src.name[: -len(".disabled")])
    elif not enabled and src.suffix == ".jar":
        dst = src.with_name(src.name + ".disabled")
    else:
        return src.name
    src.replace(dst)
    return dst.name


def remove(server: Server, filename: str) -> None:
    path = _safe(content_dir(server), filename)
    path.unlink(missing_ok=True)


def _safe(root: Path, filename: str) -> Path:
    path = (root / filename).resolve()
    if not path.is_relative_to(root.resolve()):
        raise PluginError("非法文件名")
    return path


def _version_key(value: str) -> tuple:
    """Order versions numerically, not lexicographically ("1.10" > "1.9")."""
    return tuple((0, int(p)) if p.isdigit() else (1, p.lower()) for p in re.split(r"[.\-+_ ]", str(value)) if p)


async def check_updates(server: Server) -> list[dict]:
    """Compare installed jar versions with the latest from Modrinth (best effort)."""

    async def one(item: dict) -> dict | None:
        if not item.get("name") or not item.get("version"):
            return None
        try:
            found = await search(item["name"], server=server, limit=1)
        except Exception:
            return None
        if not found:
            return None
        try:
            latest = await versions(found[0]["id"], server=server, limit=1)
        except Exception:
            return None
        if not latest:
            return None
        return {
            "filename": item["filename"],
            "installed": item["version"],
            "latest": latest[0]["name"],
            "updatable": _version_key(latest[0]["name"]) > _version_key(item["version"]),
            "project": found[0]["id"],
        }

    results = await asyncio.gather(*(one(i) for i in installed(server)[:20]))
    return [r for r in results if r and r["updatable"]]
