import asyncio
import json
import logging
import os
import time
import subprocess
import sys
from uuid import uuid4

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False

"""
MCP DAZ Server — v1.3 (emergency protocol fix)
- JSON-RPC 2.0 over WebSocket
- Implements MCP control plane:
  • initialize (FIXED)
  • notifications/initialized (ADDED)
  • tools/list
  • tools/call
  • resources/list (empty for now)
  • prompts/list (empty for now)
- Tools exposed (snake_case to match Claude Desktop):
  • load_scene(scene_path)
  • set_pose(pose_path, figure_name[optional])
  • render_scene(output_path, width[opt], height[opt])
- Backwards-compat: still accepts direct methods loadScene/setPose/render.

Config via ENV:
  HOST=127.0.0.1
  PORT=8765
  DAZ_EXE=C:\\Program Files\\DAZ 3D\\DAZStudio4\\dazstudio.exe
  DAZ_SCRIPT_PATH=C:\\Path\\To\\DazStudio\\Scripts
  LOG_LEVEL=INFO  (DEBUG/INFO/WARNING/ERROR)
"""

# === Config ===
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8765"))
DAZ_EXE = os.getenv("DAZ_EXE", r"C:\\Program Files\\DAZ 3D\\DAZStudio4\\dazstudio.exe")
DAZ_SCRIPT_PATH = os.getenv("DAZ_SCRIPT_PATH", r"C:\knosso\Daz\scripts")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# === Logging ===
# Configure logging to stderr to avoid interfering with stdio JSON-RPC communication
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    stream=sys.stderr,  # Use stderr instead of stdout for stdio mode
)
log = logging.getLogger("mcp-daz")

# === Tool Registry (for tools/list) ===
TOOLS = [
    {
        "name": "load_scene",
        "description": "Load a DAZ Studio scene file",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scene_path": {"type": "string", "description": "Path to the scene file to load"}
            },
            "required": ["scene_path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "set_pose",
        "description": "Set a pose for the selected figure",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pose_path": {"type": "string", "description": "Path to the pose file"},
                "figure_name": {"type": "string", "description": "Name of the figure to apply pose to"},
            },
            "required": ["pose_path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "render_scene",
        "description": "Render the current scene",
        "inputSchema": {
            "type": "object",
            "properties": {
                "output_path": {"type": "string", "description": "Path where to save the rendered image"},
                "width": {"type": "integer", "description": "Render width in pixels"},
                "height": {"type": "integer", "description": "Render height in pixels"},
            },
            "required": ["output_path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_scene",
        "description": "Get list of items in the current DAZ scene",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_content",
        "description": "List available items in the DAZ Studio content library",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

# === Config ===
CALL_TIMEOUT = int(os.getenv("CALL_TIMEOUT", "60"))  # seconds; adjust as needed

# === Helpers ===
def _script_path(name: str):
    return os.path.join(DAZ_SCRIPT_PATH, name)


def run_daz_script(script_name: str, *args):
    """
    Launches DAZ script and returns payload with (rc, stdout, stderr, duration_ms).
    Enforces CALL_TIMEOUT so the JSON-RPC call never hangs Claude.
    """
    script = _script_path(script_name)
    start = time.time()

    cmd = [
        DAZ_EXE,
        "-noPrompt",
        "-script", script,
    ]
    for a in args:
        cmd += ["-scriptArg", str(a)]

    # Windows: hide console window
    creationflags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW

    log.debug("Launching DAZ script: %s", {"script": script, "args": list(args), "cmd": cmd})

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags
        )
        out, err = proc.communicate(timeout=CALL_TIMEOUT)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        # Ensure process doesn't linger; best effort kill:
        try:
            proc.kill()
        except Exception:
            pass
        rc, out, err = 124, b"", b"timeout: DAZ script exceeded CALL_TIMEOUT"
    except Exception as e:
        rc, out, err = 1, b"", str(e).encode("utf-8")

    duration_ms = int((time.time() - start) * 1000)
    
    # Handle timeout case with specific JSON-RPC format
    if rc == 124:
        payload = {
            "status": "error",
            "reason": "timeout",
            "timeout_s": CALL_TIMEOUT,
            "stdout": out.decode("utf-8", "ignore").strip(),
            "stderr": err.decode("utf-8", "ignore").strip(),
            "duration_ms": duration_ms,
        }
    else:
        payload = {
            "status": "ok" if rc == 0 else "error",
            "returncode": rc,
            "stdout": out.decode("utf-8", "ignore").strip(),
            "stderr": err.decode("utf-8", "ignore").strip(),
            "duration_ms": duration_ms,
        }
    
    level = logging.INFO if rc == 0 else logging.ERROR
    log.log(level, "DAZ script finished: %s", {"script": script_name, "rc": rc, "duration_ms": duration_ms})
    return payload


# === JSON-RPC helpers ===
def rpc_result(req_id, result):
    return json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result})


def rpc_error(req_id, code, message):
    return json.dumps({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def rpc_notification(method, params=None):
    msg = {"jsonrpc": "2.0", "method": method}
    if params:
        msg["params"] = params
    return json.dumps(msg)


# === MCP method handlers ===
def handle_initialize(req_id, params):
    """Handle MCP initialize handshake"""
    server_info = {
        "name": "daz-studio-mcp",
        "version": "1.3.0"
    }
    capabilities = {
        "tools": {},
        "resources": {},
        "prompts": {}
    }
    
    protocol_version = params.get("protocolVersion", "2025-06-18")
    result = {
        "protocolVersion": protocol_version,
        "capabilities": capabilities,
        "serverInfo": server_info
    }
    return rpc_result(req_id, result)


def handle_tools_list(req_id):
    return rpc_result(req_id, {"tools": TOOLS})


def handle_tools_call(req_id, params):
    name = (params or {}).get("name")
    args = (params or {}).get("arguments") or {}

    if name == "load_scene":
        scene_path = args.get("scene_path", "")
        result = run_daz_script("load_scene.dsa", scene_path)
    elif name == "set_pose":
        pose_path = args.get("pose_path", "")
        figure_name = args.get("figure_name", "")
        result = run_daz_script("set_pose.dsa", pose_path, figure_name)
    elif name == "render_scene":
        output_path = args.get("output_path", "")
        width = args.get("width", 800)
        height = args.get("height", 600)
        result = run_daz_script("render_scene.dsa", output_path, width, height)
    elif name == "read_scene":
        result = run_daz_script("read_scene.dsa")
    elif name == "list_content":
        result = run_daz_script("list_content.dsa")
    else:
        result = {"status": "error", "stderr": f"Unknown tool: {name}"}
        
    # Normalize output
    if result.get("status") == "ok":
        payload = {"content": [{"type": "text", "text": json.dumps(result)}]}
    else:
        payload = {"error": {"message": result.get("stderr", "Unknown error"), "code": result.get("returncode", -1)}}

    return rpc_result(req_id, payload)


async def handle_mcp_request(websocket, path):
    log.info("Client connected.")
    try:
        async for message in websocket:
            log.debug("Received: %s", message)
            try:
                req = json.loads(message)
                req_id = req.get("id")
                method = req.get("method")
                params = req.get("params")

                if method == "initialize":
                    response = handle_initialize(req_id, params or {})
                elif method == "tools/list":
                    response = handle_tools_list(req_id)
                elif method == "tools/call":
                    response = handle_tools_call(req_id, params)
                elif method == "resources/list":
                    response = rpc_result(req_id, {"resources": []})
                elif method == "prompts/list":
                    response = rpc_result(req_id, {"prompts": []})
                else:
                    response = rpc_error(req_id, -32601, f"Method not found: {method}")

                log.debug("Sending: %s", response)
                await websocket.send(response)
            except json.JSONDecodeError:
                log.error("Invalid JSON received: %s", message)
                await websocket.send(rpc_error(None, -32700, "Parse error"))
            except Exception as e:
                log.exception("Error handling request.")
                await websocket.send(rpc_error(req_id, -32000, f"Server error: {e}"))
    except websockets.exceptions.ConnectionClosedOK:
        log.info("Client disconnected normally.")
    except Exception as e:
        log.exception("WebSocket error.")


async def main():
    if not WEBSOCKETS_AVAILABLE:
        log.error("websockets library not found. Please install it: pip install websockets")
        sys.exit(1)

    log.info(f"Starting MCP DAZ server on ws://{HOST}:{PORT}")
    async with websockets.serve(handle_mcp_request, HOST, PORT):
        await asyncio.Future()  # Run forever


if __name__ == '__main__':
    if '--stdio' in sys.argv:
        log.info("MCP DAZ server started in stdio mode.")
        log.info(f"DAZ_SCRIPT_PATH: {DAZ_SCRIPT_PATH}")
        log.info(f"DAZ_EXE: {DAZ_EXE}")
        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                try:
                    req = json.loads(line.strip())
                    req_id = req.get("id")
                    method = req.get("method")
                    params = req.get("params")

                    if method == "initialize":
                        response = handle_initialize(req_id, params or {})
                    elif method == "tools/list":
                        response = handle_tools_list(req_id)
                    elif method == "tools/call":
                        response = handle_tools_call(req_id, params)
                    elif method == "resources/list":
                        response = rpc_result(req_id, {"resources": []})
                    elif method == "prompts/list":
                        response = rpc_result(req_id, {"prompts": []})
                    else:
                        response = rpc_error(req_id, -32601, f"Method not found: {method}")

                    sys.stdout.write(response + '\n')
                    sys.stdout.flush()
                except json.JSONDecodeError:
                    log.error("Invalid JSON received: %s", line.strip())
                    sys.stdout.write(rpc_error(None, -32700, "Parse error") + '\n')
                    sys.stdout.flush()
                except Exception as e:
                    log.exception("Error handling request in stdio mode.")
                    sys.stdout.write(rpc_error(req_id, -32000, f"Server error: {e}") + '\n')
                    sys.stdout.flush()
            except Exception as e:
                log.exception("Error in stdio loop: %s", e)
    else:
        asyncio.run(main())
