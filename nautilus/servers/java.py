"""Java runtime discovery and provisioning.

Minecraft versions demand specific Java majors (1.16 → 8, 1.17 → 16, 1.18–1.20
→ 17, 1.20.5+ → 21), and asking the operator to install five JDKs by hand is the
single most common reason a freshly created server refuses to boot. Every
mature panel solves this the same way: enumerate what is installed, and fetch
the missing major from Adoptium (Eclipse Temurin), the JRE the Minecraft
ecosystem standardised on.

* discovery runs ``java -XshowSettings:properties -version`` and parses the
  property block — that is the only output format stable across JDK 8 → 25.
* downloads use ``/v3/assets/latest/{feature}/hotspot`` which returns the
  archive link *and* its SHA-256, so a corrupted download is rejected before
  extraction (same contract as the server-jar installer).
* managed runtimes live in ``<data>/jre/<major>-<version>``. The version is in
  the directory name so upgrading never requires deleting a runtime that a
  server is currently using.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import platform
import re
import shutil
import tarfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from ..settings import settings

log = logging.getLogger("nautilus.java")

ADOPTIUM = "https://api.adoptium.net/v3"
TUNA = "https://mirrors.tuna.tsinghua.edu.cn/Adoptium"
UA = "nautilus-mc/0.1 (+https://github.com/flulwh/nautilus-mc)"
#: Majors offered in the UI. 25 is what the 26.x server series needs.
MAJORS = [8, 11, 17, 21, 25]

class JavaError(RuntimeError):
    pass


@dataclass
class JavaRuntime:
    major: int
    version: str
    vendor: str
    path: str
    source: str  # "system" | "managed"
    arch: str = ""
    size: int = 0

    def public(self) -> dict:
        return asdict(self)


def _exe() -> str:
    return "java.exe" if os.name == "nt" else "java"


def _major_of(version: str) -> int:
    """``1.8.0_402`` → 8, ``21.0.13`` → 21, ``25-ea`` → 25."""
    parts = re.split(r"[._\-+]", version.strip())
    if not parts or not parts[0].isdigit():
        return 0
    if parts[0] == "1" and len(parts) > 1 and parts[1].isdigit():
        return int(parts[1])
    return int(parts[0])


async def _probe(path: str) -> JavaRuntime | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            path,
            "-XshowSettings:properties",
            "-version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except (OSError, ValueError):
        return None
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=12)
    except asyncio.TimeoutError:
        proc.kill()
        return None
    text = out.decode("utf-8", errors="replace")

    def prop(name: str) -> str:
        m = re.search(rf"^\s*{name}\s*=\s*(.+)$", text, re.MULTILINE)
        return m.group(1).strip() if m else ""

    version = prop("java.version")
    if not version:
        return None
    return JavaRuntime(
        major=_major_of(version),
        version=version,
        vendor=prop("java.vendor"),
        path=str(Path(path).resolve()),
        source="managed" if str(settings.data_dir) in str(Path(path).resolve()) else "system",
        arch=prop("os.arch") or platform.machine(),
    )


def _candidates() -> list[str]:
    found: list[str] = []
    if settings.java:
        # "java" is a PATH lookup, not a file next to the panel; resolving it
        # first is what lets the duplicate check below actually collapse the
        # PATH entry and the JAVA_HOME entry into one runtime.
        found.append(shutil.which(settings.java) or settings.java)
    home = os.environ.get("JAVA_HOME", "").strip()
    if home:
        found.append(str(Path(home) / "bin" / _exe()))
    for base in (settings.data_dir / "jre",):
        if base.is_dir():
            for child in sorted(base.iterdir(), reverse=True):
                if child.suffix == ".unpack" or not child.is_dir():
                    continue
                candidate = _find_java(child)
                if candidate is not None:
                    found.append(str(candidate))
    resolved = shutil.which(_exe())
    if resolved:
        found.append(resolved)
    return found


async def detect(*, refresh: bool = False) -> list[JavaRuntime]:
    global _cache
    if _cache and not refresh:
        return _cache
    seen: set[str] = set()
    out: list[JavaRuntime] = []
    for path in _candidates():
        try:
            key = str(Path(path).resolve())
        except OSError:
            key = path
        if key in seen:
            continue
        info = await _probe(path)
        if info is None or not info.major:
            continue
        # Same JDK reached through PATH / JAVA_HOME / a managed path.
        if info.path in seen:
            continue
        seen.add(key)
        seen.add(info.path)
        out.append(info)
    out.sort(key=lambda r: (-r.major, r.source != "managed"))
    _cache = out
    return out


_cache: list[JavaRuntime] | None = None


def invalidate() -> None:
    global _cache
    _cache = None


def managed_root() -> Path:
    root = settings.data_dir / "jre"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _platform() -> tuple[str, str]:
    os_name = {"nt": "windows", "posix": "linux"}.get(os.name, "linux")
    if platform.system() == "Darwin":
        os_name = "mac"
    machine = platform.machine().lower()
    arch = {
        "x86_64": "x64",
        "amd64": "x64",
        "aarch64": "aarch64",
        "arm64": "aarch64",
    }.get(machine, machine or "x64")
    return os_name, arch


async def _mirror_link(c: httpx.AsyncClient, major: int, os_name: str, arch: str, name: str) -> str | None:
    """Tsinghua mirrors Adoptium's release tree with identical file names."""
    url = f"{TUNA}/{major}/jdk/{arch}/{os_name}/{name}"
    try:
        r = await c.head(url)
    except (httpx.HTTPError, OSError):
        return None
    return url if r.status_code == 200 else None


async def _asset(major: int, os_name: str, arch: str) -> dict:
    url = f"{ADOPTIUM}/assets/latest/{major}/hotspot"
    params = {"os": os_name, "architecture": arch, "image_type": "jdk", "vendor": "eclipse"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True, headers={"User-Agent": UA}) as c:
        r = await c.get(url, params=params)
        if r.status_code == 404:
            raise JavaError(f"Adoptium 没有 Java {major} 的 {os_name}/{arch} 构建")
        r.raise_for_status()
        data = r.json()
    if not data:
        raise JavaError(f"Adoptium 没有 Java {major} 的 {os_name}/{arch} 构建")
    entry = data[0]
    pkg = entry.get("binary", {}).get("package", {})
    if not pkg.get("link"):
        raise JavaError("Adoptium 返回的下载信息不完整")
    return {
        "url": pkg["link"],
        "name": pkg.get("name") or f"temurin-{major}.zip",
        "sha256": (pkg.get("checksum") or "").lower(),
        "size": int(pkg.get("size") or 0),
        "version": entry.get("version", {}).get("semver") or entry.get("release_name", ""),
    }


def _extract(archive: Path, dest: Path) -> Path:
    """Unpack a Temurin archive into ``dest``.

    The archive's leading ``jdk-<version>-jre/`` directory is kept: renaming it
    away fails on Windows when a virus scanner briefly holds the freshly
    written files, and the detector already searches recursively.
    """
    dest.mkdir(parents=True, exist_ok=True)
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest)
    else:
        with tarfile.open(archive, "r:gz") as tf:
            for member in tf.getmembers():
                # Same guard as the backup restorer: never let an archive write
                # outside the destination.
                target = (dest / member.name).resolve()
                if not target.is_relative_to(dest.resolve()):
                    raise JavaError("压缩包包含非法路径")
            tf.extractall(dest)
    return dest


def _find_java(root: Path) -> Path | None:
    exe = _exe()
    for rel in (Path("bin") / exe, Path("Contents") / "Home" / "bin" / exe):
        if (root / rel).is_file():
            return root / rel
    for candidate in root.rglob(exe):
        if candidate.is_file():
            return candidate
    return None


async def download(major: int, progress=None) -> JavaRuntime:
    if major not in MAJORS:
        raise JavaError(f"不支持的 Java 版本：{major}")
    os_name, arch = _platform()
    asset = await _asset(major, os_name, arch)
    root = managed_root()
    version = re.sub(r"[^0-9A-Za-z._+-]", "", asset["version"] or str(major)) or str(major)
    final = root / f"{major}-{version}"
    exe = "jcmd.exe" if os.name == "nt" else "jcmd"
    if final.exists():
        found = _find_java(final)
        if found and (found.parent / exe).is_file():
            info = await _probe(str(found))
            if info:
                return info
        # A pre-JFR build installed the JRE here: same version, no jcmd.
        shutil.rmtree(final, ignore_errors=True)

    tmp = root / f".download-{major}-{os.getpid()}"
    tmp.mkdir(parents=True, exist_ok=True)
    archive = tmp / asset["name"]
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, read=180.0), follow_redirects=True, headers={"User-Agent": UA}
        ) as c:
            # The API's own link redirects to GitHub releases, which are often
            # unusable from mainland China; try the Tsinghua mirror first and
            # fall back. Both carry the identical artifact (same file name).
            mirror = await _mirror_link(c, major, os_name, arch, asset["name"])
            urls = [u for u in (mirror, asset["url"]) if u]
            last_exc: Exception | None = None
            for attempt, url in enumerate(urls):
                digest = hashlib.sha256()
                done = 0
                try:
                    async with c.stream("GET", url) as r:
                        r.raise_for_status()
                        total = int(r.headers.get("content-length", "0") or asset["size"] or 0)
                        with archive.open("wb") as fh:
                            async for chunk in r.aiter_bytes(256 * 1024):
                                fh.write(chunk)
                                digest.update(chunk)
                                done += len(chunk)
                                if progress:
                                    res = progress(done, total, f"下载 JDK {major}")
                                    if asyncio.iscoroutine(res):
                                        await res
                    break
                except (httpx.HTTPError, OSError) as exc:
                    last_exc = exc
                    if attempt + 1 < len(urls):
                        log.warning("java download from %s failed (%s), trying fallback", url.split("/")[2], exc)
            else:
                raise JavaError(f"JDK 下载失败：{last_exc}")
        if asset["sha256"] and digest.hexdigest().lower() != asset["sha256"]:
            raise JavaError("JDK 校验失败（SHA-256 不匹配）")
        if not archive.exists() or archive.stat().st_size == 0:
            raise JavaError("JDK 下载为空")
        if progress:
            res = progress(done, done, "解压")
            if asyncio.iscoroutine(res):
                await res
        await asyncio.to_thread(_extract, archive, final)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    found = _find_java(final)
    if found is None:
        raise JavaError("JDK 解压后找不到 java 可执行文件")
    if os.name != "nt":
        try:
            found.chmod(0o755)
        except OSError:
            pass
    invalidate()
    info = await _probe(str(found))
    if info is None:
        raise JavaError("下载的 JDK 无法执行")
    return info


def remove(path: str) -> bool:
    """Delete a managed runtime directory. System JDKs are never touched."""
    target = Path(path).resolve()
    root = managed_root().resolve()
    if not target.is_relative_to(root):
        raise JavaError("只能删除面板下载的运行时")
    directory = target
    while directory.parent != root and directory.parent != directory:
        directory = directory.parent
    shutil.rmtree(directory, ignore_errors=True)
    invalidate()
    return True
