"""Behavior tests for the Kalshi WebSocket server entry point.

All network interaction is mocked at the ``discover_markets`` /
``websockets.connect`` / ``_auth_headers`` / ``_sleep_until_next_scan``
seams via monkeypatch — these tests never open a socket or touch real key
material. Discovery itself is covered by tests/test_nba_scanner.py.
"""

import asyncio
import base64
import contextlib
import importlib
import json
import os
import select
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_persistence import FakeConn

import kalshi_testing  # noqa: F401  (ensures the original module object is restorable)
from kalshi_testing import server
from kalshi_testing.nba_scanner import NBA, NFL, EventRecord, MarketRecord
from kalshi_testing.server import DEFAULT_WS_URL, main

API_KEY_ID = "kalshi-test-key-id"
# Dummy PEM string, not a real key.
PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----\ndummy-not-a-real-key\n-----END PRIVATE KEY-----"


@pytest.fixture
def run_seam(monkeypatch: pytest.MonkeyPatch) -> list:
    """Replace the async _run seam with a recorder. The recorder raises
    KeyboardInterrupt after recording the call so the never-exiting supervise
    loop terminates (rc 0) after exactly one attempt."""

    calls: list = []

    async def fake_run(config, *args, **kwargs) -> None:
        calls.append(config)
        raise KeyboardInterrupt

    monkeypatch.setattr(server, "_run", fake_run)
    return calls


def _interrupt_first_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the reconnect backoff sleep so its first call raises
    KeyboardInterrupt — the supervise loop then returns 0 instead of
    retrying forever, letting a single dropped connection end the test."""

    async def interrupting_sleep(seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(server, "_sleep_for_reconnect", interrupting_sleep)


def _set_kalshi_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KALSHI_API_KEY_ID", API_KEY_ID)
    monkeypatch.setenv("KALSHI_PRIVATE_KEY", PRIVATE_KEY)


def _set_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_kalshi_credentials(monkeypatch)
    # Dummy Supabase config: nothing listens on 127.0.0.1:9, so a real
    # connection attempt refuses instantly instead of hanging.
    monkeypatch.setenv("SUPABASE_DB_URI", "postgresql://127.0.0.1:9/postgres")
    monkeypatch.setenv("SUPABASE_DB_PW", "dummy-not-a-real-password")


def _records(desired: dict) -> list[EventRecord]:
    """EventRecords matching a desired-dict scan result. No team data, so
    the Supabase persistence path skips them and these tests never need a
    database."""
    return [
        EventRecord(
            event_ticker=event_ticker,
            title=event_ticker,
            url=f"https://kalshi.com/markets/test/{event_ticker.lower()}",
            espn_start_ts=None,
            away_team=None,
            home_team=None,
            markets=tuple(MarketRecord(ticker, ticker, None) for ticker in tickers),
        )
        for event_ticker, tickers in desired.items()
    ]


def test_missing_api_key_id_exits_one_naming_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, run_seam: list
) -> None:
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY", raising=False)
    rc = main()
    captured = capsys.readouterr()
    assert rc == 1
    assert "KALSHI_API_KEY_ID" in captured.err
    assert captured.out == ""
    assert run_seam == []  # no connection attempted without credentials


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_or_blank_supabase_db_uri_exits_one_naming_variable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    run_seam: list,
    value: str | None,
) -> None:
    """AC: a missing SUPABASE_DB_URI is a visible startup failure — the
    server never starts and never silently skips persistence."""
    _set_kalshi_credentials(monkeypatch)
    if value is None:
        monkeypatch.delenv("SUPABASE_DB_URI", raising=False)
    else:
        monkeypatch.setenv("SUPABASE_DB_URI", value)
    rc = main()
    captured = capsys.readouterr()
    assert rc == 1
    assert "SUPABASE_DB_URI" in captured.err
    assert captured.out == ""
    assert run_seam == []  # no connection attempted without the DB config


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_or_blank_supabase_db_password_exits_one_naming_variable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    run_seam: list,
    value: str | None,
) -> None:
    _set_kalshi_credentials(monkeypatch)
    monkeypatch.setenv("SUPABASE_DB_URI", "postgresql://127.0.0.1:9/postgres")
    if value is None:
        monkeypatch.delenv("SUPABASE_DB_PW", raising=False)
    else:
        monkeypatch.setenv("SUPABASE_DB_PW", value)
    rc = main()
    captured = capsys.readouterr()
    assert rc == 1
    assert "SUPABASE_DB_PW" in captured.err
    assert captured.out == ""
    assert run_seam == []


def test_plaintext_ws_url_rejected_before_connecting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, run_seam: list
) -> None:
    _set_credentials(monkeypatch)
    monkeypatch.setenv("KALSHI_WS_URL", "ws://localhost:9999/ws")
    rc = main()
    captured = capsys.readouterr()
    assert rc == 1
    assert "wss://" in captured.err
    assert run_seam == []  # no connection attempted with a plaintext URL


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_or_blank_private_key_takes_the_same_error_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    run_seam: list,
    value: str | None,
) -> None:
    monkeypatch.setenv("KALSHI_API_KEY_ID", API_KEY_ID)
    if value is None:
        monkeypatch.delenv("KALSHI_PRIVATE_KEY", raising=False)
    else:
        monkeypatch.setenv("KALSHI_PRIVATE_KEY", value)
    rc = main()
    captured = capsys.readouterr()
    assert rc == 1
    assert "KALSHI_PRIVATE_KEY" in captured.err
    assert captured.out == ""
    assert run_seam == []


def test_credentials_present_runs_server_with_those_exact_values(
    monkeypatch: pytest.MonkeyPatch, run_seam: list
) -> None:
    _set_credentials(monkeypatch)
    monkeypatch.delenv("KALSHI_WS_URL", raising=False)
    rc = main()
    assert rc == 0
    assert len(run_seam) == 1
    config = run_seam[0]
    assert config.api_key_id == API_KEY_ID
    assert config.private_key == PRIVATE_KEY
    assert config.ws_url == DEFAULT_WS_URL


def test_keyboard_interrupt_shuts_down_cleanly(
    monkeypatch: pytest.MonkeyPatch, run_seam: list
) -> None:
    async def interrupted(config, *args, **kwargs) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(server, "_run", interrupted)
    _set_credentials(monkeypatch)
    assert main() == 0  # Ctrl+C -> clean exit 0


def test_connection_failure_retries_and_never_exits_without_leaking_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """AC1/AC4: a connection failure is logged on stdout with the attempt
    number and error message, and the supervise loop retries instead of
    exiting the process."""

    async def failing(config, *args, **kwargs) -> None:
        raise ConnectionError("handshake refused")

    monkeypatch.setattr(server, "_run", failing)
    _interrupt_first_reconnect(monkeypatch)
    _set_credentials(monkeypatch)
    rc = main()
    captured = capsys.readouterr()
    assert rc == 0
    assert "connection attempt 1 failed: handshake refused" in captured.out
    # The private key may never leak into any output stream.
    assert PRIVATE_KEY not in captured.err
    assert PRIVATE_KEY not in captured.out


def test_auth_headers_ed25519_signature_verifies() -> None:
    """An Ed25519 credential must sign with the single-argument Ed25519
    convention; the produced signature must verify against the public key.
    This fails with TypeError if sign() is called with >1 positional arg."""
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    headers = server._auth_headers("test-key-id", pem)
    message = f"{headers['KALSHI-ACCESS-TIMESTAMP']}GET/trade-api/ws/v2".encode()
    signature = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    key.public_key().verify(signature, message)  # raises InvalidSignature on mismatch
    assert headers["KALSHI-ACCESS-KEY"] == "test-key-id"


def test_cli_missing_credentials_exits_one_and_names_variable(tmp_path) -> None:
    """AC2 through the real __main__ path: no credentials -> exit 1, stderr names it."""
    env = dict(os.environ)
    env.pop("KALSHI_API_KEY_ID", None)
    env.pop("KALSHI_PRIVATE_KEY", None)
    result = subprocess.run(
        [sys.executable, "-m", "kalshi_testing.server"],
        cwd=tmp_path,  # no .env here
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert "KALSHI_API_KEY_ID" in result.stderr
    assert result.stdout == ""


def test_server_close_logs_connected_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Exercise the real _run: 'connected' is logged; a server-side close is
    logged as a failed attempt on stdout and the supervise loop reconnects
    instead of exiting."""

    class _FakeWs:
        async def send(self, data: str) -> None:
            pass  # subscribe frames are not under test here

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration  # server closes immediately

    class _FakeConnect:
        def __init__(self, url: str, additional_headers: dict) -> None:
            self.url = url
            self.additional_headers = additional_headers

        async def __aenter__(self) -> _FakeWs:
            return _FakeWs()

        async def __aexit__(self, *exc) -> bool:
            return False

    monkeypatch.setattr(server.websockets, "connect", _FakeConnect)
    monkeypatch.setattr(server, "_auth_headers", lambda *args: {})
    monkeypatch.setattr(server, "discover_events", lambda *args: _records({"KXE-1": ["MKT-TEST"]}))
    _interrupt_first_reconnect(monkeypatch)
    _set_credentials(monkeypatch)
    rc = main()
    captured = capsys.readouterr()
    assert f"connected to {DEFAULT_WS_URL}" in captured.out
    assert rc == 0  # the drop triggers a supervised reconnect, not an exit
    assert "connection attempt 1 failed" in captured.out
    assert "closed" in captured.out
    assert PRIVATE_KEY not in captured.err
    assert PRIVATE_KEY not in captured.out


def test_import_ignores_dotenv_planted_in_cwd(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A .env planted in an untrusted cwd must never be loaded — only the
    repo-root .env is a trusted credential source."""
    sentinel = "KALSHI_DOTENV_TEST_SENTINEL"
    (tmp_path / ".env").write_text(f"{sentinel}=planted\n")
    monkeypatch.delenv(sentinel, raising=False)
    monkeypatch.chdir(tmp_path)
    original = sys.modules.pop("kalshi_testing")
    try:
        importlib.import_module("kalshi_testing")
        assert sentinel not in os.environ
    finally:
        sys.modules["kalshi_testing"] = original


# --- Direct-script .env loading (HIP-17): running server.py as a script must
# load the repo-root .env itself, because the package __init__ never runs. ---

_DOTENV_CREDENTIALS = (
    "KALSHI_API_KEY_ID=dotenv-test-key-id\n"
    "KALSHI_PRIVATE_KEY=dotenv-dummy-key\n"
    # Nothing listens on port 9 on localhost, so a real connection attempt
    # refuses instantly instead of hanging.
    "KALSHI_WS_URL=wss://127.0.0.1:9/ws\n"
    "SUPABASE_DB_URI=postgresql://127.0.0.1:9/postgres\n"
    "SUPABASE_DB_PW=dotenv-dummy-password\n"
)


def _clean_env() -> dict[str, str]:
    """os.environ without the Kalshi/Supabase variables under test."""
    env = dict(os.environ)
    for var in (
        "KALSHI_API_KEY_ID",
        "KALSHI_PRIVATE_KEY",
        "KALSHI_WS_URL",
        "SUPABASE_DB_URI",
        "SUPABASE_DB_PW",
    ):
        env.pop(var, None)
    return env


def _repo_root() -> Path:
    for directory in Path(__file__).resolve().parents:
        if (directory / "pyproject.toml").exists():
            return directory
    pytest.skip("repo root (pyproject.toml) not found above tests/")


@contextlib.contextmanager
def _repo_root_dotenv(contents: str):
    """Temporarily write a repo-root .env; never touches a real one."""
    env_path = _repo_root() / ".env"
    if env_path.exists():
        pytest.skip("real repo-root .env present — refusing to touch it")
    env_path.write_text(contents)
    try:
        yield
    finally:
        env_path.unlink()


def _server_script() -> Path:
    return _repo_root() / "src" / "kalshi_testing" / "server.py"


def _assert_stays_up_after_connection_refusal(command: list, cwd: Path) -> None:
    """AC1 end-to-end: a refused connection must not exit the process — the
    supervise loop keeps retrying, visible as connection-attempt failure
    lines on stdout, until the process is terminated."""
    with _repo_root_dotenv(_DOTENV_CREDENTIALS):
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            env=_clean_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert proc.stdout is not None
            data = ""
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and "failed:" not in data:
                readable, _, _ = select.select([proc.stdout], [], [], 1)
                if readable:
                    chunk = proc.stdout.read(1)
                    if chunk == "" and proc.poll() is not None:
                        break  # process exited; fail with what we captured
                    data += chunk
            assert proc.poll() is None, (
                f"connection failure must not exit the process; stdout so far: {data!r}"
            )
            # Past the credential guardrail (rc 1 would mean config failure)
            # and retrying the refused connection.
            assert "connection attempt 1" in data
            assert "failed:" in data
        finally:
            proc.terminate()
            proc.wait(timeout=30)


def test_direct_script_from_package_dir_loads_repo_root_dotenv() -> None:
    """AC1: `cd src/kalshi_testing && python server.py` with a repo-root .env
    loads it (no missing-credential error) and stays up retrying the refused
    connection instead of exiting."""
    _assert_stays_up_after_connection_refusal(
        [sys.executable, "server.py"], _server_script().parent
    )


def test_direct_script_from_other_cwd_loads_repo_root_dotenv(tmp_path) -> None:
    """AC2 (PyCharm simulation): running server.py by absolute path with a
    non-repo-root working directory still loads the repo-root .env, and the
    refused connection never exits the process."""
    _assert_stays_up_after_connection_refusal([sys.executable, str(_server_script())], tmp_path)


def test_direct_script_without_repo_root_dotenv_still_fails_guardrail() -> None:
    """AC4: with no repo-root .env, direct-script invocation still exits 1
    naming KALSHI_API_KEY_ID — same behavior as the -m path."""
    if (_repo_root() / ".env").exists():
        pytest.skip("real repo-root .env present — would be loaded")
    result = subprocess.run(
        [sys.executable, "server.py"],
        cwd=_server_script().parent,
        env=_clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert "KALSHI_API_KEY_ID" in result.stderr
    assert result.stdout == ""


# --- HIP-19: event market discovery + per-market trade subscriptions ---


def test_load_config_rest_url_defaults_to_demo() -> None:
    config = server.load_config(
        {"KALSHI_API_KEY_ID": API_KEY_ID, "KALSHI_PRIVATE_KEY": PRIVATE_KEY}
    )
    assert config.rest_url == server.DEFAULT_REST_URL


def test_load_config_rest_url_override() -> None:
    config = server.load_config(
        {
            "KALSHI_API_KEY_ID": API_KEY_ID,
            "KALSHI_PRIVATE_KEY": PRIVATE_KEY,
            "KALSHI_REST_URL": "https://kalshi-demo.example.test/trade-api/v2",
        }
    )
    assert config.rest_url == "https://kalshi-demo.example.test/trade-api/v2"


def test_load_config_rejects_plaintext_rest_url() -> None:
    with pytest.raises(ValueError, match="https://"):
        server.load_config(
            {
                "KALSHI_API_KEY_ID": API_KEY_ID,
                "KALSHI_PRIVATE_KEY": PRIVATE_KEY,
                "KALSHI_REST_URL": "http://demo-api.kalshi.co/trade-api/v2",
            }
        )


class _RecordingWs:
    """Fake websocket: records sent frames, replays scripted incoming messages.

    ``empty_gate``: when the scripted incoming frames are exhausted, __anext__
    waits on this asyncio.Event instead of closing immediately — letting a
    background task (the re-scan loop) run before the fake server "closes".
    """

    def __init__(self, incoming: tuple = (), empty_gate: asyncio.Event | None = None) -> None:
        self.sent: list[str] = []
        self._incoming = list(incoming)
        self._empty_gate = empty_gate

    async def send(self, data: str) -> None:
        self.sent.append(data)

    def __aiter__(self) -> "_RecordingWs":
        return self

    async def __anext__(self) -> str:
        if not self._incoming:
            if self._empty_gate is not None:
                await self._empty_gate.wait()
            raise StopAsyncIteration  # server closes the connection
        return self._incoming.pop(0)


class _FakeConnect:
    """websockets.connect stand-in returning a scripted _RecordingWs."""

    def __init__(self, ws: _RecordingWs) -> None:
        self._ws = ws

    def __call__(self, url: str, additional_headers: dict) -> "_FakeConnect":
        return self

    async def __aenter__(self) -> _RecordingWs:
        return self._ws

    async def __aexit__(self, *exc) -> bool:
        return False


def _stub_sample_rng(monkeypatch: pytest.MonkeyPatch, draw: float) -> None:
    """Fix the raw-trade sampling draw: below RAW_TRADE_SAMPLE_RATE (0.05)
    every trade's raw payload prints, above it none does."""
    monkeypatch.setattr(server.random, "random", lambda: draw)


def _stub_market_seams(monkeypatch: pytest.MonkeyPatch, desired: dict, ws: _RecordingWs) -> None:
    monkeypatch.setattr(server, "discover_events", lambda *args: _records(desired))
    monkeypatch.setattr(server.websockets, "connect", _FakeConnect(ws))
    monkeypatch.setattr(server, "_auth_headers", lambda *args: {})
    _stub_sample_rng(monkeypatch, 0.0)  # always sample: raw payloads print deterministically
    _set_credentials(monkeypatch)


def test_one_trade_subscription_frame_per_discovered_market(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC2: exactly one subscribe frame per discovered market, each
    on the trade channel for that single ticker — and nothing else is subscribed."""
    markets = ["MKT-AAA", "MKT-BBB", "MKT-CCC"]
    ws = _RecordingWs()
    _stub_market_seams(monkeypatch, {"KXNBAGAME-TEST": markets}, ws)
    _interrupt_first_reconnect(monkeypatch)
    rc = main()
    assert rc == 0  # fake ws closes -> supervised reconnect, not an exit
    frames = [json.loads(frame) for frame in ws.sent]
    assert len(frames) == len(markets)
    subscribed = []
    for frame in frames:
        assert frame["cmd"] == "subscribe"
        assert frame["params"]["channels"] == ["trade"]
        assert len(frame["params"]["market_tickers"]) == 1
        subscribed.append(frame["params"]["market_tickers"][0])
    assert sorted(subscribed) == sorted(markets)  # the discovered markets, nothing else
    assert len({frame["id"] for frame in frames}) == len(markets)  # unique request ids


def test_scan_receives_the_configured_api_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HIP-23: _run authenticates the startup REST scan with the same
    configured credentials as the WebSocket handshake. HIP-34: the startup
    scan runs once per league (NBA, NFL), each with those credentials."""
    scan_calls: list = []

    def recording_scan(rest_url: str, now, api_key_id: str, private_key_pem: str, league) -> list:
        scan_calls.append((rest_url, api_key_id, private_key_pem, league))
        return []

    ws = _RecordingWs()
    monkeypatch.setattr(server, "discover_events", recording_scan)
    monkeypatch.setattr(server.websockets, "connect", _FakeConnect(ws))
    monkeypatch.setattr(server, "_auth_headers", lambda *args: {})
    _interrupt_first_reconnect(monkeypatch)
    _set_credentials(monkeypatch)
    rc = main()
    assert rc == 0  # fake ws closes -> supervised reconnect, not an exit
    assert scan_calls == [
        (server.DEFAULT_REST_URL, API_KEY_ID, PRIVATE_KEY, NBA),
        (server.DEFAULT_REST_URL, API_KEY_ID, PRIVATE_KEY, NFL),
    ]


def _gate_releasing_sleeper(
    monkeypatch: pytest.MonkeyPatch, gate: asyncio.Event, rescans_before_stop: int = 1
) -> list:
    """Patch the re-scan delay so each of the first ``rescans_before_stop``
    waits yields once (letting the message loop process frames) and the next
    wait opens the gate then cancels the re-scan loop — ending the run
    deterministically."""
    waits: list = []

    async def fake_sleep() -> None:
        waits.append(1)
        if len(waits) <= rescans_before_stop:
            await asyncio.sleep(0)
            return
        gate.set()
        raise asyncio.CancelledError

    monkeypatch.setattr(server, "_sleep_until_next_scan", fake_sleep)
    return waits


def test_startup_scan_failure_is_logged_and_rescan_still_runs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """AC5: a startup REST failure is logged, the WS still connects, and the
    scheduled re-scan still runs (it recovers and subscribes here)."""

    def flaky_scan(rest_url: str, now, *_credentials):
        if not hasattr(flaky_scan, "called"):
            flaky_scan.called = True
            raise RuntimeError("Kalshi REST request for series KXNBAGAME failed: HTTP 500")
        return _records({"KXNBAGAME-EV1": ["MKT-NEW"]})

    gate = asyncio.Event()
    ws = _RecordingWs(empty_gate=gate)
    waits = _gate_releasing_sleeper(monkeypatch, gate)
    monkeypatch.setattr(server, "discover_events", flaky_scan)
    monkeypatch.setattr(server.websockets, "connect", _FakeConnect(ws))
    monkeypatch.setattr(server, "_auth_headers", lambda *args: {})
    _interrupt_first_reconnect(monkeypatch)
    _set_credentials(monkeypatch)
    rc = main()
    captured = capsys.readouterr()
    assert rc == 0  # fake ws closes -> supervised reconnect, not an exit
    assert "NBA market scan failed" in captured.err
    assert "KXNBAGAME" in captured.err
    assert waits, "the re-scan loop must still be scheduled after a startup scan failure"
    frames = [json.loads(frame) for frame in ws.sent]
    subscribed = [f["params"]["market_tickers"][0] for f in frames if f["cmd"] == "subscribe"]
    assert subscribed == ["MKT-NEW"]  # re-scan recovered and subscribed


def test_rescan_failure_is_logged_and_loop_continues(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """AC5: a failed re-scan is logged and the next scan still runs. The
    scripted results are consumed one call per league per scan (HIP-34)."""
    scans = iter(
        [
            {"KXNBAGAME-EV1": ["MKT-A"]},  # startup: NBA
            {"KXNBAGAME-EV1": ["MKT-A"]},  # startup: NFL
            RuntimeError("HTTP 503"),  # re-scan 1: NBA fails
            {"KXNBAGAME-EV1": ["MKT-A"]},  # re-scan 1: NFL
            {"KXNBAGAME-EV1": ["MKT-A", "MKT-B"]},  # re-scan 2: NBA recovers
            {"KXNBAGAME-EV1": ["MKT-A", "MKT-B"]},  # re-scan 2: NFL
        ]
    )

    def fake_scan(rest_url: str, now, *_credentials):
        # Steady state after the scripted scans: an exhausted iterator would
        # raise StopIteration, which the rescan loop retries forever.
        result = next(scans, {"KXNBAGAME-EV1": ["MKT-A", "MKT-B"]})
        if isinstance(result, Exception):
            raise result
        return _records(result)

    gate = asyncio.Event()
    confirmation = json.dumps({"type": "subscribed", "id": 1, "msg": {"sids": ["sid-a"]}})
    ws = _RecordingWs(incoming=(confirmation,), empty_gate=gate)
    waits = _gate_releasing_sleeper(monkeypatch, gate, rescans_before_stop=2)
    monkeypatch.setattr(server, "discover_events", fake_scan)
    monkeypatch.setattr(server.websockets, "connect", _FakeConnect(ws))
    monkeypatch.setattr(server, "_auth_headers", lambda *args: {})
    _interrupt_first_reconnect(monkeypatch)
    _set_credentials(monkeypatch)
    rc = main()
    captured = capsys.readouterr()
    assert rc == 0  # fake ws closes -> supervised reconnect, not an exit
    assert waits == [1, 1, 1]  # all three scheduled scans ran
    assert "NBA market re-scan failed" in captured.err
    frames = [json.loads(frame) for frame in ws.sent]
    subscribed = [f["params"]["market_tickers"][0] for f in frames if f["cmd"] == "subscribe"]
    assert subscribed == ["MKT-A", "MKT-B"]  # post-failure scan still subscribed B


def test_rescan_subscribes_new_markets_and_unsubscribes_stale(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """AC3/AC2: first scan yields {A, B}, the re-scan yields {B, C} — one
    subscribe frame for C, one unsubscribe frame for A's confirmed sid, all
    logged, and the surviving market B is left untouched. The scripted
    results are consumed one call per league per scan (HIP-34); the NFL
    scans return nothing here."""
    scans = iter(
        [
            {"E": ["MKT-A", "MKT-B"]},  # startup: NBA
            {},  # startup: NFL
            {"E": ["MKT-B", "MKT-C"]},  # re-scan: NBA
            {},  # re-scan: NFL
        ]
    )
    confirmations = (
        json.dumps({"type": "subscribed", "id": 1, "msg": {"sids": ["sid-a"]}}),
        json.dumps({"type": "subscribed", "id": 2, "msg": {"sids": ["sid-b"]}}),
    )
    gate = asyncio.Event()
    ws = _RecordingWs(incoming=confirmations, empty_gate=gate)
    _gate_releasing_sleeper(monkeypatch, gate)
    # Steady state after the scripted scans: an exhausted iterator would
    # raise StopIteration, which the rescan loop retries forever.
    steady_state = {"E": ["MKT-B", "MKT-C"]}
    monkeypatch.setattr(
        server, "discover_events", lambda *args: _records(next(scans, steady_state))
    )
    monkeypatch.setattr(server.websockets, "connect", _FakeConnect(ws))
    monkeypatch.setattr(server, "_auth_headers", lambda *args: {})
    _interrupt_first_reconnect(monkeypatch)
    _set_credentials(monkeypatch)
    rc = main()
    captured = capsys.readouterr()
    assert rc == 0  # fake ws closes -> supervised reconnect, not an exit
    # AC2: subscription confirmations are logged with ticker and sid.
    assert "subscription confirmed for market MKT-A (sid: sid-a)" in captured.out
    assert "subscription confirmed for market MKT-B (sid: sid-b)" in captured.out
    frames = [json.loads(frame) for frame in ws.sent]
    subscribed = [f["params"]["market_tickers"][0] for f in frames if f["cmd"] == "subscribe"]
    assert subscribed == ["MKT-A", "MKT-B", "MKT-C"]  # C picked up by the re-scan
    unsubscribes = [f for f in frames if f["cmd"] == "unsubscribe"]
    assert len(unsubscribes) == 1
    assert unsubscribes[0]["params"]["sids"] == ["sid-a"]  # A removed by sid, B kept
    # AC3: both actions visible in logs, attributed to the right market.
    assert "subscribing to trades for market MKT-C" in captured.out
    assert "unsubscribing from market MKT-A (sid: sid-a)" in captured.out
    assert "re-scan complete" in captured.out


def test_subscription_error_logged_with_ticker_and_others_continue(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """AC5: an error frame for one subscription names that market's ticker, all
    subscribe frames still went out, and a later trade is still processed."""
    markets = ["MKT-AAA", "MKT-BBB"]
    error_frame = json.dumps({"type": "error", "id": 1, "msg": {"code": 5, "msg": "nope"}})
    trade_frame = json.dumps(
        {"type": "trade", "msg": {"market_ticker": "MKT-BBB", "yes_price": 42, "count": 7}}
    )
    ws = _RecordingWs(incoming=(error_frame, trade_frame))
    _stub_market_seams(monkeypatch, {"KXE-1": markets}, ws)
    _interrupt_first_reconnect(monkeypatch)
    rc = main()
    captured = capsys.readouterr()
    assert rc == 0  # fake ws closes afterwards -> supervised reconnect, not an exit
    assert "MKT-AAA" in captured.err  # error attributed to the rejected market
    frames = [json.loads(frame) for frame in ws.sent]
    assert [frame["params"]["market_tickers"][0] for frame in frames] == markets
    assert trade_frame in captured.out  # other subscriptions unaffected


def test_trade_message_prints_full_raw_payload(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """AC3: every trade frame's full raw payload lands on stdout."""
    raw_trade = json.dumps(
        {
            "type": "trade",
            "msg": {"trade_id": "t-1", "market_ticker": "MKT-AAA", "yes_price": 55, "count": 3},
        }
    )
    ws = _RecordingWs(incoming=(raw_trade,))
    _stub_market_seams(monkeypatch, {"KXE-1": ["MKT-AAA"]}, ws)
    _interrupt_first_reconnect(monkeypatch)
    rc = main()
    captured = capsys.readouterr()
    assert rc == 0  # fake ws closes -> supervised reconnect, not an exit
    assert raw_trade in captured.out


# --- HIP-21: auto-reconnect on connection drops ---


def test_reconnect_backoff_doubles_and_caps_at_thirty_seconds() -> None:
    """AC3: the backoff sequence folds 1 -> 2 -> 4 -> 8 -> 16 -> 30 -> 30 ..."""
    delay = server.INITIAL_RECONNECT_DELAY_S
    delays: list[float] = []
    for _ in range(8):
        delays.append(delay)
        delay = server._next_delay(delay)
    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0]


@pytest.mark.parametrize(
    ("clock_values", "expected_delays"),
    [([0.0, 1.0, 1.0, 2.0], [1.0, 2.0]), ([0.0, 1.0, 1.0, 62.0], [1.0, 1.0])],
)
def test_reconnect_backoff_resets_after_sixty_seconds_established(
    monkeypatch: pytest.MonkeyPatch, clock_values: list, expected_delays: list
) -> None:
    """AC3: a drop after >=60s connected resets the backoff to 1s; a quicker
    drop doubles it. Driven through _supervise with a fake clock — no real
    sleeping or network."""
    delays: list[float] = []

    async def scripted_run(config, db_config, **kwargs) -> None:
        on_connected = kwargs.get("on_connected")
        if on_connected is not None:
            on_connected()  # every attempt establishes, then drops
        raise ConnectionError("dropped")

    async def recording_sleep(seconds: float) -> None:
        delays.append(seconds)
        if len(delays) >= 2:
            raise KeyboardInterrupt  # end the never-exiting loop, as Ctrl+C would
        await asyncio.sleep(0)

    # Clock readings: connect1, drop1, connect2, drop2. When the second
    # connection lives less than 60s the second delay doubles (1.0 -> 2.0);
    # a long-lived connection resets it to 1.0.
    times = iter(clock_values)
    monkeypatch.setattr(server, "_run", scripted_run)
    monkeypatch.setattr(server, "_sleep_for_reconnect", recording_sleep)
    config = server.load_config(
        {"KALSHI_API_KEY_ID": API_KEY_ID, "KALSHI_PRIVATE_KEY": PRIVATE_KEY}
    )
    db_config = server.load_db_config(
        {"SUPABASE_DB_URI": "postgresql://127.0.0.1:9/postgres", "SUPABASE_DB_PW": "dummy"}
    )
    try:
        rc = asyncio.run(server._supervise(config, db_config, clock=lambda: next(times)))
    except KeyboardInterrupt:
        rc = 0  # same contract as main(): Ctrl+C ends the supervise loop cleanly
    assert rc == 0
    assert delays == expected_delays


def test_drop_triggers_reauth_and_full_resubscribe(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """AC1/AC2: a dropped connection reconnects through the full startup path
    — fresh auth headers, fresh subscribe frames per market — and trade
    payloads print again after recovery."""
    markets = ["MKT-AAA", "MKT-BBB"]
    first_trade = json.dumps({"type": "trade", "msg": {"market_ticker": "MKT-AAA"}})
    second_trade = json.dumps({"type": "trade", "msg": {"market_ticker": "MKT-BBB"}})
    ws1 = _RecordingWs(incoming=(first_trade,))
    ws2 = _RecordingWs(incoming=(second_trade,))
    sockets = [ws1, ws2]

    class _ScriptedConnect:
        """websockets.connect stand-in returning a fresh ws per attempt."""

        def __call__(self, url: str, additional_headers: dict) -> _FakeConnect:
            return _FakeConnect(sockets.pop(0))

    auth_calls: list = []

    def recording_auth(*args) -> dict:
        auth_calls.append(args)
        return {}

    delays: list[float] = []

    async def interrupting_sleep(seconds: float) -> None:
        delays.append(seconds)
        if len(delays) >= 2:
            raise KeyboardInterrupt
        await asyncio.sleep(0)

    monkeypatch.setattr(server, "discover_events", lambda *args: _records({"KXE-1": markets}))
    monkeypatch.setattr(server.websockets, "connect", _ScriptedConnect())
    monkeypatch.setattr(server, "_auth_headers", recording_auth)
    monkeypatch.setattr(server, "_sleep_for_reconnect", interrupting_sleep)
    _stub_sample_rng(monkeypatch, 0.0)  # this test bypasses _stub_market_seams
    _set_credentials(monkeypatch)
    rc = main()
    captured = capsys.readouterr()
    assert rc == 0
    # AC2: recovery re-runs the startup path — a fresh signed handshake.
    assert len(auth_calls) == 2
    # AC4: attempt, failure (with the error message), and recovery lines.
    assert "connection attempt 1" in captured.out
    assert "connection attempt 1 failed:" in captured.out
    assert "server closed the connection" in captured.out
    assert "connection attempt 2" in captured.out
    assert "recovered on attempt 2" in captured.out
    # AC3: the backoff doubled between the two attempts.
    assert delays == [1.0, 2.0]
    # AC2: the second session re-subscribed every market exactly once.
    second_frames = [json.loads(frame) for frame in ws2.sent]
    assert [frame["cmd"] for frame in second_frames] == ["subscribe", "subscribe"]
    subscribed = [frame["params"]["market_tickers"][0] for frame in second_frames]
    assert sorted(subscribed) == markets
    # AC2: trade payloads print again after recovery.
    assert first_trade in captured.out
    assert second_trade in captured.out


# --- HIP-30: Supabase persistence + whale trade detection ---


def _whale_trade(**overrides) -> dict:
    """A yes-side trade with notional 500 x $2.50 = $1250 (a whale)."""
    trade = {
        "trade_id": "t-1",
        "market_ticker": "MKT-AAA",
        "taker_outcome_side": "yes",
        "taker_book_side": "yes",
        "count_fp": "500",
        "yes_price_dollars": "2.50",
        "no_price_dollars": "0.10",
        "is_block_trade": False,
        "ts_ms": 1732492800000,
    }
    trade.update(overrides)
    return trade


def _run_with_trades(
    monkeypatch: pytest.MonkeyPatch,
    trades: list,
    conn,
    market_ticker: str = "MKT-AAA",
) -> None:
    """Drive main() through one fake session replaying the given trades, with
    the Supabase connect seam returning the in-memory FakeConn."""
    frames = tuple(json.dumps({"type": "trade", "msg": trade}) for trade in trades)
    ws = _RecordingWs(incoming=frames)
    _stub_market_seams(monkeypatch, {"KXE-1": [market_ticker]}, ws)
    monkeypatch.setattr(server.persistence, "connect", lambda config: conn)
    _interrupt_first_reconnect(monkeypatch)


def test_whale_trade_is_printed_and_inserted_with_the_yes_side_price(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """AC: a >$1000 yes-side trade is printed and inserted; the stored price
    is the YES price ($2.50 x 500 = $1250 — the no price would give $50)."""
    conn = FakeConn()
    _run_with_trades(monkeypatch, [_whale_trade()], conn)
    rc = main()
    captured = capsys.readouterr()
    assert rc == 0  # fake ws closes -> supervised reconnect, not an exit
    assert "whale trade:" in captured.out
    assert len(conn.whale_trades) == 1
    row = conn.whale_trades[0]
    assert row["market_ticker"] == "MKT-AAA"
    assert row["taker_outcome_side"] == "yes"
    assert row["taker_book_side"] == "yes"
    assert row["count_fp"] == Decimal("500")
    assert row["price_dollars"] == Decimal("2.50")  # the YES price, not $0.10
    assert row["is_block_trade"] is False
    assert row["ts_ms"] == 1732492800000


def test_whale_trade_on_a_non_moneyline_market_is_printed_and_inserted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """AC: a >$1000 trade on a non-money-line (total) market is alerted and
    persisted — whale handling covers every subscribed market type."""
    conn = FakeConn()
    trade = _whale_trade(market_ticker="MKT-TOTAL-220.5")
    _run_with_trades(monkeypatch, [trade], conn, market_ticker="MKT-TOTAL-220.5")
    rc = main()
    captured = capsys.readouterr()
    assert rc == 0
    assert "whale trade:" in captured.out
    assert len(conn.whale_trades) == 1
    row = conn.whale_trades[0]
    assert row["market_ticker"] == "MKT-TOTAL-220.5"
    assert row["price_dollars"] == Decimal("2.50")
    assert row["count_fp"] == Decimal("500")


def test_whale_trade_is_inserted_with_the_no_side_price(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """AC: for a no-side taker the stored price is the NO price ($2.50 x 500
    = $1250 — the yes price would give $50 and no whale at all)."""
    conn = FakeConn()
    trade = _whale_trade(
        taker_outcome_side="no",
        taker_book_side="no",
        yes_price_dollars="0.10",
        no_price_dollars="2.50",
        is_block_trade=True,
    )
    _run_with_trades(monkeypatch, [trade], conn)
    rc = main()
    captured = capsys.readouterr()
    assert rc == 0
    assert "whale trade:" in captured.out
    assert len(conn.whale_trades) == 1
    assert conn.whale_trades[0]["price_dollars"] == Decimal("2.50")  # the NO price
    assert conn.whale_trades[0]["is_block_trade"] is True


@pytest.mark.parametrize(
    "yes_price",
    ["2.00", "1.99"],  # exactly $1000 and below: neither printed nor inserted
    ids=["exactly-1000", "below-1000"],
)
def test_trade_at_or_below_1000_is_neither_printed_nor_inserted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, yes_price: str
) -> None:
    """AC boundary: 500 contracts at exactly $2.00 is exactly $1000 — NOT a
    whale. The raw payload still prints (pre-existing behavior); only the
    whale line and the insert are suppressed."""
    conn = FakeConn()
    _run_with_trades(monkeypatch, [_whale_trade(yes_price_dollars=yes_price)], conn)
    rc = main()
    captured = capsys.readouterr()
    assert rc == 0
    assert "whale trade:" not in captured.out
    assert conn.whale_trades == []


def test_one_cent_over_1000_is_a_whale(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """AC boundary from above: 500 x $2.0002 = $1000.10 is strictly over."""
    conn = FakeConn()
    _run_with_trades(monkeypatch, [_whale_trade(yes_price_dollars="2.0002")], conn)
    rc = main()
    captured = capsys.readouterr()
    assert rc == 0
    assert "whale trade:" in captured.out
    assert len(conn.whale_trades) == 1


def test_trade_missing_whale_fields_is_logged_and_raw_payload_still_prints(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A trade without the whale-detection fields never crashes the loop:
    the raw payload prints and the skip is visible on stderr."""
    conn = FakeConn()
    legacy = {"trade_id": "t-1", "market_ticker": "MKT-AAA", "yes_price": 55, "count": 3}
    _run_with_trades(monkeypatch, [legacy], conn)
    rc = main()
    captured = capsys.readouterr()
    assert rc == 0
    assert json.dumps({"type": "trade", "msg": legacy}) in captured.out
    assert "trade skipped for whale detection" in captured.err
    assert "whale trade:" not in captured.out
    assert conn.whale_trades == []


def test_trade_missing_count_fp_is_logged_not_silently_skipped(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A trade with a valid side/price but no count_fp is the same class of
    malformed-input skip as an unparseable price — it must be visible on
    stderr, not swallowed."""
    conn = FakeConn()
    trade = _whale_trade()
    del trade["count_fp"]
    _run_with_trades(monkeypatch, [trade], conn)
    rc = main()
    captured = capsys.readouterr()
    assert rc == 0
    assert "trade skipped for whale detection: trade is missing count_fp" in captured.err
    assert "whale trade:" not in captured.out
    assert conn.whale_trades == []


def test_whale_insert_failure_is_logged_and_never_kills_the_connection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A Supabase failure on the whale insert is logged to stderr; the whale
    line still prints and the server keeps running (reconnects, rc 0)."""

    def failing_connect(config):
        raise RuntimeError("connection refused")

    frames = (json.dumps({"type": "trade", "msg": _whale_trade()}),)
    ws = _RecordingWs(incoming=frames)
    _stub_market_seams(monkeypatch, {"KXE-1": ["MKT-AAA"]}, ws)
    monkeypatch.setattr(server.persistence, "connect", failing_connect)
    _interrupt_first_reconnect(monkeypatch)
    rc = main()
    captured = capsys.readouterr()
    assert rc == 0
    assert "whale trade:" in captured.out
    assert "whale trade persistence failed" in captured.err


def test_persistence_failure_log_redacts_the_db_password(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """psycopg errors can echo connection details; if the exception text
    contains the configured SUPABASE_DB_PW it must be redacted before it
    reaches stderr."""
    password = "dummy-not-a-real-password"

    def failing_connect(config):
        raise RuntimeError(f"connection to db failed for user postgres:{password}@host")

    frames = (json.dumps({"type": "trade", "msg": _whale_trade()}),)
    ws = _RecordingWs(incoming=frames)
    _stub_market_seams(monkeypatch, {"KXE-1": ["MKT-AAA"]}, ws)
    monkeypatch.setattr(server.persistence, "connect", failing_connect)
    _interrupt_first_reconnect(monkeypatch)
    rc = main()
    captured = capsys.readouterr()
    assert rc == 0
    assert "whale trade persistence failed" in captured.err
    assert password not in captured.err
    assert "***" in captured.err


def test_successful_scan_upserts_events_then_markets_into_supabase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC: after a successful scan the discovered events and their markets
    land in Supabase; an event whose ticker encodes no teams is skipped with
    a warning instead of breaking the batch."""
    conn = FakeConn()
    record = EventRecord(
        event_ticker="KXNBAGAME-26NOV24BOSMIA",
        title="Boston Celtics at Miami Heat",
        url="https://kalshi.com/markets/kxnbagame/kxnbagame-26nov24bosmia",
        espn_start_ts="2026-11-25T02:00:00+00:00",
        away_team="BOS",
        home_team="MIA",
        markets=(
            MarketRecord("M-ML", "Will the Celtics win", "2026-11-25T02:00:00Z"),
            MarketRecord("M-SPREAD", "Celtics -5.5", "2026-11-25T02:30:00Z"),
        ),
    )
    teamless = EventRecord(
        event_ticker="KXNBAGAME-EV1",
        title="KXNBAGAME-EV1",
        url="https://kalshi.com/markets/kxnbagame/kxnbagame-ev1",
        espn_start_ts=None,
        away_team=None,
        home_team=None,
        markets=(MarketRecord("M-OTHER", "t", None),),
    )
    ws = _RecordingWs()
    monkeypatch.setattr(server, "discover_events", lambda *args: [record, teamless])
    monkeypatch.setattr(server.websockets, "connect", _FakeConnect(ws))
    monkeypatch.setattr(server, "_auth_headers", lambda *args: {})
    monkeypatch.setattr(server.persistence, "connect", lambda config: conn)
    _interrupt_first_reconnect(monkeypatch)
    _set_credentials(monkeypatch)
    rc = main()
    assert rc == 0
    assert len(conn.events) == 1  # only the event with team data persisted
    assert "KXNBAGAME-26NOV24BOSMIA" in conn.events
    assert len(conn.markets) == 2
    event_id = conn.events["KXNBAGAME-26NOV24BOSMIA"]["id"]
    assert {m["event_id"] for m in conn.markets.values()} == {event_id}
    assert conn.commits == 1
    # Both markets are still subscribed — persistence never gates ingestion.
    frames = [json.loads(frame) for frame in ws.sent]
    subscribed = sorted(f["params"]["market_tickers"][0] for f in frames if f["cmd"] == "subscribe")
    assert subscribed == ["M-ML", "M-OTHER", "M-SPREAD"]


def test_supabase_outage_does_not_kill_the_scan_or_subscriptions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A Supabase failure during the post-scan upsert is logged to stderr and
    the subscription flow continues unaffected."""
    record = EventRecord(
        event_ticker="KXNBAGAME-26NOV24BOSMIA",
        title="Boston Celtics at Miami Heat",
        url="https://kalshi.com/markets/kxnbagame/kxnbagame-26nov24bosmia",
        espn_start_ts=None,
        away_team="BOS",
        home_team="MIA",
        markets=(MarketRecord("M-ML", "Will the Celtics win", "2026-11-25T02:00:00Z"),),
    )

    def failing_connect(config):
        raise RuntimeError("connection refused")

    ws = _RecordingWs()
    monkeypatch.setattr(server, "discover_events", lambda *args: [record])
    monkeypatch.setattr(server.websockets, "connect", _FakeConnect(ws))
    monkeypatch.setattr(server, "_auth_headers", lambda *args: {})
    monkeypatch.setattr(server.persistence, "connect", failing_connect)
    _interrupt_first_reconnect(monkeypatch)
    _set_credentials(monkeypatch)
    rc = main()
    captured = capsys.readouterr()
    assert rc == 0
    assert "Supabase persistence failed" in captured.err
    frames = [json.loads(frame) for frame in ws.sent]
    subscribed = [f["params"]["market_tickers"][0] for f in frames if f["cmd"] == "subscribe"]
    assert subscribed == ["M-ML"]


# --- HIP-35: raw trade payload sampling ---


def test_non_sampled_trade_prints_no_raw_payload_but_whale_still_alerts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """AC1/AC3/AC4: with the sampling draw above RAW_TRADE_SAMPLE_RATE no raw
    trade payload prints — but the whale alert and its Supabase insert are
    unaffected: the >$1000 trade still prints and still inserts."""
    non_whale = {"trade_id": "t-small", "market_ticker": "MKT-AAA", "yes_price": 55, "count": 3}
    whale = _whale_trade()
    conn = FakeConn()
    _run_with_trades(monkeypatch, [non_whale, whale], conn)
    _stub_sample_rng(monkeypatch, 1.0)  # above the 0.05 threshold: never sampled
    rc = main()
    captured = capsys.readouterr()
    assert rc == 0
    assert json.dumps({"type": "trade", "msg": non_whale}) not in captured.out
    assert json.dumps({"type": "trade", "msg": whale}) not in captured.out
    assert "whale trade:" in captured.out  # whale alert fires regardless of sampling
    assert len(conn.whale_trades) == 1  # persistence unchanged by sampling


# --- HIP-34: per-league scans (NBA + NFL) ---


def _league_record(event_ticker: str, url: str, market_ticker: str, title: str) -> EventRecord:
    """A persistable EventRecord (teams encoded) for one league's scan."""
    return EventRecord(
        event_ticker=event_ticker,
        title=title,
        url=url,
        espn_start_ts=None,
        away_team="BOS",
        home_team="MIA",
        markets=(MarketRecord(market_ticker, f"Will the X win: {market_ticker}", None),),
    )


def test_scan_covers_both_leagues_and_combines_their_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC: every scan discovers NBA and NFL events; records from both leagues
    are persisted to Supabase and subscribed on the WebSocket."""
    conn = FakeConn()
    nba_record = _league_record(
        "KXNBAGAME-26NOV24BOSMIA",
        "https://kalshi.com/markets/kxnbagame/kxnbagame-26nov24bosmia",
        "M-NBA",
        "Boston Celtics at Miami Heat",
    )
    nfl_record = _league_record(
        "KXNFLGAME-26NOV24BUFMIA",
        "https://kalshi.com/markets/kxnflgame/kxnflgame-26nov24bufmia",
        "M-NFL",
        "Buffalo Bills at Miami Dolphins",
    )
    leagues_seen: list = []

    def fake_scan(rest_url: str, now, *credentials):
        league = credentials[-1]
        leagues_seen.append(league)
        return [nba_record] if league is NBA else [nfl_record]

    ws = _RecordingWs()
    monkeypatch.setattr(server, "discover_events", fake_scan)
    monkeypatch.setattr(server.websockets, "connect", _FakeConnect(ws))
    monkeypatch.setattr(server, "_auth_headers", lambda *args: {})
    monkeypatch.setattr(server.persistence, "connect", lambda config: conn)
    _interrupt_first_reconnect(monkeypatch)
    _set_credentials(monkeypatch)
    rc = main()
    assert rc == 0
    assert leagues_seen == [NBA, NFL]  # one startup scan per league
    assert set(conn.events) == {"KXNBAGAME-26NOV24BOSMIA", "KXNFLGAME-26NOV24BUFMIA"}
    assert conn.events["KXNFLGAME-26NOV24BUFMIA"]["url"] == (
        "https://kalshi.com/markets/kxnflgame/kxnflgame-26nov24bufmia"
    )
    frames = [json.loads(frame) for frame in ws.sent]
    subscribed = sorted(f["params"]["market_tickers"][0] for f in frames if f["cmd"] == "subscribe")
    assert subscribed == ["M-NBA", "M-NFL"]


def test_one_leagues_scan_failure_never_suppresses_the_other(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """AC: each league scans in its own try/except — a failing NBA scan is
    logged under its own label while the NFL league's records are still
    persisted and subscribed."""
    conn = FakeConn()
    nfl_record = _league_record(
        "KXNFLGAME-26NOV24BUFMIA",
        "https://kalshi.com/markets/kxnflgame/kxnflgame-26nov24bufmia",
        "M-NFL",
        "Buffalo Bills at Miami Dolphins",
    )

    def fake_scan(rest_url: str, now, *credentials):
        if credentials[-1] is NBA:
            raise RuntimeError("Kalshi REST request for series KXNBAGAME failed: HTTP 500")
        return [nfl_record]

    ws = _RecordingWs()
    monkeypatch.setattr(server, "discover_events", fake_scan)
    monkeypatch.setattr(server.websockets, "connect", _FakeConnect(ws))
    monkeypatch.setattr(server, "_auth_headers", lambda *args: {})
    monkeypatch.setattr(server.persistence, "connect", lambda config: conn)
    _interrupt_first_reconnect(monkeypatch)
    _set_credentials(monkeypatch)
    rc = main()
    captured = capsys.readouterr()
    assert rc == 0
    assert "NBA market scan failed" in captured.err
    assert "NFL market scan failed" not in captured.err
    assert "KXNFLGAME-26NOV24BUFMIA" in conn.events
    frames = [json.loads(frame) for frame in ws.sent]
    subscribed = [f["params"]["market_tickers"][0] for f in frames if f["cmd"] == "subscribe"]
    assert subscribed == ["M-NFL"]
