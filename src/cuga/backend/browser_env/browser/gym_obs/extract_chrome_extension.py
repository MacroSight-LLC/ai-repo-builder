# Copyright 2024 ServiceNow
# Modifications Copyright 2025 CUGA
# Licensed under the Apache License, Version 2.0

import asyncio
import logging
import re
from typing import Any, Dict, List, Literal

from loguru import logger

from cuga.backend.browser_env.browser.gym_obs.http_stream_comm import (
    ChromeExtensionCommunicatorHTTP,
    ChromeExtensionCommunicatorProtocol,
)

__BID_EXPR = r"([a-zA-Z0-9]+)"
__DATA_REGEXP = re.compile(r"^browsergym_id_" + __BID_EXPR + r"\s?" + r"(.*)")

# Constants that match the original implementation
BROWSERGYM_ID_ATTRIBUTE = "data-browsergym-id"
BROWSERGYM_SETOFMARKS_ATTRIBUTE = "data-browsergym-setofmarks"
BROWSERGYM_VISIBILITY_ATTRIBUTE = "data-browsergym-visibility"


class ChromeExtensionError(Exception):
    """Exception raised when Chrome extension communication fails"""

    pass


class MarkingError(Exception):
    """Exception raised when DOM marking fails"""

    pass


# Use the WebSocket server-based communicator
ChromeExtensionCommunicator = ChromeExtensionCommunicatorHTTP


class ChromeExtensionExtractor:
    """Chrome extension-based page information extractor"""

    def __init__(
        self, tags_to_mark: Literal["all", "standard_html"] = "standard_html", lenient: bool = False
    ):
        self.tags_to_mark = tags_to_mark
        self.lenient = lenient
        self.communicator = ChromeExtensionCommunicator()

    async def __aenter__(self):
        await self.communicator.__aenter__()
        # Wait for Chrome extension to connect
        await self.communicator.wait_for_connection(timeout=10.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.communicator.__aexit__(exc_type, exc_val, exc_tb)


async def _pre_extract_chrome_extension(
    communicator: ChromeExtensionCommunicatorProtocol,
    tags_to_mark: Literal["all", "standard_html"] = "standard_html",
    lenient: bool = False,
) -> None:
    """
    Pre-extraction routine that marks DOM elements via Chrome extension
    """
    try:
        warnings = await communicator.mark_elements(tags_to_mark=tags_to_mark)

        # Log any warning messages
        for msg in warnings:
            logger.warning(msg)

    except Exception as e:
        if not lenient:
            raise MarkingError(f"Pre-extraction failed: {str(e)}")
        else:
            logger.warning(f"Pre-extraction failed (lenient mode): {str(e)}")


async def _post_extract_chrome_extension(communicator: ChromeExtensionCommunicatorProtocol) -> None:
    """
    Post-extraction cleanup routine via Chrome extension
    """
    try:
        # await communicator.unmark_elements()
        logger.debug("Post-extraction cleanup completed successfully")

    except Exception as e:
        logger.warning(f"Post-extraction cleanup failed: {str(e)}")


async def extract_dom_snapshot_chrome_extension(
    communicator: ChromeExtensionCommunicatorProtocol,
    computed_styles: List[str] = None,
    include_dom_rects: bool = True,
    include_paint_order: bool = True,
    temp_data_cleanup: bool = True,
) -> Dict[str, Any]:
    """
    Extract DOM snapshot via Chrome extension
    """
    if computed_styles is None:
        computed_styles = []

    try:
        return await communicator.extract_dom_snapshot(
            computed_styles=computed_styles,
            include_dom_rects=include_dom_rects,
            include_paint_order=include_paint_order,
            temp_data_cleanup=temp_data_cleanup,
        )

    except Exception as e:
        raise ChromeExtensionError(f"Failed to extract DOM snapshot: {str(e)}")


async def extract_accessibility_tree_chrome_extension(
    communicator: ChromeExtensionCommunicatorProtocol,
) -> Dict[str, Any]:
    """
    Extract accessibility tree via Chrome extension
    """
    try:
        return await communicator.extract_accessibility_tree()

    except Exception as e:
        raise ChromeExtensionError(f"Failed to extract accessibility tree: {str(e)}")


async def extract_dom_tree_chrome_extension(
    communicator: ChromeExtensionCommunicatorProtocol,
    do_highlight_elements: bool = True,
    focus_highlight_index: int = -1,
    viewport_expansion: int = 0,
    debug_mode: bool = False,
) -> Dict[str, Any]:
    """
    Extract DOM tree with interactive element analysis via Chrome extension
    """
    try:
        return await communicator.extract_dom_tree(
            do_highlight_elements=do_highlight_elements,
            focus_highlight_index=focus_highlight_index,
            viewport_expansion=viewport_expansion,
            debug_mode=debug_mode,
        )

    except Exception as e:
        raise ChromeExtensionError(f"Failed to extract DOM tree: {str(e)}")


async def extract_screenshot_chrome_extension(
    communicator: ChromeExtensionCommunicatorProtocol, format: str = "png", quality: int = 100
) -> str:
    """
    Extract screenshot via Chrome extension
    """
    try:
        return await communicator.extract_screenshot(format=format, quality=quality)

    except Exception as e:
        raise ChromeExtensionError(f"Failed to extract screenshot: {str(e)}")


async def extract_focused_element_bid_chrome_extension(
    communicator: ChromeExtensionCommunicatorProtocol,
) -> str:
    """
    Extract focused element's browsergym ID via Chrome extension
    """
    try:
        return await communicator.extract_focused_element_bid()

    except Exception as e:
        logger.warning(f"Failed to extract focused element BID: {str(e)}")
        return ""


async def extract_page_content_chrome_extension(communicator: ChromeExtensionCommunicatorProtocol) -> str:
    """
    Extract page content as text via Chrome extension
    """
    try:
        return await communicator.extract_page_content(as_text=False)

    except Exception as e:
        raise ChromeExtensionError(f"Failed to extract page content: {str(e)}")


async def extract_page_url_chrome_extension(communicator: ChromeExtensionCommunicatorProtocol) -> str:
    """Get active tab URL via Chrome extension"""
    try:
        return await communicator.get_active_tab_url()
    except Exception as e:
        raise ChromeExtensionError(f"Failed to extract page URL: {str(e)}")


async def extract_page_title_chrome_extension(communicator: ChromeExtensionCommunicatorProtocol) -> str:
    """Get active tab title via Chrome extension"""
    try:
        return await communicator.get_active_tab_title()
    except Exception as e:
        raise ChromeExtensionError(f"Failed to extract page title: {str(e)}")


def extract_data_items_from_aria(string: str, log_level: int = logging.NOTSET) -> tuple[List[str], str]:
    """
    Utility function to extract temporary data stored in ARIA attributes
    """
    match = __DATA_REGEXP.fullmatch(string)
    if not match:
        logger.log(
            log_level,
            f"Failed to extract BrowserGym data from ARIA string: {repr(string)}",
        )
        return [], string

    groups = match.groups()
    data_items = groups[:-1]
    original_aria = groups[-1]
    return list(data_items), original_aria


def extract_dom_extra_properties_chrome_extension(dom_snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract extra properties from DOM snapshot (adapted from original implementation)
    """

    def to_string(idx):
        if idx == -1:
            return None
        else:
            return dom_snapshot["strings"][idx]

    # Pre-locate important string ids
    try:
        bid_string_id = dom_snapshot["strings"].index(BROWSERGYM_ID_ATTRIBUTE)
    except ValueError:
        bid_string_id = -1
    try:
        vis_string_id = dom_snapshot["strings"].index(BROWSERGYM_VISIBILITY_ATTRIBUTE)
    except ValueError:
        vis_string_id = -1
    try:
        som_string_id = dom_snapshot["strings"].index(BROWSERGYM_SETOFMARKS_ATTRIBUTE)
    except ValueError:
        som_string_id = -1

    # Build the iframe tree (simplified version)
    doc_properties = {0: {"parent": None, "abs_pos": {"x": 0, "y": 0}, "nodes": []}}

    # Process documents
    for doc_idx, document in enumerate(dom_snapshot.get("documents", [])):
        if doc_idx not in doc_properties:
            doc_properties[doc_idx] = {"parent": None, "abs_pos": {"x": 0, "y": 0}, "nodes": []}

        # Initialize node properties
        doc_properties[doc_idx]["nodes"] = [
            {
                "bid": None,
                "visibility": None,
                "bbox": None,
                "clickable": False,
                "set_of_marks": None,
            }
            for _ in range(len(document.get("nodes", {}).get("parentIndex", [])))
        ]

        # Extract clickable property
        clickable_indices = document.get("nodes", {}).get("isClickable", {}).get("index", [])
        for node_idx in clickable_indices:
            if node_idx < len(doc_properties[doc_idx]["nodes"]):
                doc_properties[doc_idx]["nodes"][node_idx]["clickable"] = True

        # Extract bid and visibility properties
        node_attributes = document.get("nodes", {}).get("attributes", [])
        for node_idx, node_attrs in enumerate(node_attributes):
            if node_idx >= len(doc_properties[doc_idx]["nodes"]):
                continue

            for i in range(0, len(node_attrs), 2):
                if i + 1 >= len(node_attrs):
                    break

                name_string_id = node_attrs[i]
                value_string_id = node_attrs[i + 1]

                if name_string_id == bid_string_id:
                    doc_properties[doc_idx]["nodes"][node_idx]["bid"] = to_string(value_string_id)
                elif name_string_id == vis_string_id:
                    vis_value = to_string(value_string_id)
                    if vis_value:
                        try:
                            doc_properties[doc_idx]["nodes"][node_idx]["visibility"] = float(vis_value)
                        except ValueError:
                            pass
                elif name_string_id == som_string_id:
                    doc_properties[doc_idx]["nodes"][node_idx]["set_of_marks"] = (
                        to_string(value_string_id) == "1"
                    )

        # Extract bbox property
        layout = document.get("layout", {})
        node_indices = layout.get("nodeIndex", [])
        bounds = layout.get("bounds", [])
        client_rects = layout.get("clientRects", [])

        for i, node_idx in enumerate(node_indices):
            if node_idx < len(doc_properties[doc_idx]["nodes"]) and i < len(bounds) and i < len(client_rects):
                # Empty clientRect means element is not actually rendered
                if not client_rects[i]:
                    doc_properties[doc_idx]["nodes"][node_idx]["bbox"] = None
                else:
                    # Copy bounds and adjust for absolute document position
                    bbox = bounds[i].copy() if bounds[i] else [0, 0, 0, 0]
                    bbox[0] += doc_properties[doc_idx]["abs_pos"]["x"]
                    bbox[1] += doc_properties[doc_idx]["abs_pos"]["y"]
                    doc_properties[doc_idx]["nodes"][node_idx]["bbox"] = bbox

    # Collect extra properties of all nodes with browsergym_id attribute
    # BID ids seems correct so far
    extra_properties = {}
    for doc in doc_properties.values():
        for node in doc["nodes"]:
            bid = node["bid"]
            if bid:
                if bid in extra_properties:
                    logger.warning(f"Duplicate {BROWSERGYM_ID_ATTRIBUTE}={repr(bid)} attribute detected")
                extra_properties[bid] = {
                    "visibility": node["visibility"],
                    "bbox": node["bbox"],
                    "clickable": node["clickable"],
                    "set_of_marks": node["set_of_marks"],
                }

    return extra_properties


def add_browsergym_id_to_accessibility_tree(
    accessibility_tree: Dict[str, Any], dom_snapshot: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Post-process the accessibility tree to add browsergym_id field to nodes
    based on correlation with DOM snapshot
    """
    if not accessibility_tree or not dom_snapshot:
        return accessibility_tree

    # Build a mapping from backendNodeId to browsergym_id from DOM snapshot
    backend_node_id_to_bid = {}

    def to_string(idx):
        if idx == -1:
            return None
        else:
            return dom_snapshot["strings"][idx]

    # Pre-locate the bid string ID
    try:
        bid_string_id = dom_snapshot["strings"].index(BROWSERGYM_ID_ATTRIBUTE)
    except ValueError:
        bid_string_id = -1

    if bid_string_id == -1:
        logger.warning("No browsergym_id attributes found in DOM snapshot")
        return accessibility_tree

    # Extract backend node IDs and browsergym_ids from DOM snapshot
    for document in dom_snapshot.get("documents", []):
        backend_node_ids = document.get("nodes", {}).get("backendNodeId", [])
        node_attributes = document.get("nodes", {}).get("attributes", [])

        for node_idx, node_attrs in enumerate(node_attributes):
            if node_idx >= len(backend_node_ids):
                continue

            backend_node_id = backend_node_ids[node_idx]
            browsergym_id = None

            # Look for browsergym_id in this node's attributes
            for i in range(0, len(node_attrs), 2):
                if i + 1 >= len(node_attrs):
                    break

                name_string_id = node_attrs[i]
                value_string_id = node_attrs[i + 1]

                if name_string_id == bid_string_id:
                    browsergym_id = to_string(value_string_id)
                    break

            if browsergym_id and backend_node_id:
                backend_node_id_to_bid[backend_node_id] = browsergym_id

    # Add browsergym_id to accessibility tree nodes
    processed_tree = accessibility_tree.copy()
    if "nodes" in processed_tree:
        for node in processed_tree["nodes"]:
            if "backendDOMNodeId" in node:
                backend_node_id = node["backendDOMNodeId"]
                if backend_node_id in backend_node_id_to_bid:
                    node["browsergym_id"] = backend_node_id_to_bid[backend_node_id]

    logger.info(
        f"Added browsergym_id to {len([n for n in processed_tree.get('nodes', []) if 'browsergym_id' in n])} accessibility tree nodes"
    )

    return processed_tree


async def full_extract_chrome_extension(
    communicator: ChromeExtensionCommunicatorProtocol,
    tags_to_mark: Literal["all", "standard_html"] = "standard_html",
    lenient: bool = False,
    max_retries: int = 3,
) -> Dict[str, Any]:
    """
    Full extraction routine using Chrome extension
    """
    dom_snapshot = None
    accessibility_tree = None
    dom_tree = None
    extra_properties = None
    focused_element_bid = None
    screenshot = None
    page_content = None
    page_url = None

    for retry in range(max_retries):
        try:
            # Pre-extraction: mark DOM elements
            await _pre_extract_chrome_extension(
                communicator, tags_to_mark, lenient=(retry == max_retries - 1)
            )

            # Extract all data in parallel for better performance
            tasks = [
                extract_dom_snapshot_chrome_extension(communicator),
                extract_accessibility_tree_chrome_extension(communicator),
                extract_dom_tree_chrome_extension(communicator, do_highlight_elements=True, debug_mode=True),
                extract_focused_element_bid_chrome_extension(communicator),
                extract_screenshot_chrome_extension(communicator),
                extract_page_content_chrome_extension(communicator),
                extract_page_url_chrome_extension(communicator),
                extract_page_title_chrome_extension(communicator),
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results
            dom_snapshot = results[0] if not isinstance(results[0], Exception) else {}
            accessibility_tree = results[1] if not isinstance(results[1], Exception) else {}
            dom_tree = results[2] if not isinstance(results[2], Exception) else {}
            focused_element_bid = results[3] if not isinstance(results[3], Exception) else ""
            screenshot = results[4] if not isinstance(results[4], Exception) else ""
            page_content = results[5] if not isinstance(results[5], Exception) else ""
            page_url = results[6] if not isinstance(results[6], Exception) else ""
            page_title = results[7] if not isinstance(results[7], Exception) else ""

            # Log any exceptions
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.warning(f"Task {i} failed: {result}")

            # Post-process accessibility tree to add browsergym_id field
            if accessibility_tree and dom_snapshot:
                accessibility_tree = add_browsergym_id_to_accessibility_tree(accessibility_tree, dom_snapshot)

            # Process extra properties from DOM snapshot
            if dom_snapshot:
                extra_properties = extract_dom_extra_properties_chrome_extension(dom_snapshot)
            else:
                extra_properties = {}

            break

        except (ChromeExtensionError, MarkingError) as e:
            err_msg = str(e)
            if retry < max_retries - 1 and any(
                msg in err_msg
                for msg in [
                    "Frame was detached",
                    "Frame with the given frameId is not found",
                    "Execution context was destroyed",
                    "Frame has been detached",
                    "Chrome extension connection timeout",
                ]
            ):
                logger.warning(f"Extraction failed, retrying ({retry + 1}/{max_retries}): {err_msg}")
                await _post_extract_chrome_extension(communicator)
                await asyncio.sleep(0.5)
                continue
            else:
                raise

        finally:
            # Always cleanup
            try:
                await _post_extract_chrome_extension(communicator)
            except Exception as cleanup_error:
                logger.warning(f"Cleanup failed: {cleanup_error}")

    return {
        "dom_snapshot": dom_snapshot,
        "accessibility_tree": accessibility_tree,
        "dom_tree": dom_tree,
        "extra_properties": extra_properties,
        "focused_element_bid": focused_element_bid,
        "screenshot": screenshot,
        "page_content": page_content,
        "page_url": page_url,
        "page_title": page_title,
    }
