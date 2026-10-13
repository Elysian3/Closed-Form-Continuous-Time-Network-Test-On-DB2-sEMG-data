"""Run real local API/browser checks with disposable synthetic recording storage."""

import os
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

UI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI))
import server

NODE = Path(
    os.environ.get(
        "NODE_EXE",
        "C:/Users/14148/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe",
    )
)


def main():
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError("Run with the existing Python 3.13 interpreter")
    with tempfile.TemporaryDirectory(prefix="antikythera-browser-") as directory:
        service = server.CaptureService(
            UI.parent, output_root=directory,
            capture_profile=os.environ.get('CAPTURE_PROFILE', 'full'),
        )
        if service.template is None:
            raise RuntimeError(service.template_error)
        handler = type("TestHandler", (server.Handler,), {"service": service})
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        env = {
            **os.environ,
            "UI_URL": f"http://127.0.0.1:{httpd.server_port}",
            "SESSION_ROOT": directory,
        }
        try:
            for name in sys.argv[1:] or ["protocol.cjs", "flow.cjs", "mesh.cjs"]:
                subprocess.run(
                    [str(NODE), str(UI / "tests" / name)], env=env, check=True
                )
        finally:
            httpd.shutdown()
            thread.join()
            httpd.server_close()


if __name__ == "__main__":
    main()
