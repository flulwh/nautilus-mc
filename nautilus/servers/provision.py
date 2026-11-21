"""Server jar provisioning from the official distribution APIs.

Every endpoint below was verified live (see docs/research.md §2.1):

* PaperMC   -> ``fill.papermc.io/v3`` (the old v2 API is sunset)
* Purpur    -> ``api.purpurmc.org/v2``
* Vanilla   -> ``piston-meta.mojang.com`` manifest + per-version JSON
* Fabric    -> ``meta.fabricmc.net`` server jar endpoint
* Forge     -> ``promotions_slim.json`` + ``maven.minecraftforge.net`` installer
* NeoForge  -> ``maven.neoforged.net`` maven metadata + installer

Forge and NeoForge do not publish a ready server jar: they publish an
*installer* that resolves libraries into ``libraries/`` and writes a small
bootstrap jar. That is exactly what ``itzg/mc-image-helper`` does in
production (``ForgeLikeInstaller``): run ``java -jar <installer> --installServer``
and then launch the bootstrap jar with ``-jar``. We follow the same contract so
the rest of the supervisor (stdin, RCON, stop ladder) needs no special case.

Downloads are verified against the SHA-256/SHA-1 published by the source; a
mismatch aborts the install instead of leaving a broken jar behind.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import aiofiles
import httpx

log = logging.getLogger("nautilus.provision")

UA = "nautilus-mc/0.1 (+https://github.com/flulwh/nautilus-mc)"
TIMEOUT = httpx.Timeout(30.0, read=120.0)
Progress = Callable[[int, int], Awaitable[None] | None]
LineSink = Callable[[str], None]

KINDS = ["paper", "purpur", "vanilla", "fabric", "forge", "neoforge", "bedrock", "custom"]
MANUAL_KINDS = {"bedrock", "custom"}

FORGE_PROMOS_URL = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
FORGE_MAVEN = "https://maven.minecraftforge.net"
NEOFORGE_MAVEN = "https://maven.neoforged.net/releases"

#: How long a Forge/NeoForge installer may run before it is killed.
INSTALL_TIMEOUT = 20 * 60


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
    #: Minecraft version this artifact targets (needed to name the entry jar).
    mc_version: str = ""
    #: Loader version (Forge build, NeoForge build, Fabric loader).
    loader_version: str = ""
    #: True when ``filename`` is an installer that must be executed first.
    install: bool = False
    #: Bootstrap jars the installer may leave behind, most likely first.
    entry: tuple[str, ...] = field(default_factory=tuple)


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
    return Artifact(url, f"fabric-{version}-{loader}.jar", 21, mc_version=version, loader_version=loader)


# --------------------------------------------------------------------- forge


def _verkey(value: str) -> tuple:
    """Sort key that orders ``1.20.4`` / ``21.1.72`` numerically, "26.3" too."""
    return tuple(int(part) if part.isdigit() else -1 for part in re.split(r"[.\-+]", value))


async def forge_promotions() -> dict[str, str]:
    async with await _client() as c:
        r = await c.get(FORGE_PROMOS_URL)
        r.raise_for_status()
        return r.json().get("promos", {})


async def forge_versions() -> list[str]:
    """Minecraft versions Forge actually ships a build for."""
    promos = await forge_promotions()
    versions = {key.rsplit("-", 1)[0] for key in promos}
    return sorted(versions, key=_verkey, reverse=True)


async def forge_artifact(version: str, loader: str = "") -> Artifact:
    """Resolve the Forge installer for one Minecraft version.

    Version qualifiers changed three times over the years, so the maven path is
    probed in the same order ``ForgeInstallerResolver`` uses upstream.
    """
    promos = await forge_promotions()
    build = loader.strip()
    if build.lower() in {"", "recommended", "latest"}:
        build = promos.get(f"{version}-{build.lower() or 'recommended'}") or promos.get(f"{version}-latest") or ""
    if not build:
        raise ProvisionError(f"Forge {version} 没有可用构建（该版本可能只支持 NeoForge）")

    async with await _client() as c:
        for combo in (f"{version}-{build}", f"{version}-{build}-{version}", f"{version}-{build}-mc172"):
            url = f"{FORGE_MAVEN}/net/minecraftforge/forge/{combo}/forge-{combo}-installer.jar"
            r = await c.head(url)
            if r.status_code == 200 and int(r.headers.get("content-length", "0") or 0) > 100_000:
                return Artifact(
                    url,
                    f"forge-{combo}-installer.jar",
                    java_major_hint(version),
                    mc_version=version,
                    loader_version=build,
                    install=True,
                    entry=(
                        f"forge-{version}-{build}.jar",
                        f"forge-{version}-{build}-shim.jar",
                        f"forge-{version}-{build}-universal.jar",
                        f"forge-{version}-{build}-{version}.jar",
                    ),
                )
    raise ProvisionError(f"Forge {version}-{build} 安装器不存在")


async def neoforge_metadata() -> list[str]:
    async with await _client() as c:
        r = await c.get(f"{NEOFORGE_MAVEN}/net/neoforged/neoforge/maven-metadata.xml")
        r.raise_for_status()
    try:
        root = ET.fromstring(r.text)
    except ET.ParseError as exc:
        raise ProvisionError(f"NeoForge 元数据解析失败：{exc}") from exc
    return [node.text or "" for node in root.iter("version") if node.text]


def neoforge_mc(loader: str) -> str:
    """``21.1.72`` -> ``1.21.1`` (NeoForge encodes the MC version in its own)."""
    parts = loader.split(".")
    if len(parts) < 2:
        return ""
    return f"1.{parts[0]}.{parts[1]}"


async def neoforge_versions() -> list[str]:
    versions = {neoforge_mc(v) for v in await neoforge_metadata()}
    versions.discard("")
    return sorted(versions, key=_verkey, reverse=True)


async def neoforge_artifact(version: str, loader: str = "") -> Artifact:
    builds = await neoforge_metadata()
    prefix = version[2:] if version.startswith("1.") else version
    candidates = [v for v in builds if v.startswith(prefix + ".")]
    if version == "1.20.1":
        # NeoForge 1.20.1 lived under ``net.neoforged:forge`` before the split.
        legacy = [v for v in builds if v.startswith("1.20.1-")]
        if legacy:
            raise ProvisionError("NeoForge 1.20.1 使用旧坐标，请手动上传安装器")
    if not candidates:
        raise ProvisionError(f"NeoForge {version} 没有可用构建")
    build = loader.strip()
    if build.lower() in {"", "latest", "recommended"}:
        build = max(candidates, key=_verkey)
    elif build not in candidates:
        raise ProvisionError(f"NeoForge {version} 没有构建 {build}")

    async with await _client() as c:
        url = f"{NEOFORGE_MAVEN}/net/neoforged/neoforge/{build}/neoforge-{build}-installer.jar"
        r = await c.head(url)
        if r.status_code >= 400:
            raise ProvisionError(f"NeoForge {build} 安装器不存在（{r.status_code}）")
    return Artifact(
        url,
        f"neoforge-{build}-installer.jar",
        java_major_hint(version),
        mc_version=version,
        loader_version=build,
        install=True,
        entry=(
            f"neoforge-{build}.jar",
            f"neoforge-{build}-shim.jar",
            f"neoforge-{build}-universal.jar",
        ),
    )


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
    if kind == "forge":
        return (await forge_versions())[:80]
    if kind == "neoforge":
        return (await neoforge_versions())[:80]
    return []


async def resolve(kind: str, version: str, loader: str = "") -> Artifact:
    if kind == "paper":
        return await paper_artifact(version)
    if kind == "purpur":
        return await purpur_artifact(version)
    if kind == "vanilla":
        return await vanilla_artifact(version)
    if kind == "fabric":
        return await fabric_artifact(version, loader)
    if kind == "forge":
        return await forge_artifact(version, loader)
    if kind == "neoforge":
        return await neoforge_artifact(version, loader)
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
    tmp = target.with_name(f"{target.name}.{os.getpid()}.{time.time_ns()}.part")
    sha256 = hashlib.sha256()
    sha1 = hashlib.sha1()
    done = 0
    total = 0

    try:
        async with await _client() as c:
            async with c.stream("GET", url) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", "0") or 0)
                async with aiofiles.open(tmp, "wb") as fh:
                    async for chunk in r.aiter_bytes(256 * 1024):
                        await fh.write(chunk)
                        sha256.update(chunk)
                        sha1.update(chunk)
                        done += len(chunk)
                        if progress:
                            res = progress(done, total)
                            if asyncio.iscoroutine(res):
                                await res

        # A dropped connection can end the stream early without raising; the
        # content-length is the only signal for sources that publish no hash.
        if total and done != total:
            raise ProvisionError(f"下载不完整（{done}/{total} 字节），已丢弃")

        if expected_sha256 and sha256.hexdigest().lower() != expected_sha256.lower():
            raise ProvisionError("SHA-256 校验失败，已丢弃下载文件")
        if expected_sha1 and sha1.hexdigest().lower() != expected_sha1.lower():
            raise ProvisionError("SHA-1 校验失败，已丢弃下载文件")
    except Exception:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise

    try:
        os.replace(tmp, target)
    except OSError as exc:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise ProvisionError(f"无法写入 {target.name}（{exc}）。如果服务器正在运行，请先停止再更新。") from exc
    log.info("installed %s (%d bytes)", target, done)
    return target


def _java_proxy_args() -> list[str]:
    """Translate the panel's proxy environment into JVM flags.

    ``httpx`` honours ``HTTPS_PROXY`` automatically, but a ``java -jar`` child
    process does not: behind a corporate proxy the Forge installer then dies
    with "Remote host terminated the handshake", which looks like a broken
    download rather than a missing proxy setting.
    """
    raw = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
        or ""
    ).strip()
    if not raw:
        return []
    from urllib.parse import unquote, urlparse

    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    if not parsed.hostname:
        return []
    port = parsed.port or 8080
    args = [
        f"-Dhttp.proxyHost={parsed.hostname}",
        f"-Dhttp.proxyPort={port}",
        f"-Dhttps.proxyHost={parsed.hostname}",
        f"-Dhttps.proxyPort={port}",
        "-Dhttp.nonProxyHosts=localhost|127.0.0.1",
    ]
    if parsed.username:
        args.append(f"-Dhttp.proxyUser={unquote(parsed.username)}")
        if parsed.password:
            args.append(f"-Dhttp.proxyPassword={unquote(parsed.password)}")
    return args


def _installer_excerpt(directory: Path, installer: Path, lines: int = 6) -> str:
    log_path = directory / f"{installer.name}.log"
    if not log_path.exists():
        return ""
    try:
        tail = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-lines:]
    except OSError:
        return ""
    return "\n".join(tail)


async def _run_installer(java: str, directory: Path, installer: Path, on_line: LineSink | None = None) -> None:
    """Execute a Forge/NeoForge installer and stream its output.

    The installer resolves every library into ``libraries/`` and writes the
    bootstrap jar; it prints hundreds of lines and takes minutes on a cold
    cache, so the caller gets the lines live instead of a silent 5-minute hang.
    """
    argv = [java, *_java_proxy_args(), "-jar", installer.name, "--installServer"]
    log.info("running installer: %s (cwd=%s)", " ".join(argv), directory)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(directory),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            limit=1024 * 1024,
        )
    except FileNotFoundError as exc:
        raise ProvisionError(f"找不到 Java 可执行文件：{java}") from exc

    deadline = time.monotonic() + INSTALL_TIMEOUT
    assert proc.stdout is not None
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProvisionError(f"安装器超过 {INSTALL_TIMEOUT // 60} 分钟未完成，已终止")
            try:
                raw = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
            except asyncio.TimeoutError:
                raise ProvisionError(f"安装器超过 {INSTALL_TIMEOUT // 60} 分钟未完成，已终止") from None
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip()
            if on_line:
                on_line(line)
    finally:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        code = await proc.wait()

    if code != 0:
        excerpt = _installer_excerpt(directory, installer)
        detail = f"\n{excerpt}" if excerpt else ""
        raise ProvisionError(f"安装器退出码 {code}（常见原因：Java 版本过低、网络中断或代理未生效）{detail}")


async def install(
    server,
    kind: str,
    version: str,
    *,
    loader: str = "",
    progress: Progress | None = None,
    on_line: LineSink | None = None,
) -> str:
    """Provision a server directory and return the jar the supervisor should run.

    For direct downloads that is the downloaded file; for Forge/NeoForge it is
    the bootstrap jar produced by the installer (``forge-<mc>-<build>-shim.jar``
    and friends), which is what ``java -jar`` expects on every platform.
    """
    from ..settings import settings

    artifact = await resolve(kind, version, loader)
    directory = server.directory
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / artifact.filename
    await download(
        artifact.url,
        target,
        expected_sha256=artifact.sha256,
        expected_sha1=artifact.sha1,
        progress=progress,
    )
    if not artifact.install:
        return artifact.filename

    java = getattr(server, "java", "") or settings.java
    await _run_installer(java, directory, target, on_line)

    for name in artifact.entry:
        if (directory / name).is_file():
            log.info("%s installer produced %s", kind, name)
            return name

    # Version naming drifted across Forge releases; fall back to a scan but
    # never pick the installer itself.
    for pattern in ("forge-*-shim.jar", "forge-*.jar", "neoforge-*-shim.jar", "neoforge-*.jar"):
        found = [p.name for p in sorted(directory.glob(pattern)) if "installer" not in p.name]
        if found:
            log.info("%s installer produced %s (glob fallback)", kind, found[0])
            return found[0]
    excerpt = _installer_excerpt(directory, target)
    raise ProvisionError("安装器执行完毕但没有生成服务端 jar，请查看控制台输出" + (f"\n{excerpt}" if excerpt else ""))


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
