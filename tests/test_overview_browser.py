"""Optional real Firefox smoke test for the dependency-free sidebar component."""

import asyncio
import json
import shutil
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web


@pytest.mark.skipif(shutil.which("firefox") is None, reason="Firefox not installed")
async def test_overview_browser_controls_preview_and_safe_text(
    tmp_path, unused_tcp_port, aiohttp_server, socket_enabled
):
    """Exercise the browser DOM through Firefox's built-in WebDriver BiDi server."""
    source = (
        Path(__file__).parents[1]
        / "custom_components/modern_alerts/frontend/overview.js"
    )

    async def script(request):
        return web.Response(text=source.read_text(), content_type="text/javascript")

    async def index(request):
        return web.Response(
            text='<!doctype html><html><body><script src="/overview.js"></script></body></html>',
            content_type="text/html",
        )

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/overview.js", script)
    server = await aiohttp_server(app)
    profile = tmp_path / "profile"
    profile.mkdir()
    # Keep the test browser local and skip first-run network traffic.
    (profile / "user.js").write_text(
        'user_pref("browser.shell.checkDefaultBrowser", false);\nuser_pref("browser.startup.homepage_override.mstone", "ignore");\nuser_pref("datareporting.policy.dataSubmissionEnabled", false);\nuser_pref("toolkit.telemetry.enabled", false);\n'
    )
    browser_log = (tmp_path / "firefox.log").open("wb")
    process = await asyncio.create_subprocess_exec(
        "firefox",
        "--headless",
        "--no-remote",
        "--profile",
        str(profile),
        "--remote-debugging-port",
        str(unused_tcp_port),
        "about:blank",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=browser_log,
    )
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=2)
        ) as session:
            async with asyncio.timeout(10):
                while True:
                    try:
                        socket = await session.ws_connect(
                            f"http://127.0.0.1:{unused_tcp_port}/session",
                        )
                        break
                    except aiohttp.ClientError, TimeoutError:
                        await asyncio.sleep(0.1)
            async with socket:
                serial = 0

                async def command(method, params):
                    nonlocal serial
                    serial += 1
                    await socket.send_json(
                        {"id": serial, "method": method, "params": params}
                    )
                    async with asyncio.timeout(8):
                        while True:
                            response = await socket.receive_json()
                            if response.get("id") == serial:
                                assert response.get("type") == "success", response
                                return response["result"]

                await command("session.new", {"capabilities": {}})
                context = (await command("browsingContext.create", {"type": "tab"}))[
                    "context"
                ]
                await command(
                    "browsingContext.navigate",
                    {
                        "context": context,
                        "url": str(server.make_url("/")),
                        "wait": "complete",
                    },
                )

                async def evaluate(expression):
                    if "await " in expression or ";" in expression:
                        expression = "(async () => {" + expression + "})()"
                    result = await command(
                        "script.evaluate",
                        {
                            "expression": expression,
                            "target": {"context": context},
                            "awaitPromise": True,
                        },
                    )
                    assert result["type"] == "success", result
                    return result["result"].get("value")

                fixture = {
                    "entry_id": "test",
                    "name": '<img src=x onerror="window.injected=true">',
                    "loaded": True,
                    "active": True,
                    "acknowledged": False,
                    "can_acknowledge": True,
                    "can_control": True,
                    "enable_snooze": True,
                    "entity_id": "sensor.test_status",
                    "source_suspended": False,
                    "history": [],
                    "escalation_stage": "Initial",
                    "notification_blockers": ["quiet_hours"],
                    "destinations": ["notify.phone"],
                    "notification_errors": {},
                    "output_errors": {},
                    "policy_errors": {},
                    "outputs": [],
                }
                await evaluate(
                    """window.rows = """
                    + json.dumps([fixture])
                    + """;
                    window.calls = []; window.panel = document.createElement('modern-alerts-overview');
                    panel.hass = {user:{is_admin:true}, callWS:async request => request.type.endsWith('/overview') ? rows : {...rows[0], preview:{message:'<script>bad</script>'}}, callService:async (...args) => calls.push(args)};
                    document.body.append(panel); await panel.refresh(); true;"""
                )
                async with asyncio.timeout(5):
                    while not await evaluate(  # noqa: ASYNC110 - poll a remote browser
                        'panel.shadowRoot.querySelectorAll("article").length === 1'
                    ):  # noqa: ASYNC110 - poll a remote browser
                        await asyncio.sleep(0.05)
                assert (
                    await evaluate('panel.shadowRoot.querySelector("h2").textContent')
                    == fixture["name"]
                )
                assert (
                    await evaluate('panel.shadowRoot.querySelectorAll("img").length')
                    == 0
                )
                await evaluate(
                    'Array.from(panel.shadowRoot.querySelectorAll("button")).find(b => b.textContent === "Acknowledge").click(); await new Promise(r=>setTimeout(r,10)); true;'
                )
                assert await evaluate("JSON.stringify(calls[0])") == json.dumps(
                    [
                        "modern_alerts",
                        "acknowledge",
                        {"entity_id": "sensor.test_status"},
                    ],
                    separators=(",", ":"),
                )
                await evaluate("await panel.explain(rows[0]); true;")
                assert await evaluate('panel.shadowRoot.querySelector("dialog").open')
                assert (
                    await evaluate('panel.shadowRoot.querySelector("pre").textContent')
                    == "<script>bad</script>"
                )
                assert await evaluate("calls.length") == 1
                # Filtering works without sending actions or rerendering templates.
                await evaluate('panel._filter="idle"; panel.renderRows(); true;')
                assert (
                    await evaluate(
                        'panel.shadowRoot.querySelectorAll("article").length'
                    )
                    == 0
                )
                await evaluate("panel.remove(); true;")
                await command("session.end", {})
    finally:
        if process.returncode is None:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            process.kill()
            await process.wait()
        browser_log.close()
        print((tmp_path / "firefox.log").read_text())
