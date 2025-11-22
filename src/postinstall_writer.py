# src/postinstall_writer.py
"""
Postinstall writer: copy .env.example -> user .env if missing and
inject values from install_values.txt found in {app}, {tmp}, or cwd/install.
Designed to be packaged as one-file exe with PyInstaller.
"""

import os
import sys
import shutil
import tempfile
import argparse
import logging
import subprocess
import getpass
from pathlib import Path

APP_NAME = "hankstoremanager"
ENV_NAME = ".env"
ENV_EXAMPLE_NAME = ".env.example"
INSTALL_VALUES_BASENAME = "install_values.txt"

# Configure minimal logging
logging.basicConfig(level=logging.INFO, format="[postinstall] %(message)s")
logger = logging.getLogger(__name__)


def get_user_data_dir(app_name=APP_NAME):
    if sys.platform.startswith("win"):
        return Path(os.getenv("APPDATA") or Path.home()) / app_name
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_name
    return Path.home() / ".local" / "share" / app_name


def atomic_replace_file(target: Path, content: str) -> bool:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        logger.debug("Failed to ensure parent directory for %s", target)
    # Create a secure temporary file in the target directory
    tmp_file = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", dir=str(target.parent))
        tmp_file = tmp_path
        os.close(fd)
        # Write using utf-8 and ensure flush to disk by closing the file
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        # Replace atomically
        os.replace(tmp_path, str(target))
        # Set permissive owner-only permissions on POSIX
        try:
            if os.name != "nt":
                os.chmod(str(target), 0o600)
        except Exception:
            logger.debug("Failed to chmod %s", target)
        # On Windows attempt to restrict inheritance and grant R/W to current user (best-effort)
        try:
            if os.name == "nt":
                user = os.getenv("USERNAME") or getpass.getuser()
                cmd = ['icacls', str(target), '/inheritance:r', '/grant:r', f'{user}:(R,W)', '/C']
                subprocess.run(cmd, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception:
            logger.debug("Failed to run icacls for %s", target)
        return True
    except Exception:
        logger.exception("atomic_replace_file failed for %s", target)
        # Cleanup tmp if exists
        try:
            if tmp_file and os.path.exists(tmp_file):
                os.remove(tmp_file)
        except Exception:
            pass
        return False


def read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        logger.debug("read_text_file failed for %s", path)
        return ""


def write_text_file(path: Path, text: str) -> bool:
    return atomic_replace_file(path, text)


def merge_or_set_env_key(env_text: str, key: str, value: str, overwrite: bool = False) -> str:
    """
    Ensure key=value is present in env_text. If overwrite is True, replace existing value.
    Preserve final newline conventions (use \n internally).
    """
    lines = env_text.splitlines()
    out_lines = []
    found = False
    prefix = f"{key}="
    for ln in lines:
        if not found and ln.strip().startswith(prefix):
            found = True
            if overwrite:
                out_lines.append(f"{key}={value}")
            else:
                out_lines.append(ln)
        else:
            out_lines.append(ln)
    if not found:
        sep = "\n" if env_text else ""
        return env_text + sep + f"{key}={value}\n"
    # Reconstruct with trailing newline if original had it
    has_trailing = env_text.endswith("\n")
    result = "\n".join(out_lines)
    if has_trailing:
        result += "\n"
    return result


def parse_key_values_file(path: Path):
    d = {}
    try:
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if not line.strip() or line.strip().startswith("#"):
                continue
            k, sep, v = line.partition("=")
            if sep:
                d[k.strip()] = v.strip()
    except Exception:
        logger.debug("parse_key_values_file failed for %s", path)
    return d


def find_install_values_paths(exe_dir: Path):
    candidates = [
        exe_dir / INSTALL_VALUES_BASENAME,
        Path(tempfile.gettempdir()) / INSTALL_VALUES_BASENAME,
        Path.cwd() / "install" / INSTALL_VALUES_BASENAME,
        Path.cwd() / INSTALL_VALUES_BASENAME,
    ]
    return [p for p in candidates if p.exists()]


def generate_fernet_key():
    try:
        from cryptography.fernet import Fernet
        return Fernet.generate_key().decode()
    except Exception:
        import base64, os
        logger.debug("cryptography not available; falling back to random base64 key generation")
        return base64.urlsafe_b64encode(os.urandom(32)).decode()


def detect_exe_dir():
    """
    Return the directory where the executable bundles resources.
    For PyInstaller one-file frozen apps, sys._MEIPASS is used.
    """
    try:
        if getattr(sys, "frozen", False):
            # When frozen by PyInstaller, _MEIPASS holds a temp extraction dir for bundled files.
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                return Path(meipass).resolve()
            # Fallback to executable directory
            return Path(sys.executable).resolve().parent
    except Exception:
        logger.debug("PyInstaller frozen detection failed")
    # Non-frozen: use source file location
    try:
        return Path(__file__).resolve().parent
    except Exception:
        return Path(sys.argv[0]).resolve().parent


def main():
    parser = argparse.ArgumentParser(
        description="Postinstall writer: copy .env.example -> user .env and inject key from install_values.txt"
    )
    parser.add_argument(
        "--generate-if-missing",
        dest="generate",
        action="store_true",
        help="Generate a Fernet key into user .env if FACTURATION_OBR_FERNET_KEY is missing (DEV convenience)",
    )
    parser.add_argument(
        "--force",
        dest="force",
        action="store_true",
        help="Force overwrite existing FACTURATION_OBR_FERNET_KEY if present",
    )
    parser.add_argument(
        "--verbose",
        dest="verbose",
        action="store_true",
        help="Enable verbose debug output",
    )
    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)
        logger.debug("Verbose logging enabled")

    exe_dir = detect_exe_dir()
    user_dir = get_user_data_dir()
    try:
        user_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        logger.debug("Could not ensure user_dir exists: %s", user_dir)

    logger.info("exe_dir=%s user_dir=%s", exe_dir, user_dir)

    env_path = user_dir / ENV_NAME

    # copy .env.example -> user .env if missing
    if not env_path.exists():
        candidate_paths = [
            exe_dir / ENV_EXAMPLE_NAME,
            exe_dir.parent / ENV_EXAMPLE_NAME,
            Path.cwd() / ENV_EXAMPLE_NAME,
        ]
        copied = False
        for cand in candidate_paths:
            logger.debug("Checking candidate .env.example: %s", cand)
            if cand.exists():
                try:
                    shutil.copy2(str(cand), str(env_path))
                    copied = True
                    logger.info("Copied .env.example from %s to %s", cand, env_path)
                    break
                except Exception:
                    logger.exception("Failed to copy %s to %s", cand, env_path)
                    copied = False
        if not copied and not env_path.exists():
            # Create empty env file as placeholder
            ok = write_text_file(env_path, "")
            if ok:
                logger.info("Created empty user .env at %s", env_path)
            else:
                logger.error("Failed to create placeholder user .env at %s", env_path)

    current_text = read_text_file(env_path)

    # Locate install_values.txt from several candidate locations
    found_paths = find_install_values_paths(exe_dir)
    install_vals = {}
    for p in found_paths:
        logger.debug("Found install values candidate: %s", p)
        install_vals = parse_key_values_file(p)
        if install_vals:
            logger.info("Loaded install values from %s", p)
            break

    injected = False
    if install_vals.get("FACTURATION_OBR_FERNET_KEY"):
        val = install_vals.get("FACTURATION_OBR_FERNET_KEY").strip().strip('"').strip("'")
        new_text = merge_or_set_env_key(current_text, "FACTURATION_OBR_FERNET_KEY", val, overwrite=args.force)
        ok = write_text_file(env_path, new_text)
        if not ok:
            logger.error("Failed to write user .env with injected FACTURATION_OBR_FERNET_KEY")
            return 2
        injected = True
        current_text = new_text
        logger.info("Injected FACTURATION_OBR_FERNET_KEY into %s", env_path)

    if not injected and args.generate:
        # Only generate if missing or empty
        existing = None
        for ln in current_text.splitlines():
            if ln.strip().startswith("FACTURATION_OBR_FERNET_KEY="):
                existing = ln.partition("=")[2].strip()
                break
        if not existing:
            gen = generate_fernet_key()
            new_text = merge_or_set_env_key(current_text, "FACTURATION_OBR_FERNET_KEY", gen, overwrite=args.force)
            ok = write_text_file(env_path, new_text)
            if ok:
                logger.info("Generated and wrote FACTURATION_OBR_FERNET_KEY to %s", env_path)
            else:
                logger.error("Failed to write generated FACTURATION_OBR_FERNET_KEY to %s", env_path)

    # optional: copy app.inv if present near exe (useful for licensing or fingerprints)
    try:
        candidates = [exe_dir / "app.inv", Path.cwd() / "app.inv", exe_dir.parent / "app.inv"]
        for c in candidates:
            if c.exists():
                try:
                    dest = user_dir / "app.inv"
                    shutil.copy2(str(c), str(dest))
                    logger.info("Copied app.inv from %s to %s", c, dest)
                except Exception:
                    logger.debug("Failed to copy app.inv from %s", c)
    except Exception:
        logger.debug("Exception while handling app.inv candidates", exc_info=True)

    return 0


if __name__ == "__main__":
    try:
        code = main()
        sys.exit(code if isinstance(code, int) else 0)
    except Exception:
        logger.exception("Unhandled exception in postinstall_writer")
        sys.exit(3)
