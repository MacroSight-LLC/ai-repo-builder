import asyncio
import json
import uuid
from typing import Any, Dict, Optional

import websockets
from loguru import logger
from websockets.server import WebSocketServerProtocol


class ChromeExtensionWebSocketServer:
    """
    WebSocket server that handles communication with Chrome extension
    """

    def __init__(self, host: str = "localhost", port: int = 9223):
        self.host = host
        self.port = port
        self.server = None
        self.connected_clients: Dict[str, WebSocketServerProtocol] = {}
        self.pending_requests: Dict[str, asyncio.Future] = {}
        self.request_timeout = 30  # seconds

    async def start(self):
        """Start the WebSocket server"""
        try:
            # Create a wrapper function that properly handles the websocket handler signature
            async def handler(websocket, path):
                await self.handle_client(websocket)

            self.server = await websockets.serve(
                self.handle_client, self.host, self.port, ping_interval=20, ping_timeout=10
            )
            logger.info(f"WebSocket server started on ws://{self.host}:{self.port}")
        except Exception as e:
            logger.error(f"Failed to start WebSocket server: {e}")
            raise

    async def stop(self):
        """Stop the WebSocket server"""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            logger.info("WebSocket server stopped")

    async def handle_client(self, websocket: WebSocketServerProtocol):
        """Handle new client connection"""
        client_id = str(uuid.uuid4())
        self.connected_clients[client_id] = websocket
        logger.info(f"Chrome extension connected: {client_id}")

        try:
            async for message in websocket:
                logger.info(f"DEBUG: Raw message received from {client_id}, length: {len(message)}")
                try:
                    data = json.loads(message)
                    logger.info(f"DEBUG: Parsed JSON message type: {data.get('type')}")
                    await self.handle_message(client_id, websocket, data)
                except json.JSONDecodeError as e:
                    logger.error(f"DEBUG: Invalid JSON from client {client_id}: {e}")
                    logger.error(f"Invalid JSON from client {client_id}: {e}")
                    await self.send_error(websocket, "Invalid JSON format")
                except Exception as e:
                    logger.error(f"DEBUG: Error handling message from {client_id}: {e}")
                    logger.error(f"Error handling message from {client_id}: {e}")
                    await self.send_error(websocket, str(e))
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Chrome extension disconnected: {client_id}")
        except Exception as e:
            logger.error(f"Connection error with {client_id}: {e}")
        finally:
            if client_id in self.connected_clients:
                del self.connected_clients[client_id]

    async def handle_message(self, client_id: str, websocket: WebSocketServerProtocol, data: Dict[str, Any]):
        """Handle incoming message from Chrome extension"""
        message_type = data.get("type")
        request_id = data.get("request_id")

        logger.info(f"DEBUG: Received message from {client_id}: {message_type} (request_id: {request_id})")
        logger.debug(f"DEBUG: Full message data keys: {list(data.keys())}")

        if message_type == "ping":
            await self.send_message(
                websocket,
                {"type": "pong", "request_id": request_id, "timestamp": asyncio.get_event_loop().time()},
            )
        elif message_type == "extension_ready":
            await self.send_message(websocket, {"type": "server_ready", "request_id": request_id})
            logger.info(f"Extension {client_id} is ready")
        elif message_type == "page_extraction_complete":
            logger.info(f"DEBUG: Processing page_extraction_complete from {client_id}")
            # Handle page extraction data from Chrome extension
            await self.handle_page_extraction(client_id, data)
            logger.info(f"DEBUG: Sending extraction_received response to {client_id}")
            await self.send_message(
                websocket, {"type": "extraction_received", "request_id": request_id, "status": "success"}
            )
        elif message_type == "agent_query":
            logger.info(f"DEBUG: Processing agent_query from {client_id}")
            # Handle agent query from Chrome extension in a background task so
            # the main connection handler can continue receiving messages.
            # This prevents deadlocks where we send a request to the extension
            # (e.g. page extraction) and then block waiting for the response
            # while the receive loop is paused.
            asyncio.create_task(self.handle_agent_query(client_id, websocket, data))
            # No immediate response needed here; the spawned task will stream
            # data back to the extension.
            return  # Exit early to keep the handler responsive
        elif request_id and request_id in self.pending_requests:
            # This is a response to a request we sent
            future = self.pending_requests.pop(request_id)
            if not future.cancelled():
                future.set_result(data)
        else:
            logger.warning(f"Unknown message type or unmatched request: {message_type}")

    async def send_message(self, websocket: WebSocketServerProtocol, data: Dict[str, Any]):
        """Send message to a specific websocket"""
        try:
            message = json.dumps(data)
            await websocket.send(message)
        except Exception as e:
            logger.error(f"Failed to send message: {e}")

    async def send_error(self, websocket: WebSocketServerProtocol, error_message: str):
        """Send error message to websocket"""
        await self.send_message(websocket, {"type": "error", "message": error_message})

    async def broadcast_message(self, data: Dict[str, Any]):
        """Send message to all connected clients"""
        if not self.connected_clients:
            logger.warning("No connected clients to broadcast to")
            return

        message = json.dumps(data)
        disconnected = []

        for client_id, websocket in self.connected_clients.items():
            try:
                await websocket.send(message)
            except Exception as e:
                logger.error(f"Failed to send message to {client_id}: {e}")
                disconnected.append(client_id)

        # Clean up disconnected clients
        for client_id in disconnected:
            del self.connected_clients[client_id]

    async def send_request(self, data: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
        """Send request to Chrome extension and wait for response"""
        if not self.connected_clients:
            raise ConnectionError("No Chrome extension connected")

        # Use the first connected client (in future could support multiple)
        websocket = next(iter(self.connected_clients.values()))

        request_id = str(uuid.uuid4())
        data["request_id"] = request_id

        # Create future to wait for response
        future = asyncio.get_running_loop().create_future()
        self.pending_requests[request_id] = future

        try:
            # Send request
            await self.send_message(websocket, data)

            # Wait for response with timeout
            response = await asyncio.wait_for(future, timeout=timeout or self.request_timeout)
            return response

        except asyncio.TimeoutError:
            # Clean up on timeout
            if request_id in self.pending_requests:
                del self.pending_requests[request_id]
            raise TimeoutError(f"Request {request_id} timed out")
        except Exception as e:
            # Clean up on error
            if request_id in self.pending_requests:
                del self.pending_requests[request_id]
            raise

    def is_connected(self) -> bool:
        """Check if any Chrome extension is connected"""
        return len(self.connected_clients) > 0

    def get_connected_count(self) -> int:
        """Get number of connected Chrome extensions"""
        return len(self.connected_clients)

    async def handle_page_extraction(self, client_id: str, data: Dict[str, Any]):
        """Handle page extraction data received from Chrome extension"""
        try:
            logger.info(f"DEBUG: handle_page_extraction called for {client_id}")
            logger.info(f"DEBUG: Input data keys: {list(data.keys())}")
            logger.info(f"Received page extraction data from client {client_id}")

            # Log summary of received data
            extraction_data = data.get("data", {})
            summary = {
                "client_id": client_id,
                "timestamp": data.get("timestamp", "unknown"),
                "has_dom_snapshot": bool(extraction_data.get("dom_snapshot")),
                "has_accessibility_tree": bool(extraction_data.get("accessibility_tree")),
                "has_screenshot": bool(extraction_data.get("screenshot")),
                "has_focused_element_bid": bool(extraction_data.get("focused_element_bid")),
                "has_page_content": bool(extraction_data.get("page_content")),
            }

            if extraction_data.get("dom_snapshot"):
                dom_snapshot = extraction_data["dom_snapshot"]
                summary["dom_document_count"] = len(dom_snapshot.get("documents", []))

            if extraction_data.get("accessibility_tree"):
                ax_tree = extraction_data["accessibility_tree"]
                summary["accessibility_node_count"] = len(ax_tree.get("nodes", []))

            logger.info(f"Page extraction summary: {summary}")

            # Store the latest extraction data (could be used by other parts of the system)
            if not hasattr(self, 'latest_extraction_data'):
                self.latest_extraction_data = {}

            self.latest_extraction_data[client_id] = {
                "data": extraction_data,
                "timestamp": data.get("timestamp"),
                "summary": summary,
            }

            # Optional: Save to file for debugging (similar to server's save_extracted_data)
            await self._save_extraction_debug_data(client_id, extraction_data)

        except Exception as e:
            logger.error(f"Error handling page extraction from {client_id}: {str(e)}")

    async def _save_extraction_debug_data(self, client_id: str, extraction_data: Dict[str, Any]):
        """Save extraction data to debug files (optional)"""
        try:
            import json
            import base64
            from datetime import datetime
            from pathlib import Path

            # Create debug directory
            debug_dir = Path("debug_extractions_websocket")
            debug_dir.mkdir(exist_ok=True)

            # Create timestamp for file naming
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]

            # Create extraction-specific directory
            extraction_dir = debug_dir / f"websocket_{client_id}_{timestamp}"
            extraction_dir.mkdir(exist_ok=True)

            # Save each data type to separate files
            for key, value in extraction_data.items():
                if value is None:
                    continue

                if key == "screenshot" and isinstance(value, str):
                    # Save screenshot as PNG
                    try:
                        screenshot_data = base64.b64decode(value)
                        screenshot_file = extraction_dir / "screenshot.png"
                        with open(screenshot_file, 'wb') as f:
                            f.write(screenshot_data)
                    except Exception:
                        # If decode fails, save as text
                        screenshot_file = extraction_dir / "screenshot.txt"
                        with open(screenshot_file, 'w', encoding='utf-8') as f:
                            f.write(value[:1000] + "..." if len(value) > 1000 else value)
                elif key == "page_content" and isinstance(value, str):
                    # Save page content as text
                    content_file = extraction_dir / "page_content.txt"
                    with open(content_file, 'w', encoding='utf-8') as f:
                        f.write(value)
                else:
                    # Save other data as JSON
                    json_file = extraction_dir / f"{key}.json"
                    with open(json_file, 'w', encoding='utf-8') as f:
                        json.dump(value, f, indent=2, ensure_ascii=False)

            # Save summary
            summary_file = extraction_dir / "websocket_summary.json"
            summary_data = {
                "client_id": client_id,
                "timestamp": timestamp,
                "extraction_keys": list(extraction_data.keys()),
                "received_via": "websocket",
            }
            with open(summary_file, 'w', encoding='utf-8') as f:
                json.dump(summary_data, f, indent=2, ensure_ascii=False)

            logger.debug(f"WebSocket extraction data saved to: {extraction_dir}")

        except Exception as e:
            logger.debug(f"Failed to save WebSocket extraction debug data: {str(e)}")

    async def handle_agent_query(
        self, client_id: str, websocket: WebSocketServerProtocol, data: Dict[str, Any]
    ):
        """Handle agent query from Chrome extension"""
        try:
            query = data.get("query", "")
            logger.info(f"Received agent query from {client_id}: {query}")

            # Import here to avoid circular imports
            from server.main import event_stream

            # Start the agent stream
            logger.info(f"Starting agent stream for query: {query}")

            # Send initial response to indicate we're processing
            await self.send_message(
                websocket, {"type": "agent_response", "content": f"Processing query: {query}\n\n"}
            )

            # Stream the agent responses
            async for chunk in event_stream(query, api_mode=False):
                try:
                    # Parse the chunk if it's a JSON string
                    if chunk.strip():
                        # Remove "data: " prefix if present
                        if chunk.startswith("data: "):
                            chunk = chunk[6:]

                        # Try to parse as JSON
                        try:
                            chunk_data = json.loads(chunk)
                            content = chunk_data.get("data", chunk)
                        except json.JSONDecodeError:
                            content = chunk

                        # Send response chunk to Chrome extension
                        await self.send_message(websocket, {"type": "agent_response", "content": content})

                except Exception as e:
                    logger.error(f"Error processing agent response chunk: {str(e)}")
                    continue

            # Send completion message
            await self.send_message(websocket, {"type": "agent_complete"})

            logger.info(f"Agent query completed for {client_id}")

        except Exception as e:
            logger.error(f"Error handling agent query from {client_id}: {str(e)}")
            await self.send_message(websocket, {"type": "agent_error", "message": str(e)})

    def get_latest_extraction_data(self, client_id: str = None) -> Dict[str, Any]:
        """Get the latest extraction data from a client or any client"""
        if not hasattr(self, 'latest_extraction_data'):
            return {}

        if client_id:
            return self.latest_extraction_data.get(client_id, {})
        else:
            # Return the most recent extraction from any client
            if self.latest_extraction_data:
                latest_client = max(
                    self.latest_extraction_data.keys(),
                    key=lambda k: self.latest_extraction_data[k].get("timestamp", 0),
                )
                return self.latest_extraction_data[latest_client]
            return {}


class ChromeExtensionCommunicatorWebSocket:
    """
    WebSocket-based communicator for Chrome extension
    """

    def __init__(self, host: str = "localhost", port: int = 9223):
        self.host = host
        self.port = port
        self.server = ChromeExtensionWebSocketServer(host, port)

    async def __aenter__(self):
        await self.server.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.server.stop()

    async def wait_for_connection(self, timeout: float = 10.0):
        """Wait for Chrome extension to connect"""
        start_time = asyncio.get_event_loop().time()

        while not self.server.is_connected():
            if asyncio.get_event_loop().time() - start_time > timeout:
                raise TimeoutError("Chrome extension connection timeout")
            await asyncio.sleep(0.1)

        logger.info("Chrome extension connected successfully")

    async def send_extraction_request(self, request_type: str, data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Send extraction request to Chrome extension"""
        request = {"type": request_type, "data": data or {}}

        response = await self.server.send_request(request)

        if response.get("type") == "error":
            raise RuntimeError(f"Extension error: {response.get('message', 'Unknown error')}")

        return response

    async def ping(self) -> bool:
        """Ping the Chrome extension"""
        try:
            response = await self.server.send_request({"type": "ping"}, timeout=5.0)
            return response.get("type") == "pong"
        except Exception:
            return False

    async def extract_dom_snapshot(self, **kwargs) -> Dict[str, Any]:
        """Extract DOM snapshot"""
        response = await self.send_extraction_request("extract_dom_snapshot", kwargs)
        return response.get("data", {})

    async def extract_accessibility_tree(self) -> Dict[str, Any]:
        """Extract accessibility tree"""
        response = await self.send_extraction_request("extract_accessibility_tree")
        return response.get("data", {})

    async def extract_screenshot(self, format: str = "png", quality: int = 100) -> str:
        """Extract screenshot"""
        response = await self.send_extraction_request(
            "extract_screenshot", {"format": format, "quality": quality}
        )
        return response.get("data", "")

    async def extract_focused_element_bid(self) -> str:
        """Extract focused element BID"""
        response = await self.send_extraction_request("extract_focused_element_bid")
        return response.get("data", "")

    async def extract_page_content(self, as_text: bool = False) -> str:
        """Extract page content"""
        response = await self.send_extraction_request("extract_page_content", {"as_text": as_text})
        return response.get("data", "")

    async def get_active_tab_url(self) -> str:
        """Get the URL of the active browser tab"""
        response = await self.send_extraction_request("get_active_tab_url")
        return response.get("data", "")

    async def get_active_tab_title(self) -> str:
        """Get the title of the active browser tab"""
        response = await self.send_extraction_request("get_active_tab_title")
        return response.get("data", "")

    async def mark_elements(self, tags_to_mark: str = "standard_html") -> list:
        """Mark DOM elements"""
        response = await self.send_extraction_request("mark_elements", {"tags_to_mark": tags_to_mark})
        return response.get("warnings", [])

    async def unmark_elements(self):
        """Unmark DOM elements"""
        await self.send_extraction_request("unmark_elements")


# Example usage
async def main():
    """Example server usage"""
    async with ChromeExtensionCommunicatorWebSocket() as comm:
        print("WebSocket server started, waiting for Chrome extension...")

        try:
            # Wait for extension to connect
            await comm.wait_for_connection(timeout=30.0)

            # Test ping
            is_alive = await comm.ping()
            print(f"Extension ping: {is_alive}")

            if is_alive:
                # Mark elements
                warnings = await comm.mark_elements()
                print(f"Marking warnings: {warnings}")

                # Extract data
                screenshot = await comm.extract_screenshot()
                print(f"Screenshot length: {len(screenshot)}")

                # Clean up
                await comm.unmark_elements()

        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
