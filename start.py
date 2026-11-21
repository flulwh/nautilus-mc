#!/usr/bin/env python3
"""一键启动 Nautilus。

用法：
    start.bat                     Windows：双击，或命令行里执行
    ./start.sh                    Linux / macOS
    python start.py --port 8200   带参数的等效写法

脚本按顺序做四件事，任一步失败都会给出可直接照做的修复提示：

    1. 准备 .venv —— 首次运行自动创建并安装依赖，之后秒起
    2. 首次启动时引导设置管理员密码（回车则自动生成）
    3. 检查端口是否被占用
    4. 启动面板，等端口就绪后自动打开浏览器

Ctrl+C 停止面板。所有 Nautilus 配置仍走 ``NAUTILUS_*`` 环境变量或 ``.env``。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = Path(os.environ.get("NAUTILUS_VENV") or (ROOT / ".venv")).expanduser().resolve()
#: 记录依赖安装时的 pyproject 指纹，改了依赖就会重装。
STAMP = VENV / ".nautilus-setup"
MIN_PYTHON = (3, 11)
#: 国内网络默认走清华镜像；装不上再回落到官方源。
DEFAULT_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
#: 安装失败时用来判断"依赖也许已经齐了"的关键模块。
CRITICAL_MODULES = (
    "fastapi",
    "uvicorn",
    "sqlalchemy",
    "fastapi_users",
    "alembic",
    "casbin",
    "psutil",
    "pydantic_settings",
    "httpx",
)


# --------------------------------------------------------------------------
# 输出helper：只用 ASCII 标点，避免 cp936 控制台编码告警
# --------------------------------------------------------------------------
def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(msg: str) -> None:
    say(f"[+] {msg}")


def warn(msg: str) -> None:
    say(f"[!] {msg}")


def die(msg: str, hint: str | None = None) -> int:
    say(f"[x] {msg}")
    if hint:
        for line in hint.strip().splitlines():
            say(f"    {line.strip()}")
    return 1


# --------------------------------------------------------------------------
# 阶段一：准备解释器与依赖
# --------------------------------------------------------------------------
def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def inside_venv() -> bool:
    return Path(sys.prefix).resolve() == VENV


def pyproject_fingerprint() -> str:
    try:
        return hashlib.sha256((ROOT / "pyproject.toml").read_bytes()).hexdigest()[:16]
    except OSError:
        return "unknown"


def deps_current() -> bool:
    """只要 pyproject 没改过就算就绪。

    刻意不比较 Python 版本：venv 一旦建好，里面的解释器就固定了；
    而 bootstrap 阶段跑的可能是另一个解释器（``py -3`` 和 PATH 上的
    ``python`` 常常不是同一版本），拿它来比会来回触发重装。
    """
    try:
        meta = json.loads(STAMP.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return meta.get("pyproject") == pyproject_fingerprint()


def python_tag() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def write_stamp() -> None:
    try:
        STAMP.parent.mkdir(parents=True, exist_ok=True)
        STAMP.write_text(
            json.dumps({"pyproject": pyproject_fingerprint(), "python": python_tag(), "at": int(time.time())}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def create_venv() -> Path | None:
    py = venv_python()
    if py.exists():
        return py
    step(f"首次运行，创建虚拟环境 {VENV.name} ...")
    try:
        rc = subprocess.call([sys.executable, "-m", "venv", str(VENV)])
    except OSError as exc:
        die(f"创建虚拟环境失败：{exc}")
        return None
    if rc != 0 or not py.exists():
        die(
            "创建虚拟环境失败。",
            """
            Debian/Ubuntu 可能缺 venv 组件：
                sudo apt install python3-venv
            或者装一个完整 Python 3.11+ 后重试。
            """,
        )
        return None
    return py


def pip_install(py: Path) -> bool:
    """先镜像后官方源；全部失败返回 False。"""
    indexes: list[str | None] = [os.environ.get("NAUTILUS_PIP_INDEX") or DEFAULT_INDEX, None]
    base = [str(py), "-m", "pip", "install", "--disable-pip-version-check", "-e", str(ROOT)]
    for i, index in enumerate(indexes):
        cmd = base + (["-i", index] if index else [])
        label = index or "官方 PyPI"
        step(f"安装依赖（{label}）...")
        if subprocess.call(cmd, cwd=ROOT) == 0:
            return True
        if i + 1 < len(indexes):
            warn(f"{label} 装不上，换源重试 ...")
    return False


def modules_importable(py: Path) -> bool:
    """安装失败时的兜底探测：依赖可能早就装好了，只是网络不通。"""
    code = "import " + ", ".join(CRITICAL_MODULES)
    return subprocess.call([str(py), "-c", code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0


def bootstrap() -> int:
    """在系统 Python 下运行：确保 .venv 可用，然后切进去重新执行本脚本。"""
    if sys.version_info < MIN_PYTHON:
        need = ".".join(map(str, MIN_PYTHON))
        return die(
            f"需要 Python {need}+，当前是 {python_tag()}。",
            """
            Windows:  winget install Python.Python.3.12
            macOS:    brew install python@3.12
            Linux:    使用发行版自带的 python3（>= 3.11）
            也可以显式指定解释器：py -3.12 start.py
            """,
        )

    py = create_venv()
    if py is None:
        return 1

    if not deps_current():
        if not pip_install(py):
            if modules_importable(py):
                warn("依赖安装没成功，但关键模块都在，继续启动。")
                write_stamp()
            else:
                return die(
                    "依赖安装失败。",
                    """
                    手动重试一次看看真实报错：
                        .venv\\Scripts\\python -m pip install -e .
                    国内网络可指定镜像：
                        set NAUTILUS_PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
                    """,
                )
        else:
            write_stamp()

    # 切进 .venv 重新执行自己，之后 sys.executable 就是 venv 里的解释器。
    #
    # 这里特意用 subprocess 而不是 os.execv：Windows 上 os.execv 是模拟实现，
    # 父进程会立刻以 0 退出、把子进程丢成孤儿，退出码就传不回去了
    # （端口冲突本该返回 1，结果 start.bat 看到的是 0）。
    argv = [str(py), "-u", str(Path(__file__).resolve()), *sys.argv[1:]]
    try:
        return subprocess.call(argv, cwd=str(ROOT))
    except KeyboardInterrupt:
        return 130


# --------------------------------------------------------------------------
# 阶段二：准备运行时（此时已在 .venv 内）
# --------------------------------------------------------------------------
def port_in_use(host: str, port: int) -> bool:
    # 刻意不设 SO_REUSEADDR：Windows 上设了会允许绑定已占用的端口，检测就失效了。
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return True
    return False


def accepts(host: str, port: int, timeout: float = 0.35) -> bool:
    probe_host = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
    try:
        with socket.create_connection((probe_host, port), timeout=timeout):
            return True
    except OSError:
        return False


def ask_admin_password(settings) -> None:
    """首次启动引导设置管理员密码；回车交给 Nautilus 自己生成并打印。"""
    if settings.bootstrap_admin_password or os.environ.get("NAUTILUS_BOOTSTRAP_ADMIN_PASSWORD"):
        return
    if settings.db_path.exists():
        return
    if not sys.stdin.isatty():
        return
    say()
    say("首次启动需要创建一个管理员账号（用户名 " + settings.bootstrap_admin_user + "）。")
    try:
        import getpass

        pwd = getpass.getpass("设置管理员密码（直接回车则自动生成并打印在下方日志里）: ")
    except (EOFError, KeyboardInterrupt):
        say()
        return
    if pwd:
        os.environ["NAUTILUS_BOOTSTRAP_ADMIN_PASSWORD"] = pwd
        say("[+] 已使用你输入的密码创建管理员。")
    else:
        say("[+] 稍后会在日志里打印自动生成的管理员密码，请留意保存。")


def open_browser_when_ready(url: str, host: str, port: int, proc: subprocess.Popen) -> None:
    deadline = time.time() + 60
    while time.time() < deadline:
        if proc.poll() is not None:  # 进程已经退了，别再开浏览器
            return
        if accepts(host, port):
            try:
                webbrowser.open(url)
            except Exception:  # noqa: BLE001 - 打不开浏览器不影响面板本身
                pass
            return
        time.sleep(0.4)


def serve(args: argparse.Namespace) -> int:
    os.chdir(ROOT)
    try:
        from nautilus.settings import settings
    except Exception as exc:  # noqa: BLE001 - 依赖缺失或目录不可写时给出可读信息
        return die(
            f"启动前置检查失败：{exc}",
            """
            依赖缺失：  删除 .venv 后重新运行本脚本即可重装。
            目录不可写：检查 NAUTILUS_DATA_DIR 是否指向一个可写目录。
            """,
        )

    host = args.host or settings.host
    port = args.port or settings.port

    ask_admin_password(settings)

    if port_in_use(host, port):
        return die(
            f"端口 {port} 已被占用（{host}）。",
            f"""
            换一个端口再启动：
                start.bat --port {port + 1}
            或者看看谁占了它：
                netstat -ano | findstr :{port}
            """,
        )

    url = f"http://{'127.0.0.1' if host in {'0.0.0.0', '::', ''} else host}:{port}{settings.root_path}"
    say()
    say("=" * 60)
    say("  Nautilus 面板")
    say(f"  地址    {url}")
    say(f"  监听    {host}:{port}")
    say(f"  数据    {settings.data_dir.resolve()}")
    say("  停止    Ctrl+C")
    say("=" * 60)
    say()

    env = os.environ.copy()
    env["NAUTILUS_HOST"] = host
    env["NAUTILUS_PORT"] = str(port)
    # 子进程不需要再走一遍 venv 引导。
    env.pop("NAUTILUS_VENV", None)

    proc = subprocess.Popen([sys.executable, "-m", "nautilus"], cwd=str(ROOT), env=env)
    if not args.no_browser:
        threading.Thread(
            target=open_browser_when_ready, args=(url, host, port, proc), daemon=True
        ).start()

    try:
        rc = proc.wait()
    except KeyboardInterrupt:
        # Ctrl+C 通常已经发到子进程了，这里只等它收尾。
        try:
            rc = proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.terminate()
            rc = proc.wait()
    if rc != 0:
        warn(f"面板退出码 {rc}，上面日志里有失败原因。")
    return rc


# --------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        prog="start",
        description="一键启动 Nautilus 面板",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-H", "--host", help="监听地址（默认取 NAUTILUS_HOST）")
    parser.add_argument("-p", "--port", type=int, help="监听端口（默认取 NAUTILUS_PORT）")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    args = parser.parse_args()

    if not inside_venv():
        return bootstrap()  # 不会返回：要么 execv 进 venv，要么返回错误码
    return serve(args)


if __name__ == "__main__":
    sys.exit(main())
