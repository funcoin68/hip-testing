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
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import kalshi_testing  # noqa: F401  (ensures the original module object is restorable)
from kalshi_testing import server
from kalshi_testing.server import DEFAULT_WS_URL, main

API_KEY_ID = "kalshi-test-key-id"
# Dummy PEM string, not a real key.
PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----\ndummy-not-a-real-key\n-----END PRIVATE KEY-----"


@pytest.fixture
def run_seam(monkeypatch: pytest.MonkeyPatch) -> list:
    """Replace the async _run seam with a recorder."""

    calls: list = []

    async def fake_run(config) -> None:
        calls.append(config)

    monkeypatch.setattr(server, "_run", fake_run)
    return calls


def _set_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KALSHI_API_KEY_ID", API_KEY_ID)
    monkeypatch.setenv("KALSHI_PRIVATE_KEY", PRIVATE_KEY)


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
    async def interrupted(config) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(server, "_run", interrupted)
    _set_credentials(monkeypatch)
    assert main() == 0  # Ctrl+C -> clean exit 0


def test_connection_failure_exits_nonzero_without_leaking_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    async def failing(config) -> None:
        raise ConnectionError("handshake refused")

    monkeypatch.setattr(server, "_run", failing)
    _set_credentials(monkeypatch)
    rc = main()
    captured = capsys.readouterr()
    assert rc != 0
    assert captured.err != ""
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
    """Exercise the real _run: 'connected' is logged, and a server-side close
    exits non-zero so it cannot be mistaken for a clean Ctrl+C shutdown."""

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
    monkeypatch.setattr(server, "discover_markets", lambda *args: {"KXE-1": ["MKT-TEST"]})
    _set_credentials(monkeypatch)
    rc = main()
    captured = capsys.readouterr()
    assert f"connected to {DEFAULT_WS_URL}" in captured.out
    assert rc == 2  # dropped connection != clean Ctrl+C exit 0
    assert "closed" in captured.err
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
)


def _clean_env() -> dict[str, str]:
    """os.environ without the Kalshi variables under test."""
    env = dict(os.environ)
    for var in ("KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY", "KALSHI_WS_URL"):
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


def test_direct_script_from_package_dir_loads_repo_root_dotenv() -> None:
    """AC1: `cd src/kalshi_testing && python server.py` with a repo-root .env
    gets past the credential guardrail (exit 2 = failed past config, not the
    missing-credential exit 1)."""
    with _repo_root_dotenv(_DOTENV_CREDENTIALS):
        result = subprocess.run(
            [sys.executable, "server.py"],
            cwd=_server_script().parent,
            env=_clean_env(),
            capture_output=True,
            text=True,
            timeout=60,
        )
    assert result.returncode == 2
    assert "KALSHI_API_KEY_ID" not in result.stderr


def test_direct_script_from_other_cwd_loads_repo_root_dotenv(tmp_path) -> None:
    """AC2 (PyCharm simulation): running server.py by absolute path with a
    non-repo-root working directory still loads the repo-root .env."""
    with _repo_root_dotenv(_DOTENV_CREDENTIALS):
        result = subprocess.run(
            [sys.executable, str(_server_script())],
            cwd=tmp_path,  # PyCharm debug working directory: not the repo root
            env=_clean_env(),
            capture_output=True,
            text=True,
            timeout=60,
        )
    assert result.returncode == 2
    assert "KALSHI_API_KEY_ID" not in result.stderr


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


def _stub_market_seams(monkeypatch: pytest.MonkeyPatch, desired: dict, ws: _RecordingWs) -> None:
    monkeypatch.setattr(server, "discover_markets", lambda *args: desired)
    monkeypatch.setattr(server.websockets, "connect", _FakeConnect(ws))
    monkeypatch.setattr(server, "_auth_headers", lambda *args: {})
    _set_credentials(monkeypatch)


def test_one_trade_subscription_frame_per_discovered_market(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC2: exactly one subscribe frame per discovered market, each
    on the trade channel for that single ticker — and nothing else is subscribed."""
    markets = ["MKT-AAA", "MKT-BBB", "MKT-CCC"]
    ws = _RecordingWs()
    _stub_market_seams(monkeypatch, {"KXNBAGAME-TEST": markets}, ws)
    rc = main()
    assert rc == 2  # fake ws closes -> ConnectionError path
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
    configured credentials as the WebSocket handshake."""
    scan_calls: list = []

    def recording_scan(rest_url: str, now, api_key_id: str, private_key_pem: str) -> dict:
        scan_calls.append((rest_url, api_key_id, private_key_pem))
        return {}

    ws = _RecordingWs()
    monkeypatch.setattr(server, "discover_markets", recording_scan)
    monkeypatch.setattr(server.websockets, "connect", _FakeConnect(ws))
    monkeypatch.setattr(server, "_auth_headers", lambda *args: {})
    _set_credentials(monkeypatch)
    rc = main()
    assert rc == 2  # fake ws closes -> ConnectionError path
    assert scan_calls == [(server.DEFAULT_REST_URL, API_KEY_ID, PRIVATE_KEY)]


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
        return {"KXNBAGAME-EV1": ["MKT-NEW"]}

    gate = asyncio.Event()
    ws = _RecordingWs(empty_gate=gate)
    waits = _gate_releasing_sleeper(monkeypatch, gate)
    monkeypatch.setattr(server, "discover_markets", flaky_scan)
    monkeypatch.setattr(server.websockets, "connect", _FakeConnect(ws))
    monkeypatch.setattr(server, "_auth_headers", lambda *args: {})
    _set_credentials(monkeypatch)
    rc = main()
    captured = capsys.readouterr()
    assert rc == 2  # fake ws closes -> ConnectionError path, not a crash
    assert "NBA market scan failed" in captured.err
    assert "KXNBAGAME" in captured.err
    assert waits, "the re-scan loop must still be scheduled after a startup scan failure"
    frames = [json.loads(frame) for frame in ws.sent]
    subscribed = [f["params"]["market_tickers"][0] for f in frames if f["cmd"] == "subscribe"]
    assert subscribed == ["MKT-NEW"]  # re-scan recovered and subscribed


def test_rescan_failure_is_logged_and_loop_continues(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """AC5: a failed re-scan is logged and the next scan still runs."""
    scans = iter(
        [
            {"KXNBAGAME-EV1": ["MKT-A"]},
            RuntimeError("HTTP 503"),
            {"KXNBAGAME-EV1": ["MKT-A", "MKT-B"]},
        ]
    )

    def fake_scan(rest_url: str, now, *_credentials):
        result = next(scans)
        if isinstance(result, Exception):
            raise result
        return result

    gate = asyncio.Event()
    confirmation = json.dumps({"type": "subscribed", "id": 1, "msg": {"sids": ["sid-a"]}})
    ws = _RecordingWs(incoming=(confirmation,), empty_gate=gate)
    waits = _gate_releasing_sleeper(monkeypatch, gate, rescans_before_stop=2)
    monkeypatch.setattr(server, "discover_markets", fake_scan)
    monkeypatch.setattr(server.websockets, "connect", _FakeConnect(ws))
    monkeypatch.setattr(server, "_auth_headers", lambda *args: {})
    _set_credentials(monkeypatch)
    rc = main()
    captured = capsys.readouterr()
    assert rc == 2
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
    logged, and the surviving market B is left untouched."""
    scans = iter([{"E": ["MKT-A", "MKT-B"]}, {"E": ["MKT-B", "MKT-C"]}])
    confirmations = (
        json.dumps({"type": "subscribed", "id": 1, "msg": {"sids": ["sid-a"]}}),
        json.dumps({"type": "subscribed", "id": 2, "msg": {"sids": ["sid-b"]}}),
    )
    gate = asyncio.Event()
    ws = _RecordingWs(incoming=confirmations, empty_gate=gate)
    _gate_releasing_sleeper(monkeypatch, gate)
    monkeypatch.setattr(server, "discover_markets", lambda *args: next(scans))
    monkeypatch.setattr(server.websockets, "connect", _FakeConnect(ws))
    monkeypatch.setattr(server, "_auth_headers", lambda *args: {})
    _set_credentials(monkeypatch)
    rc = main()
    captured = capsys.readouterr()
    assert rc == 2
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
    rc = main()
    captured = capsys.readouterr()
    assert rc == 2  # fake ws closes afterwards -> ConnectionError path
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
    rc = main()
    captured = capsys.readouterr()
    assert rc == 2
    assert raw_trade in captured.out
