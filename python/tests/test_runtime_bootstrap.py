"""Windows PowerShell 5.1 tests for the managed runtime downloader."""

import base64
import hashlib
import http.server
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit


SCRIPT = Path(__file__).resolve().parents[2] / "runtime" / "setup-runtime.ps1"
POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
ACTIVE_SERVERS = []
DIAGNOSTIC_LIMIT = 2000


def bounded_output(value, proxy_env=None):
    """Keep timeout evidence small and never print proxy credentials or commands."""
    if value is None:
        return "<no output>"
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
    secrets = []
    for address in (proxy_env or {}).values():
        secrets.append(address)
        try:
            uri = urlsplit(address if "://" in address else "http://" + address)
            secrets.extend(unquote(part) for part in (uri.username, uri.password) if part)
        except ValueError:
            pass
    for secret in sorted(filter(None, secrets), key=len, reverse=True):
        text = text.replace(secret, "<redacted>")
    if len(text) > DIAGNOSTIC_LIMIT:
        return text[:DIAGNOSTIC_LIMIT] + "\n<truncated>"
    return text


def timeout_evidence(error, proxy_env=None):
    servers = [server.snapshot() for server in ACTIVE_SERVERS]
    return (f"Bootstrap PowerShell timed out after {error.timeout}s; "
            f"local HTTP counters={servers}\n"
            f"stdout:\n{bounded_output(error.stdout, proxy_env)}\n"
            f"stderr:\n{bounded_output(error.stderr, proxy_env)}")


def ps_string(value):
    return "'" + str(value).replace("'", "''") + "'"


def powershell_setup():
    # Loading the file itself would execute the installer. Parse its top-level
    # function definitions instead, so every request in these tests is local.
    return """
[Console]::Error.WriteLine('[bootstrap-test] powershell-started')
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
# This fixture only needs a .NET constructor. New-Object would first autoload
# Microsoft.PowerShell.Utility, scanning the caller's inherited module paths.
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::Error.WriteLine('[bootstrap-test] encoding-ready')
$Invariant = [Globalization.CultureInfo]::InvariantCulture
$tokens = $null
$parseErrors = $null
[Console]::Error.WriteLine('[bootstrap-test] parse-started')
$ast = [System.Management.Automation.Language.Parser]::ParseFile(%s, [ref]$tokens, [ref]$parseErrors)
[Console]::Error.WriteLine('[bootstrap-test] script-parsed')
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
foreach ($definition in $ast.EndBlock.Statements) {
    if ($definition -is [System.Management.Automation.Language.FunctionDefinitionAst]) {
        [Console]::Error.WriteLine('[bootstrap-test] defining-function=' + $definition.Name)
        . ([scriptblock]::Create($definition.Extent.Text))
    }
}
[Console]::Error.WriteLine('[bootstrap-test] functions-loaded')
""" % ps_string(SCRIPT)


def run_powershell(body, proxy_env=None):
    setup = powershell_setup()
    command = (setup + "\n[Console]::Error.WriteLine('[bootstrap-test] call-started')\n" + body
               + "\n[Console]::Error.WriteLine('[bootstrap-test] call-finished')\n")
    # -EncodedCommand takes base64-encoded UTF-16LE, independent of the console
    # code page and safe for Windows paths containing quotes or non-ASCII text.
    encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
    env = os.environ.copy()
    for name in list(env):
        if name.lower() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}:
            del env[name]
    env.update(proxy_env or {})
    try:
        return subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", encoded],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=30,
        )
    except subprocess.TimeoutExpired as error:
        # TimeoutExpired's default traceback prints the large encoded command but
        # hides captured phase/output evidence. Keep the same timeout and fail.
        raise AssertionError(timeout_evidence(error, proxy_env)) from None


class LocalHTTPServer(http.server.ThreadingHTTPServer):
    # HTTPServer enables SO_REUSEADDR. On Windows that allows two live listeners
    # to claim the same address, sending a proxy request to the wrong fixture.
    allow_reuse_address = False

    def __init__(self, *args, **kwargs):
        self.accepted = 0
        self.requests = 0
        self.counter_lock = threading.Lock()
        super().__init__(*args, **kwargs)

    def get_request(self):
        connection = super().get_request()
        with self.counter_lock:
            self.accepted += 1
        return connection

    def snapshot(self):
        with self.counter_lock:
            return {"port": self.server_port, "accepted": self.accepted, "requests": self.requests}

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class LocalServer:
    def __init__(self, handler):
        self.server = LocalHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        ACTIVE_SERVERS.append(self.server)
        self.thread.start()
        return self

    def __exit__(self, *_):
        try:
            self.server.shutdown()
            self.thread.join(timeout=5)
            self.server.server_close()
        finally:
            ACTIVE_SERVERS.remove(self.server)


class QuietHandler(http.server.BaseHTTPRequestHandler):
    def parse_request(self):
        valid = super().parse_request()
        if valid:
            with self.server.counter_lock:
                self.server.requests += 1
        return valid

    def log_message(self, *_):
        pass


class RuntimeBootstrapDiagnostics(unittest.TestCase):
    def test_timeout_keeps_bounded_phases_and_local_counters_without_command_or_secrets(self):
        secret = "fake:proxy-password"
        address = "http://test-user:fake%3Aproxy-password@127.0.0.1:5515"
        failure = subprocess.TimeoutExpired(
            ["powershell", "-EncodedCommand", "DO-NOT-PRINT-COMMAND"], 30,
            output=("progress " + secret + "x" * 5000).encode(),
            stderr=("[bootstrap-test] powershell-started\n"
                    "[bootstrap-test] functions-loaded\n"
                    "[bootstrap-test] call-started\n" + address).encode(),
        )
        with LocalServer(QuietHandler) as server:
            with server.server.counter_lock:
                server.server.accepted = 2
                server.server.requests = 1
            with patch("subprocess.run", side_effect=failure) as run:
                with self.assertRaises(AssertionError) as raised:
                    run_powershell("throw 'unused'", {"HTTP_PROXY": address})
            self.assertEqual(run.call_args.kwargs["timeout"], 30)
            command = base64.b64decode(run.call_args.args[0][-1]).decode("utf-16le")
            for phase in ("powershell-started", "functions-loaded", "call-started", "call-finished"):
                self.assertIn(f"[bootstrap-test] {phase}", command)
        message = str(raised.exception)
        self.assertIn("accepted': 2", message)
        self.assertIn("requests': 1", message)
        self.assertIn("[bootstrap-test] call-started", message)
        self.assertIn("<truncated>", message)
        self.assertIn("<redacted>", message)
        self.assertNotIn(secret, message)
        self.assertNotIn(address, message)
        self.assertNotIn("test-user", message)
        self.assertNotIn("DO-NOT-PRINT-COMMAND", message)
        self.assertLess(len(message), 2 * DIAGNOSTIC_LIMIT + 500)
        self.assertEqual(ACTIVE_SERVERS, [])

    def test_cold_start_timeout_without_output_is_explicit(self):
        failure = subprocess.TimeoutExpired(["powershell", "secret-command"], 30)
        with patch("subprocess.run", side_effect=failure):
            with self.assertRaisesRegex(AssertionError, "stdout:\\n<no output>\\nstderr:\\n<no output>"):
                run_powershell("throw 'unused'")


@unittest.skipUnless(os.name == "nt" and Path(POWERSHELL).is_file(),
                     "requires Windows PowerShell 5.1")
class RuntimeBootstrapDownload(unittest.TestCase):
    def test_function_loader_does_not_require_module_autoload(self):
        # A fresh PowerShell process has not loaded Microsoft.PowerShell.Utility.
        # Loading definitions must not start module discovery before Get-Pinned
        # is even called. The product functions keep their normal dependencies.
        setup = "$PSModuleAutoLoadingPreference = 'None'\n" + powershell_setup()
        with patch(f"{__name__}.powershell_setup", return_value=setup):
            result = run_powershell("Write-Line ([Console]::Out) 'functions ready'")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(result.stdout.strip(), "functions ready")
        for phase in ("encoding-ready", "parse-started", "script-parsed", "functions-loaded", "call-finished"):
            self.assertIn(f"[bootstrap-test] {phase}", result.stderr)

    def test_concurrent_proxy_and_origin_have_distinct_exclusive_ports(self):
        with LocalServer(QuietHandler) as origin, LocalServer(QuietHandler) as proxy:
            self.assertNotEqual(origin.server.server_port, proxy.server.server_port)
            if os.name == "nt":
                with socket.socket() as intruder:
                    intruder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    with self.assertRaises(OSError):
                        intruder.bind(origin.server.server_address)

    def download(self, url, destination, payload, proxy_env=None):
        return run_powershell(
            "Get-Pinned %s %s %s 'download-test'" % (
                ps_string(url), ps_string(destination),
                ps_string(hashlib.sha256(payload).hexdigest())),
            proxy_env,
        )

    def assert_downloaded(self, result, destination, payload):
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(destination.read_bytes(), payload)
        self.assertFalse(Path(str(destination) + ".part").exists())

    def test_environment_proxy_routes_actual_download(self):
        payload = b"through the configured proxy"
        requests = []

        class Proxy(QuietHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        with LocalServer(Proxy) as proxy, tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "İzmir çalışma"
            folder.mkdir()
            destination = folder / "uv.zip"
            url = "http://fairbeam-download.invalid/uv.zip"
            result = self.download(url, destination, payload, {"HTTP_PROXY": proxy.url})
            self.assert_downloaded(result, destination, payload)
            self.assertEqual(requests, [url])
            self.assertEqual(proxy.server.snapshot(), {
                "port": proxy.server.server_port, "accepted": 1, "requests": 1})

    def test_no_proxy_bypasses_environment_proxy(self):
        payload = b"direct local response"
        direct_requests = []
        proxy_requests = []

        class Origin(QuietHandler):
            def do_GET(self):
                direct_requests.append(self.path)
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        class Proxy(QuietHandler):
            def do_GET(self):
                proxy_requests.append(self.path)
                self.send_error(502)

        with LocalServer(Origin) as origin, LocalServer(Proxy) as proxy:
            with tempfile.TemporaryDirectory() as tmp:
                destination = Path(tmp) / "uv.zip"
                result = self.download(
                    origin.url + "/uv.zip", destination, payload,
                    {"HTTP_PROXY": proxy.url, "NO_PROXY": "127.0.0.1"},
                )
                self.assert_downloaded(result, destination, payload)
        self.assertEqual(direct_requests, ["/uv.zip"])
        self.assertEqual(proxy_requests, [])

    def test_proxy_selection_and_system_proxy_preservation(self):
        def selected(uri, env):
            result = run_powershell(
                "$uri = [Uri]%s\n"
                "$request = [System.Net.HttpWebRequest]::Create($uri)\n"
                "Set-DownloadProxy $request\n"
                "[Console]::Out.WriteLine($request.Proxy.GetProxy($uri).AbsoluteUri)\n"
                "$request.Abort()" % ps_string(uri), env)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            return result.stdout.strip()

        self.assertEqual(
            selected("https://download.example.test/uv.zip",
                     {"https_proxy": "http://127.0.0.1:5511"}),
            "http://127.0.0.1:5511/",
        )
        self.assertEqual(
            selected("http://download.example.test/uv.zip",
                     {"ALL_PROXY": "http://127.0.0.1:5512"}),
            "http://127.0.0.1:5512/",
        )
        self.assertEqual(
            selected("http://download.example.test/uv.zip",
                     {"HTTP_PROXY": "http://127.0.0.1:5513",
                      "ALL_PROXY": "http://127.0.0.1:5512"}),
            "http://127.0.0.1:5513/",
        )
        result = run_powershell(
            "$request = [System.Net.HttpWebRequest]::Create('http://download.example.test/')\n"
            "$original = New-Object System.Net.WebProxy 'http://127.0.0.1:5514'\n"
            "$request.Proxy = $original\n"
            "Set-DownloadProxy $request\n"
            "[Console]::Out.WriteLine([object]::ReferenceEquals($original, $request.Proxy))\n"
            "$request.Abort()")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(result.stdout.strip(), "True")
        result = run_powershell(
            "$uri = [Uri]'http://download.example.test/uv.zip'\n"
            "$request = [System.Net.HttpWebRequest]::Create($uri)\n"
            "Set-DownloadProxy $request\n"
            "$credential = $request.Proxy.Credentials.GetCredential($request.Proxy.Address, 'Basic')\n"
            "$valid = ($credential.UserName -eq 'user') -and "
            "($credential.Password -eq 'fake:password') -and "
            "(-not $request.Proxy.Address.UserInfo)\n"
            "[Console]::Out.WriteLine($valid)\n"
            "$request.Abort()",
            {"HTTP_PROXY": "http://user:fake%3Apassword@127.0.0.1:5515"},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(result.stdout.strip(), "True")

    def test_interrupted_transfer_retries_and_discards_partial_file(self):
        payload = b"complete payload after interrupted response"
        requests = []

        class Origin(QuietHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if len(requests) == 1:
                    self.wfile.write(payload[:7])
                    self.wfile.flush()
                    self.connection.shutdown(socket.SHUT_WR)
                else:
                    self.wfile.write(payload)

        with LocalServer(Origin) as origin, tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "uv.zip"
            Path(str(destination) + ".part").write_bytes(b"stale partial download")
            result = self.download(origin.url + "/uv.zip", destination, payload,
                                   {"NO_PROXY": "127.0.0.1"})
            self.assert_downloaded(result, destination, payload)
        self.assertEqual(requests, ["/uv.zip", "/uv.zip"])

    def test_hash_mismatch_does_not_retry(self):
        requests = []

        class Origin(QuietHandler):
            def do_GET(self):
                requests.append(self.path)
                payload = b"corrupt data"
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        with LocalServer(Origin) as origin, tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "uv.zip"
            result = self.download(origin.url + "/uv.zip", destination, b"expected data",
                                   {"NO_PROXY": "127.0.0.1"})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SHA-256 mismatch", result.stderr)
            self.assertFalse(destination.exists())
            self.assertFalse(Path(str(destination) + ".part").exists())
        self.assertEqual(requests, ["/uv.zip"])

    def test_verified_cache_is_reused_without_request(self):
        requests = []
        payload = b"previously verified payload"

        class Origin(QuietHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_error(500)

        with LocalServer(Origin) as origin, tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "uv.zip"
            destination.write_bytes(payload)
            Path(str(destination) + ".part").write_bytes(b"stale partial download")
            result = self.download(origin.url + "/uv.zip", destination, payload,
                                   {"NO_PROXY": "127.0.0.1"})
            self.assert_downloaded(result, destination, payload)
        self.assertEqual(requests, [])

    def test_transient_status_is_bounded_and_cleans_partial_file(self):
        requests = []

        class Origin(QuietHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_error(503)

        with LocalServer(Origin) as origin, tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "uv.zip"
            result = self.download(origin.url + "/uv.zip", destination, b"expected",
                                   {"NO_PROXY": "127.0.0.1"})
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(destination.exists())
            self.assertFalse(Path(str(destination) + ".part").exists())
        self.assertEqual(requests, ["/uv.zip"] * 3)

    def test_permanent_status_and_local_file_failure_do_not_retry(self):
        requests = []

        class Origin(QuietHandler):
            def do_GET(self):
                requests.append(self.path)
                if self.path == "/missing":
                    self.send_error(404)
                else:
                    self.send_response(200)
                    self.send_header("Content-Length", "4")
                    self.end_headers()
                    self.wfile.write(b"data")

        with LocalServer(Origin) as origin, tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "uv.zip"
            result = self.download(origin.url + "/missing", destination, b"data",
                                   {"NO_PROXY": "127.0.0.1"})
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(Path(str(destination) + ".part").exists())
            unavailable = Path(tmp) / "missing-parent" / "uv.zip"
            result = self.download(origin.url + "/data", unavailable, b"data",
                                   {"NO_PROXY": "127.0.0.1"})
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(Path(str(unavailable) + ".part").exists())
            self.assertIn("could not remove partial download", result.stderr)
        # The absent parent is detected while cleaning a stale .part, before any
        # network request. It must not enter the retry loop.
        self.assertEqual(requests, ["/missing"])

    def test_invalid_proxy_does_not_expose_credentials(self):
        requests = []

        class Origin(QuietHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_error(500)

        secret = "bootstrap-proxy-secret"
        with LocalServer(Origin) as origin, tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "uv.zip"
            result = self.download(
                origin.url + "/uv.zip", destination, b"expected",
                {"HTTP_PROXY": f"http://user:{secret}@127.0.0.1:notaport"},
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(destination.exists())
            self.assertFalse(Path(str(destination) + ".part").exists())
        self.assertEqual(requests, [])
        self.assertNotIn(secret, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
