"""Server jar provisioning from the official distribution APIs.

Every endpoint below was verified live (see docs/research.md §2.1):

* PaperMC  -> ``fill.papermc.io/v3`` (the old v2 API is sunset)
* Purpur   -> ``api.purpurmc.org/v2``
* Vanilla  -> ``piston-meta.mojang.com`` manifest + per-version JSON
* Fabric   -> ``meta.fabricmc.net`` server jar endpoint

Downloads are verified against the SHA-256/SHA-1 published by the source; a
mismatch aborts the install instead of leaving a broken jar behind.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger("nautilus.provision")

UA = "nautilus-mc/0.1 (+https://github.com/flulwh/nautilus-mc)"
TIMEOUT = httpx.Timeout(30.0, read=120.0)
Progress = Callable[[int, int], Awaitable[None] | None]

KINDS = ["paper", "purpur", "vanilla", "fabric", "forge", "bedrock", "custom"]
MANUAL_KINDS = {"forge", "bedrock", "custom"}


class ProvisionError(RuntimeError):
    pass


@dataclass
class Artifact:
    """A downloadable server jar plus whatever checksum the source publishes."""

    url: str
    filename: str
    java: int = 21
    sha256: str = ""
    sha1: str = ""


async def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": UA})


async def paper_versions(project: str = "paper") -> list[str]:
    async with await _client() as c:
        r = await c.get(f"https://fill.papermc.io/v3/projects/{project}")
        r.raise_for_status()
        versions: dict[str, list[str]] = r.json().get("versions", {})
    flat: list[str] = []
    for group in versions.values():
        flat.extend(group)
    return flat


async def paper_artifact(version: str, project: str = "paper") -> Artifact:
    async with await _client() as c:
        r = await c.get(f"https://fill.papermc.io/v3/projects/{project}/versions/{version}/builds/latest")
        if r.status_code == 404:
            raise ProvisionError(f"Paper {version} 没有可用构建")
        r.raise_for_status()
        build = r.json()
        dl = build["downloads"]["server:default"]
        java_major = 21
        try:
            vmeta = await c.get(f"https://fill.papermc.io/v3/projects/{project}/versions/{version}")
            if vmeta.status_code == 200:
                java_major = int(vmeta.json()["version"]["java"]["version"]["minimum"])
        except Exception:  # pragma: no cover - version metadata is best effort
            pass
    return Artifact(dl["url"], dl["name"], java_major, sha256=dl["checksums"]["sha256"])


async def purpur_artifact(version: str) -> Artifact:
    async with await _client() as c:
        r = await c.get(f"https://api.purpurmc.org/v2/purpur/{version}/latest")
        if r.status_code == 404:
            raise ProvisionError(f"Purpur {version} 不存在")
        r.raise_for_status()
        build = r.json().get("build", "latest")
    url = f"https://api.purpurmc.org/v2/purpur/{version}/{build}/download"
    return Artifact(url, f"purpur-{version}-{build}.jar", 21)


async def vanilla_artifact(version: str) -> Artifact:
    async with await _client() as c:
        manifest = (await c.get("https://piston-meta.mojang.com/mc/game/version_manifest_v2.json")).json()
        entry = next((v for v in manifest["versions"] if v["id"] == version), None)
        if entry is None:
            raise ProvisionError(f"原版 {version} 不存在")
        meta = (await c.get(entry["url"])).json()
        server = meta.get("downloads", {}).get("server")
        if not server:
            raise ProvisionError(f"原版 {version} 没有服务端下载")
    # Mojang publishes SHA-1 for the server jar
    return Artifact(server["url"], f"vanilla-{version}.jar", 21, sha1=server.get("sha1", ""))


async def fabric_artifact(version: str, loader: str = "") -> Artifact:
    async with await _client() as c:
        if not loader:
            loaders = (await c.get(f"https://meta.fabricmc.net/v2/versions/loader/{version}")).json()
            stable = [x for x in loaders if x.get("loader", {}).get("stable")] or loaders
            if not stable:
                raise ProvisionError(f"Fabric {version} 没有可用 loader")
            loader = stable[0]["loader"]["version"]
        url = f"https://meta.fabricmc.net/v2/versions/loader/{version}/{loader}/server/jar"
        r = await c.head(url)
        if r.status_code >= 400:
            raise ProvisionError(f"Fabric 服务端 jar 不可用（{r.status_code}）")
    return Artifact(url, f"fabric-{version}-{loader}.jar", 21)


async def versions_for(kind: str) -> list[str]:
    """Version catalogue for a server kind, newest first."""
    if kind in {"paper", "purpur"}:
        return await paper_versions(kind)
    if kind == "vanilla":
        async with await _client() as c:
            manifest = (await c.get("https://piston-meta.mojang.com/mc/game/version_manifest_v2.json")).json()
        return [v["id"] for v in manifest["versions"] if v["type"] == "release"][:60]
    if kind == "fabric":
        async with await _client() as c:
            games = (await c.get("https://meta.fabricmc.net/v2/versions/game")).json()
        return [g["version"] for g in games if g.get("stable")][:60]
    return []


async def resolve(kind: str, version: str) -> Artifact:
    if kind == "paper":
        return await paper_artifact(version)
    if kind == "purpur":
        return await purpur_artifact(version)
    if kind == "vanilla":
        return await vanilla_artifact(version)
    if kind == "fabric":
        return await fabric_artifact(version)
    raise ProvisionError(f"{kind} 需要手动上传服务端 jar（无公开稳定分发 API）")


async def download(
    url: str,
    target: Path,
    *,
    expected_sha256: str = "",
    expected_sha1: str = "",
    progress: Progress | None = None,
) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    sha256 = hashlib.sha256()
    sha1 = hashlib.sha1()
    done = 0

    async with await _client() as c:
        async with c.stream("GET", url) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", "0") or 0)
            with open(tmp, "wb") as fh:
                async for chunk in r.aiter_bytes(256 * 1024):
                    fh.write(chunk)
                    sha256.update(chunk)
                    sha1.update(chunk)
                    done += len(chunk)
                    if progress:
                        res = progress(done, total)
                        if asyncio.iscoroutine(res):
                            await res

    if expected_sha256 and sha256.hexdigest().lower() != expected_sha256.lower():
        tmp.unlink(missing_ok=True)
        raise ProvisionError("SHA-256 校验失败，已丢弃下载文件")
    if expected_sha1 and sha1.hexdigest().lower() != expected_sha1.lower():
        tmp.unlink(missing_ok=True)
        raise ProvisionError("SHA-1 校验失败，已丢弃下载文件")

    target.unlink(missing_ok=True)
    tmp.replace(target)
    log.info("installed %s (%d bytes)", target, done)
    return target


def java_major_hint(version: str) -> int:
    """Best-effort mapping used when the provider does not tell us."""
    try:
        parts = [int(x) for x in version.split(".")[:2]]
    except ValueError:
        return 21
    if not parts:
        return 21
    if parts[0] >= 26:
        return 25
    minor = parts[1] if len(parts) > 1 else 0
    if parts[0] == 1 and minor <= 16:
        return 8
    if minor <= 17:
        return 16 if minor < 17 else 17
    if minor <= 20:
        return 17
    return 21
