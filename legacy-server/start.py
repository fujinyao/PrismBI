"""
PrismBI Legacy Server Entry Point

For systems where pywebview is unavailable, this script
starts the backend server and opens the system browser.
Works on Windows 7 SP1 and later.

Starts both the Python backend and the Next.js frontend
standalone server (if available), then opens the browser
to the frontend URL.
"""

import logging
import os
import socket
import sys
import threading
import time
import webbrowser
import subprocess
import shutil

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("prismbi")

FRONTEND_PORT = 5173


def find_free_port(start=8400, max_tries=100):
    for port in range(start, start + max_tries):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"No free port in range {start}-{start + max_tries}")


def wait_for_server(host, port, timeout=30):
    import urllib.request
    import urllib.error
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            req = urllib.request.Request(f"http://{host}:{port}/api/settings/public", method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.5)
    return False


def start_frontend(script_dir, backend_host, backend_port):
    frontend_dir = os.path.join(script_dir, "frontend", "out")
    server_js = os.path.join(frontend_dir, "server.js")
    if not os.path.isfile(server_js):
        logger.warning("Frontend standalone server not found at %s", server_js)
        return None, 0

    node = shutil.which("node") or shutil.which("nodejs")
    if node is None:
        logger.warning("Node.js not found in PATH — cannot start frontend server")
        return None, 0

    frontend_env = os.environ.copy()
    frontend_env["PORT"] = str(FRONTEND_PORT)
    frontend_env["NEXT_PUBLIC_API_URL"] = f"http://{backend_host}:{backend_port}"
    frontend_env["API_INTERNAL_URL"] = f"http://{backend_host}:{backend_port}"
    frontend_env["WS_INTERNAL_URL"] = f"http://{backend_host}:{backend_port}"
    frontend_env["NEXT_PUBLIC_WS_URL"] = f"http://{backend_host}:{backend_port}"
    frontend_env["HOSTNAME"] = "127.0.0.1"

    process = subprocess.Popen(
        [node, server_js],
        cwd=frontend_dir,
        env=frontend_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    logger.info("Frontend server PID %d on port %d", process.pid, FRONTEND_PORT)
    return process, FRONTEND_PORT


def main():
    port = find_free_port()
    host = "127.0.0.1"
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(data_dir, exist_ok=True)

    print()
    print("  ____                    _  ____  _     ___   __")
    print(" |  _ \\ _ __ ___  _ __ __| || __ )(_)___|_ _| / _|")
    print(" | |_) | '__/ _ \\| '__/ _` ||  _ \\| / __|| |_ | |_ ")
    print(" |  __/| | | (_) | | | (_| || |_) | \\__ \\ | ||  _|")
    print(" |_|   |_|  \\___/|_|  \\__,_||____/|_||___/___||_|")
    print()
    print("  PrismBI Server - Legacy Edition (Windows 7+)")
    print(f"  Starting on http://localhost:{port}")
    print(f"  Data directory: {data_dir}")
    print()
    print("  Press Ctrl+C to stop the server")
    print()

    backend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
    sys.path.append(backend_dir)
    os.environ["PRISMBI_PORT"] = str(port)
    os.environ["PRISMBI_DATA_DIR"] = data_dir
    os.environ.setdefault("PRISMBI_DB_PATH", os.path.join(data_dir, "prismbi.duckdb"))
    os.environ.setdefault("PRISMBI_CORS_ORIGINS", f"http://localhost:{port},http://127.0.0.1:{port}")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    frontend_proc, frontend_port = start_frontend(script_dir, host, port)
    if frontend_proc is not None:
        navigate_url = f"http://localhost:{frontend_port}"
    else:
        navigate_url = f"http://localhost:{port}"

    def open_browser():
        target_port = frontend_port if frontend_proc is not None else port
        if wait_for_server(host, port, timeout=30):
            webbrowser.open(navigate_url)

    threading.Thread(target=open_browser, daemon=True).start()

    try:
        import uvicorn
        from main import app
        uvicorn.run(app, host=host, port=port, log_level="info")
    except ImportError:
        print("ERROR: uvicorn not found. Install with: pip install uvicorn[standard]")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nShutting down PrismBI...")
    finally:
        if frontend_proc is not None:
            frontend_proc.terminate()
            try:
                frontend_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                frontend_proc.kill()


if __name__ == "__main__":
    main()
