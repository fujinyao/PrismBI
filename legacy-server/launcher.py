"""
PrismBI Desktop Launcher (Win7+ Compatible)

Creates a native desktop window using pywebview.
On Win10+, uses EdgeChromium (WebView2).
On Win7, falls back to MSHTML or CEF.

Starts the Python backend as a subprocess,
then starts the Next.js frontend standalone server (if available),
and loads the frontend in the window.
"""

import argparse
import logging
import os
import socket
import subprocess
import sys
import time
import webbrowser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("prismbi")

BACKEND_LOG_FILE = "backend.log"
FRONTEND_PORT = 5173


def find_free_port(start_port: int = 8400, max_tries: int = 100) -> int:
    for port in range(start_port, start_port + max_tries):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"No free port in range {start_port}-{start_port + max_tries}")


def wait_for_server(host: str, port: int, timeout: float = 30.0) -> bool:
    import urllib.request
    import urllib.error
    start = time.time()
    url = f"http://{host}:{port}/api/settings/public"
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.5)
    return False


def start_backend(host: str, port: int, data_dir: str) -> subprocess.Popen:
    env = os.environ.copy()
    env["PRISMBI_PORT"] = str(port)
    env["PRISMBI_DATA_DIR"] = data_dir
    env.setdefault("PRISMBI_DB_PATH", os.path.join(data_dir, "prismbi.duckdb"))
    env.setdefault("PRISMBI_CORS_ORIGINS", f"http://localhost:{port},http://127.0.0.1:{port}")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    if sys.platform == "win32":
        backend_exe = os.path.join(script_dir, "backend", "prismbi-backend.exe")
    else:
        backend_exe = os.path.join(script_dir, "backend", "prismbi-backend")

    if not os.path.isfile(backend_exe):
        backend_exe = os.path.join(script_dir, "backend", "main.py")
        cmd = [sys.executable, backend_exe, "--host", host, "--port", str(port)]
    else:
        cmd = [backend_exe, "--host", host, "--port", str(port)]

    log_path = os.path.join(data_dir, BACKEND_LOG_FILE)
    backend_log = open(log_path, "a")

    process = subprocess.Popen(
        cmd,
        env=env,
        cwd=script_dir,
        stdout=backend_log,
        stderr=subprocess.STDOUT,
    )
    logger.info("Backend PID %d, log: %s", process.pid, log_path)
    return process


def stop_backend(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        logger.warning("Backend did not terminate, killing...")
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/F", "/T"],
                           capture_output=True)
        else:
            import signal
            try:
                os.kill(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def start_frontend(script_dir: str, backend_host: str, backend_port: int) -> tuple[subprocess.Popen | None, int]:
    frontend_dir = os.path.join(script_dir, "frontend", "out")
    server_js = os.path.join(frontend_dir, "server.js")
    if not os.path.isfile(server_js):
        logger.warning("Frontend standalone server not found at %s", server_js)
        logger.warning("Falling back: opening browser directly to backend API (UI unavailable)")
        return None, 0

    import shutil
    node = shutil.which("node") or shutil.which("nodejs")
    if node is None:
        logger.warning("Node.js not found in PATH — cannot start frontend server")
        logger.warning("Falling back: opening browser directly to backend API (UI unavailable)")
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


def stop_frontend(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        logger.warning("Frontend did not terminate, killing...")
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/F", "/T"],
                           capture_output=True)
        else:
            import signal
            try:
                os.kill(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def create_window(url: str, title: str = "PrismBI", width: int = 1280, height: int = 800):
    try:
        import webview
        logger.info("Using pywebview for native window (CEF/WebView2)")
        window = webview.create_window(
            title=title,
            url=url,
            width=width,
            height=height,
            min_size=(1024, 600),
            resizable=True,
            text_select=True,
        )
        webview.start(debug=False)
        return window
    except ImportError:
        logger.warning("pywebview not available, falling back to system browser")
        webbrowser.open(url)
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="PrismBI Desktop (Win7+ Compatible)")
    parser.add_argument("--port", type=int, default=None, help="Server port (auto-detect if occupied)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Server host")
    parser.add_argument("--no-gui", action="store_true", help="Run in server-only mode (no window)")
    parser.add_argument("--data-dir", type=str, default=None, help="Data directory")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = args.data_dir or os.path.join(script_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    host = args.host
    port = args.port or int(os.getenv("PRISMBI_PORT", "8400"))

    port = find_free_port(start_port=port)

    logger.info("Starting PrismBI backend on port %d", port)
    backend_proc = start_backend(host, port, data_dir)

    if not wait_for_server(host, port, timeout=60):
        logger.error("Backend failed to start within 60 seconds. Check %s for details.",
                      os.path.join(data_dir, BACKEND_LOG_FILE))
        stop_backend(backend_proc)
        sys.exit(1)

    logger.info("Backend is ready at http://localhost:%d", port)

    frontend_proc, frontend_port = start_frontend(script_dir, host, port)
    if frontend_proc is not None:
        navigate_url = f"http://localhost:{frontend_port}"
        logger.info("Frontend is ready at %s", navigate_url)
    else:
        navigate_url = f"http://localhost:{port}"

    if args.no_gui:
        logger.info("Running in server-only mode. Press Ctrl+C to stop.")
        logger.info("Open your browser at: %s", navigate_url)
        try:
            backend_proc.wait()
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            stop_backend(backend_proc)
            stop_frontend(frontend_proc)
    else:
        logger.info("Opening PrismBI window...")
        try:
            create_window(navigate_url, title="PrismBI", width=1280, height=800)
        except Exception as e:
            logger.warning("Window creation failed (%s), opening browser", e)
            webbrowser.open(navigate_url)
            logger.info("Running in browser mode. Press Ctrl+C to stop.")
            try:
                backend_proc.wait()
            except KeyboardInterrupt:
                pass
        finally:
            logger.info("Shutting down backend...")
            stop_backend(backend_proc)
            stop_frontend(frontend_proc)


if __name__ == "__main__":
    main()
