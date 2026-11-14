# -*- coding: utf-8 -*-
"""Start main Phoenix bot and optional Vault user bot together (Railway-friendly)."""
import logging
import os
import runpy
import threading
import time


def _resolve_user_bot_token() -> str:
    token = (os.environ.get("USER_BOT_TOKEN") or os.environ.get("BOT_TOKEN_3") or "").strip()
    if token:
        return token
    try:
        import userbot_store
        userbot_store.init()
        return (userbot_store.get_token() or "").strip()
    except Exception as e:
        logging.warning("userbot_store token read failed: %s", e)
        return ""


def run_user_bot():
    token = _resolve_user_bot_token()
    if not token:
        logging.info("USER_BOT_TOKEN / userbot_meta not set - skipping Vault user bot")
        return
    # Ensure child process/path sees the token
    os.environ["USER_BOT_TOKEN"] = token
    while True:
        try:
            logging.info("Starting Vault user bot...")
            runpy.run_path("user_bot.py", run_name="__main__")
            logging.warning("Vault user bot exited cleanly; restarting in 5s")
        except Exception as e:
            logging.error("Vault user bot failed: %s — restart in 15s", e)
            time.sleep(15)
            continue
        time.sleep(5)



def _ensure_tools():
    """Ensure Xray and Pantegnos binaries exist under /data (or env paths)."""
    import os, subprocess, sys
    from pathlib import Path

    def _run(cmd):
        try:
            subprocess.run(cmd, check=False)
        except Exception as e:
            print(f"[start] tool setup warn: {e}", flush=True)

    data = Path(os.environ.get("DATA_DIR", "/data"))
    try:
        data.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    xray = Path(os.environ.get("XRAY_BIN", str(data / "xray")))
    if not xray.is_file():
        print(f"[start] Xray missing at {xray} — downloading...", flush=True)
        _run([sys.executable, "download_xray.py"])
        # download_xray typically writes ./xray
        for cand in (Path("xray"), Path("/app/xray"), xray):
            if cand.is_file():
                try:
                    if cand.resolve() != xray.resolve():
                        xray.write_bytes(cand.read_bytes())
                    xray.chmod(0o755)
                except Exception:
                    pass
                break
    os.environ.setdefault("XRAY_BIN", str(xray))

    pbin = Path(os.environ.get("PANTEGNOS_BIN", str(data / "pantegnos")))
    if not pbin.is_file():
        print(f"[start] Pantegnos missing at {pbin} — downloading...", flush=True)
        _run([sys.executable, "download_pantegnos.py", str(pbin)])
    if pbin.is_file():
        try:
            pbin.chmod(0o755)
        except Exception:
            pass
        os.environ.setdefault("PANTEGNOS_BIN", str(pbin))


_ensure_tools()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    token = _resolve_user_bot_token()
    if token:
        t = threading.Thread(target=run_user_bot, name="user-bot", daemon=True)
        t.start()
        logging.info("Started Vault user bot in background thread (auto-restart)")
    else:
        logging.info("Vault user bot not started (no token in env or userbot_meta.db)")

    runpy.run_path("bot.py", run_name="__main__")
